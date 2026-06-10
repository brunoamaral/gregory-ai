import hashlib
import json
import logging
import re
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from gregory.models import (
	Articles,
	Trials,
	TeamCategory,
	ArticleCategoryAssignment,
	TrialCategoryAssignment,
	CategoryAssignmentSource,
	CategoryType,
)
from datetime import timedelta

# Configure logging
logger = logging.getLogger(__name__)

class Command(BaseCommand):
	help = (
		'Rebuilds category associations for articles and trials by diffing the desired state against the '
		'current state: matching items are added and stale items removed, without ever clearing the tables. '
		'For trials, searches across multiple fields including title, summary, intervention, outcomes, '
		'scientific title, and therapeutic areas.'
	)

	def add_arguments(self, parser):
		parser.add_argument('--days', type=int, help='Only process content from the last N days')
		parser.add_argument('--category', type=int, help='Only process the automatic category with this ID (e.g. to backfill a newly created category)')
		parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for processing')
		parser.add_argument('--min-score', type=int, default=3, help='Minimum score to categorize content')
		parser.add_argument('--articles-only', action='store_true', help='Only rebuild article categories')
		parser.add_argument('--trials-only', action='store_true', help='Only rebuild trial categories')
		parser.add_argument('--dry-run', action='store_true', help='Run without making changes, just report what would happen')
		parser.add_argument('--verbose', action='store_true', help='Show detailed progress information')

	# Default scope: all automatic categories (overridden by --category)
	category_id = None

	def handle(self, *args, **options):
		self.dry_run = options.get('dry_run', False)
		self.verbose = options.get('verbose', False)
		self.category_id = options.get('category')
		days = options.get('days')
		batch_size = options.get('batch_size')
		min_score = options.get('min_score')

		if self.category_id is not None:
			try:
				target = TeamCategory.objects.get(pk=self.category_id)
			except TeamCategory.DoesNotExist:
				raise CommandError(f'TeamCategory {self.category_id} does not exist.')
			if target.category_type != CategoryType.AUTOMATIC:
				self.stdout.write(self.style.WARNING(
					f"Category '{target.category_name}' is manual; nothing to do."
				))
				return

		if self.dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN MODE: No changes will be made to the database"))

		if not options.get('trials_only'):
			self.rebuild_cats_articles(days, batch_size, min_score)

		if not options.get('articles_only'):
			self.rebuild_cats_trials(days, batch_size, min_score)

		if not self.dry_run and not options.get('articles_only') and not options.get('trials_only'):
			self.finalize_sync_state(min_score)

		self.stdout.write(self.style.SUCCESS('Successfully rebuilt category associations.'))

	def log_message(self, message, level=1):
		"""Log a message if verbosity is high enough"""
		if self.verbose or level == 0:
			self.stdout.write(message)
			logger.info(message)

	def iter_batches(self, queryset, pk_field, batch_size):
		"""Yield batches via keyset pagination so the scan is stable and avoids OFFSET costs."""
		last_pk = None
		while True:
			batch_qs = queryset.order_by(pk_field)
			if last_pk is not None:
				batch_qs = batch_qs.filter(**{f'{pk_field}__gt': last_pk})
			batch = list(batch_qs[:batch_size])
			if not batch:
				return
			last_pk = getattr(batch[-1], pk_field)
			yield batch

	def target_categories(self):
		qs = TeamCategory.objects.filter(category_type=CategoryType.AUTOMATIC)
		if self.category_id is not None:
			qs = qs.filter(pk=self.category_id)
		return qs.prefetch_related('subjects')

	def category_config_hash(self, cat, min_score):
		"""Fingerprint of everything that affects which items match this category."""
		payload = json.dumps({
			'terms': sorted(term.lower() for term in cat.category_terms or []),
			# Iterate the (prefetched) relation instead of values_list, which
			# would bypass prefetch_related and re-query per category
			'subjects': sorted(subject.id for subject in cat.subjects.all()),
			'min_score': min_score,
		}, sort_keys=True)
		return hashlib.sha256(payload.encode()).hexdigest()

	def category_cutoff(self, cat, cutoff_date, config_hash):
		"""Return the cutoff to use for this category in incremental mode.

		If the matching configuration changed since the last sync (or the
		category was never synced), the incremental window would miss older
		items that now match (or no longer match), so fall back to a full
		re-match for this category only.
		"""
		if cutoff_date and cat.match_config_hash != config_hash:
			self.stdout.write(f"  Matching configuration changed for '{cat.category_name}'; performing a full re-match")
			return None
		return cutoff_date

	def finalize_sync_state(self, min_score):
		"""Record the matching configuration each automatic category was synced with.

		Only called after a complete run (articles and trials): storing the hash
		after a partial run would make the other side skip the full re-match it
		still needs.
		"""
		now = timezone.now()
		for cat in self.target_categories():
			cat.match_config_hash = self.category_config_hash(cat, min_score)
			cat.last_synced_at = now
			cat.save(update_fields=['match_config_hash', 'last_synced_at'])

	def sync_category(self, manager, desired_ids, automatic_ids, manual_ids):
		"""Diff desired vs current automatic associations and apply only the changes.

		Manual assignments are never touched: they are not removed when stale,
		and a desired item that is already manually assigned is left manual.
		"""
		to_add = desired_ids - automatic_ids - manual_ids
		to_remove = automatic_ids - desired_ids
		if not self.dry_run:
			if to_add:
				manager.add(*to_add, through_defaults={'source': CategoryAssignmentSource.AUTOMATIC})
			if to_remove:
				manager.remove(*to_remove)
		return len(to_add), len(to_remove)

	def rebuild_cats_articles(self, days=None, batch_size=1000, min_score=3):
		self.stdout.write("Processing articles categorization...")

		# Define date cutoff for incremental updates
		cutoff_date = None
		if days:
			cutoff_date = timezone.now() - timedelta(days=days)
			self.stdout.write(f"Processing articles updated since {cutoff_date}")

		# Manual categories are curated entirely by hand and never touched here
		categories = self.target_categories()
		if self.category_id is None:
			manual_categories = TeamCategory.objects.exclude(category_type=CategoryType.AUTOMATIC).count()
			if manual_categories:
				self.stdout.write(f"Skipping {manual_categories} manual categories")
		total_categories = categories.count()
		total_added = 0
		total_removed = 0

		for index, cat in enumerate(categories, 1):
			terms = cat.category_terms

			self.stdout.write(f"[{index}/{total_categories}] Processing category: {cat.category_name}")

			config_hash = self.category_config_hash(cat, min_score)
			cat_cutoff = self.category_cutoff(cat, cutoff_date, config_hash)

			# Prepare term patterns for more accurate matching
			term_patterns = [re.compile(r'\b' + re.escape(term.lower()) + r'\b') for term in terms]

			# Desired set of article ids for this category; empty terms mean no articles qualify
			desired_ids = set()

			if not terms:
				self.log_message(f"  Category '{cat.category_name}' has no terms; stale automatic associations will be removed")

			for subject in cat.subjects.all() if terms else []:
				self.log_message(f"  - Processing subject: {subject.subject_name}")

				matched_for_subject = 0

				# Base query for this subject
				base_query = Articles.objects.filter(subjects__id=subject.id)

				# Apply date filter if incremental
				if cat_cutoff:
					base_query = base_query.filter(
						Q(discovery_date__gte=cat_cutoff) |
						Q(last_updated__gte=cat_cutoff)
					)

				# Initial database filtering (broad match)
				query = Q()
				for term in terms:
					upper_term = term.upper()
					query |= Q(utitle__contains=upper_term) | Q(usummary__contains=upper_term)

				candidates = base_query.filter(query)
				total_candidates = candidates.count()
				self.log_message(f"    Found {total_candidates} candidate articles")

				processed = 0

				for batch in self.iter_batches(candidates, 'article_id', batch_size):
					processed += len(batch)

					# Score-based categorization for this batch
					articles_with_scores = []

					for article in batch:
						score = 0
						matched_terms = set()
						title = article.title.lower()
						summary = (article.summary or "").lower()

						# Check for whole-word matches in title (higher weight)
						for i, pattern in enumerate(term_patterns):
							if pattern.search(title):
								score += 3
								matched_terms.add(terms[i])

						# Check for whole-word matches in summary
						for i, pattern in enumerate(term_patterns):
							if pattern.search(summary):
								score += 1
								matched_terms.add(terms[i])

						# Bonus for multiple term matches
						score += len(matched_terms) * 2

						# Add if score meets threshold
						if score >= min_score:
							desired_ids.add(article.article_id)
							matched_for_subject += 1
							articles_with_scores.append((article.article_id, article.title, score, list(matched_terms)))

					# Log detailed info for verbose mode
					if self.verbose:
						for article_id, title, score, matched in articles_with_scores:
							self.log_message(f"      Article {article_id}: Score {score}, Terms: {', '.join(matched)}")
							self.log_message(f"        Title: {title[:100]}...")

					self.log_message(f"    Processed {processed} of {total_candidates} articles")

				self.stdout.write(f"    Matched {matched_for_subject} articles for subject '{subject.subject_name}'")

			# Current associations, scoped to the same window as the desired set
			assignment_qs = ArticleCategoryAssignment.objects.filter(teamcategory=cat)
			if cat_cutoff:
				assignment_qs = assignment_qs.filter(
					Q(articles__discovery_date__gte=cat_cutoff) |
					Q(articles__last_updated__gte=cat_cutoff)
				)
			automatic_ids = set(
				assignment_qs.filter(source=CategoryAssignmentSource.AUTOMATIC).values_list('articles_id', flat=True)
			)
			manual_ids = set(
				assignment_qs.exclude(source=CategoryAssignmentSource.AUTOMATIC).values_list('articles_id', flat=True)
			)
			if manual_ids:
				self.log_message(f"  Preserving {len(manual_ids)} manual article assignments")

			added, removed = self.sync_category(cat.articles, desired_ids, automatic_ids, manual_ids)
			total_added += added
			total_removed += removed
			self.stdout.write(f"  Category '{cat.category_name}': +{added} added, -{removed} removed articles")

		if self.dry_run:
			self.stdout.write(self.style.WARNING(
				f"DRY RUN: Would have added {total_added} and removed {total_removed} article categorizations"
			))
		else:
			self.stdout.write(self.style.SUCCESS(
				f"Added {total_added} and removed {total_removed} article categorizations in total"
			))

	def rebuild_cats_trials(self, days=None, batch_size=1000, min_score=3):
		self.stdout.write("Processing trials categorization...")

		# Define date cutoff for incremental updates
		cutoff_date = None
		if days:
			cutoff_date = timezone.now() - timedelta(days=days)
			self.stdout.write(f"Processing trials updated since {cutoff_date}")

		# Manual categories are curated entirely by hand and never touched here
		categories = self.target_categories()
		if self.category_id is None:
			manual_categories = TeamCategory.objects.exclude(category_type=CategoryType.AUTOMATIC).count()
			if manual_categories:
				self.stdout.write(f"Skipping {manual_categories} manual categories")
		total_categories = categories.count()
		total_added = 0
		total_removed = 0

		for index, cat in enumerate(categories, 1):
			terms = cat.category_terms

			self.stdout.write(f"[{index}/{total_categories}] Processing category: {cat.category_name}")

			config_hash = self.category_config_hash(cat, min_score)
			cat_cutoff = self.category_cutoff(cat, cutoff_date, config_hash)

			# Prepare term patterns for more accurate matching
			term_patterns = [re.compile(r'\b' + re.escape(term.lower()) + r'\b') for term in terms]

			# Desired set of trial ids for this category; empty terms mean no trials qualify
			desired_ids = set()

			if not terms:
				self.log_message(f"  Category '{cat.category_name}' has no terms; stale automatic associations will be removed")

			for subject in cat.subjects.all() if terms else []:
				self.log_message(f"  - Processing subject: {subject.subject_name}")

				matched_for_subject = 0

				# Base query for this subject
				base_query = Trials.objects.filter(subjects__id=subject.id)

				# Apply date filter if incremental
				if cat_cutoff:
					base_query = base_query.filter(
						Q(discovery_date__gte=cat_cutoff) |
						Q(last_updated__gte=cat_cutoff)
					)

				# Initial database filtering (broad match) - trials have more searchable fields
				query = Q()
				for term in terms:
					upper_term = term.upper()
					query |= (
						Q(utitle__contains=upper_term) |
						Q(usummary__contains=upper_term) |
						Q(intervention__icontains=term) |
						Q(primary_outcome__icontains=term) |
						Q(scientific_title__icontains=term) |
						Q(secondary_outcome__icontains=term) |
						Q(therapeutic_areas__icontains=term)
					)

				candidates = base_query.filter(query)
				total_candidates = candidates.count()
				self.log_message(f"    Found {total_candidates} candidate trials")

				processed = 0

				for batch in self.iter_batches(candidates, 'trial_id', batch_size):
					processed += len(batch)

					# Score-based categorization for this batch
					trials_with_scores = []

					for trial in batch:
						score = 0
						matched_terms = set()
						title = trial.title.lower()
						summary = (trial.summary or "").lower()
						intervention = (trial.intervention or "").lower()
						primary_outcome = (trial.primary_outcome or "").lower()
						scientific_title = (trial.scientific_title or "").lower()
						secondary_outcome = (trial.secondary_outcome or "").lower()
						therapeutic_areas = (trial.therapeutic_areas or "").lower()

						# Scoring system for trial categorization:
						# Title matches: 3 points (highest priority - most descriptive)
						# Summary matches: 2 points (good context)
						# Scientific title matches: 2 points (formal description)
						# Intervention matches: 2 points (what's being tested)
						# Primary/Secondary outcome matches: 1 point each (results focus)
						# Therapeutic areas matches: 1 point (general categorization)
						# Multiple term bonus: +2 points per unique matched term

						# Check for whole-word matches in title (highest weight)
						for i, pattern in enumerate(term_patterns):
							if pattern.search(title):
								score += 3
								matched_terms.add(terms[i])

						# Check for whole-word matches in summary
						for i, pattern in enumerate(term_patterns):
							if pattern.search(summary):
								score += 2
								matched_terms.add(terms[i])

						# Check for whole-word matches in scientific title
						for i, pattern in enumerate(term_patterns):
							if pattern.search(scientific_title):
								score += 2
								matched_terms.add(terms[i])

						# Check for whole-word matches in intervention
						for i, pattern in enumerate(term_patterns):
							if pattern.search(intervention):
								score += 2
								matched_terms.add(terms[i])

						# Check for whole-word matches in primary outcome
						for i, pattern in enumerate(term_patterns):
							if pattern.search(primary_outcome):
								score += 1
								matched_terms.add(terms[i])

						# Check for whole-word matches in secondary outcome
						for i, pattern in enumerate(term_patterns):
							if pattern.search(secondary_outcome):
								score += 1
								matched_terms.add(terms[i])

						# Check for whole-word matches in therapeutic areas
						for i, pattern in enumerate(term_patterns):
							if pattern.search(therapeutic_areas):
								score += 1
								matched_terms.add(terms[i])

						# Bonus for multiple term matches
						score += len(matched_terms) * 2

						# Add if score meets threshold
						if score >= min_score:
							desired_ids.add(trial.trial_id)
							matched_for_subject += 1
							trials_with_scores.append((trial.trial_id, trial.title, score, list(matched_terms)))

					# Log detailed info for verbose mode
					if self.verbose:
						for trial_id, title, score, matched in trials_with_scores:
							self.log_message(f"      Trial {trial_id}: Score {score}, Terms: {', '.join(matched)}")
							self.log_message(f"        Title: {title[:100]}...")
							# Show which fields contributed to the match
							trial = next((t for t in batch if t.trial_id == trial_id), None)
							if trial:
								search_fields = {
									'title': trial.title,
									'summary': trial.summary or '',
									'intervention': trial.intervention or '',
									'primary_outcome': trial.primary_outcome or '',
									'scientific_title': trial.scientific_title or '',
									'secondary_outcome': trial.secondary_outcome or '',
									'therapeutic_areas': trial.therapeutic_areas or ''
								}
								matching_fields = []
								for field_name, field_value in search_fields.items():
									for term in matched:
										if term.lower() in field_value.lower():
											matching_fields.append(field_name)
											break
								if matching_fields:
									self.log_message(f"        Matched in fields: {', '.join(set(matching_fields))}")

					self.log_message(f"    Processed {processed} of {total_candidates} trials")

				self.stdout.write(f"    Matched {matched_for_subject} trials for subject '{subject.subject_name}'")

			# Current associations, scoped to the same window as the desired set
			assignment_qs = TrialCategoryAssignment.objects.filter(teamcategory=cat)
			if cat_cutoff:
				assignment_qs = assignment_qs.filter(
					Q(trials__discovery_date__gte=cat_cutoff) |
					Q(trials__last_updated__gte=cat_cutoff)
				)
			automatic_ids = set(
				assignment_qs.filter(source=CategoryAssignmentSource.AUTOMATIC).values_list('trials_id', flat=True)
			)
			manual_ids = set(
				assignment_qs.exclude(source=CategoryAssignmentSource.AUTOMATIC).values_list('trials_id', flat=True)
			)
			if manual_ids:
				self.log_message(f"  Preserving {len(manual_ids)} manual trial assignments")

			added, removed = self.sync_category(cat.trials, desired_ids, automatic_ids, manual_ids)
			total_added += added
			total_removed += removed
			self.stdout.write(f"  Category '{cat.category_name}': +{added} added, -{removed} removed trials")

		if self.dry_run:
			self.stdout.write(self.style.WARNING(
				f"DRY RUN: Would have added {total_added} and removed {total_removed} trial categorizations"
			))
		else:
			self.stdout.write(self.style.SUCCESS(
				f"Added {total_added} and removed {total_removed} trial categorizations in total"
			))
