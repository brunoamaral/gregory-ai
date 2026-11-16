from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from gregory.models import Articles, Trials, ArticleTrialReference
from gregory.utils.trial_identifiers import (
	extract_identifiers,
	extract_identifiers_from_trial_identifiers,
	may_contain_identifier,
)


class Command(BaseCommand):
	help = (
		"Detects trial identifiers in the title and summary of every article and "
		"creates the ArticleTrialReference rows that are missing"
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--article-id", type=int, help="Process a specific article (by ID)"
		)
		parser.add_argument(
			"--trial-id",
			type=int,
			help="Process articles looking for a specific trial (by ID)",
		)
		parser.add_argument(
			"--reset",
			action="store_true",
			help=(
				"Remove the auto-detected article-trial references before scanning. "
				"Links added by an editor and links an editor unlinked are kept."
			),
		)
		parser.add_argument(
			"--limit", type=int, help="Limit the number of articles processed"
		)
		parser.add_argument(
			"--recent",
			action="store_true",
			help=(
				"Process only articles discovered in the last 30 days (or --days). "
				"Off by default: the pipeline scans every article."
			),
		)
		parser.add_argument(
			"--days",
			type=int,
			default=30,
			help="Number of days to look back when using --recent (default: 30)",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be created but don't save to database",
		)

	def handle(self, *args, **options):
		# Handle optional reset of auto-detected references. Manual links are an
		# editor's work and unlinked ones (suppressed) are an editor's decision,
		# so neither is something detection may regenerate or throw away.
		# Rows a --reset preview would have deleted: still in the database on a
		# dry run, so the scan below must not count them as already linked.
		reset_pks = set()
		if options["reset"]:
			resettable = ArticleTrialReference.objects.filter(
				source=ArticleTrialReference.SOURCE_AUTO, suppressed=False
			)
			count = resettable.count()
			if options["dry_run"]:
				reset_pks = set(resettable.values_list("pk", flat=True))
				self.stdout.write(f"Would delete {count} existing references (dry run)")
			else:
				resettable.delete()
				self.stdout.write(f"Deleted {count} existing references")

		# (article, trial) pairs detection must leave alone: ones an editor
		# unlinked (suppressed), and ones an editor linked by hand (manual), which
		# a second, auto row would only list twice. Skipped whatever identifier
		# found them: a trial matched by a second identifier type is still the
		# link that was removed.
		skip_pairs = set(
			ArticleTrialReference.objects.filter(
				Q(suppressed=True) | Q(source=ArticleTrialReference.SOURCE_MANUAL)
			).values_list("article_id", "trial_id")
		)

		# Setup article query. Every article is read, whatever its summary: a
		# registry id in a title alone is a link.
		if options["article_id"]:
			articles = Articles.objects.filter(article_id=options["article_id"])
			self.stdout.write(
				f"Processing specific article ID: {options['article_id']}"
			)
		else:
			articles = Articles.objects.all()
			if options["recent"]:
				days = options["days"]
				date_threshold = timezone.now() - timedelta(days=days)
				articles = articles.filter(discovery_date__gte=date_threshold)
				self.stdout.write(f"Processing articles from the last {days} days")
			else:
				self.stdout.write("Processing all articles")

		if options["limit"]:
			articles = articles.order_by("article_id")[: options["limit"]]
			self.stdout.write(f"Limiting to {options['limit']} articles")

		# Setup trial query
		if options["trial_id"]:
			trials = Trials.objects.filter(trial_id=options["trial_id"])
			self.stdout.write(f"Looking for specific trial ID: {options['trial_id']}")
		else:
			trials = Trials.objects.filter(identifiers__isnull=False).exclude(
				identifiers={}
			)

		# Build a canonical-identifier -> [trial_id, ...] index once, instead of
		# re-scanning every trial's identifiers for every article.
		identifier_index = defaultdict(list)
		trial_count = 0
		for trial in trials.only("trial_id", "identifiers").iterator(chunk_size=2000):
			trial_count += 1
			for canonical_id in extract_identifiers_from_trial_identifiers(
				trial.identifiers
			):
				identifier_index[canonical_id].append(trial.trial_id)

		# Every (article, trial, identifier_type) that already has a row, loaded
		# once so a full scan only writes what is missing. get_or_create below
		# stays the actual write, so a concurrent run cannot duplicate a row.
		existing_keys = set(
			ArticleTrialReference.objects.exclude(pk__in=reset_pks).values_list(
				"article_id", "trial_id", "identifier_type"
			)
		)

		created = 0
		already_linked_pairs = set()
		skipped_pairs = set()
		articles_scanned = 0
		linked_articles = set()
		linked_trials = set()
		verbose = options["verbosity"] >= 2
		dry_run = options["dry_run"]

		# One streamed pass over the articles: the corpus is large, so only the
		# three columns detection needs are read.
		rows = articles.values_list("article_id", "title", "summary")
		for article_id, title, summary in rows.iterator(chunk_size=2000):
			articles_scanned += 1
			text = f"{title or ''} {summary or ''}"
			if not may_contain_identifier(text):
				continue

			for canonical_id in extract_identifiers(text):
				id_type, id_value = canonical_id
				for trial_id in identifier_index.get(canonical_id, []):
					pair = (article_id, trial_id)
					if pair in skip_pairs:
						skipped_pairs.add(pair)
						continue
					key = (article_id, trial_id, id_type)
					if key in existing_keys:
						already_linked_pairs.add(pair)
						continue

					created += 1
					linked_articles.add(article_id)
					linked_trials.add(trial_id)
					existing_keys.add(key)
					if verbose:
						prefix = "Would create" if dry_run else "Created"
						self.stdout.write(
							f"{prefix}: Article {article_id} -> Trial {trial_id} via {id_type}={id_value}"
						)
					if not dry_run:
						ArticleTrialReference.objects.get_or_create(
							article_id=article_id,
							trial_id=trial_id,
							identifier_type=id_type,
							defaults={"identifier_value": id_value},
						)

		action = "Would have created" if dry_run else "Created"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {articles_scanned} articles against {trial_count} trials. "
				f"{action} {created} article-trial links "
				f"involving {len(linked_articles)} articles and {len(linked_trials)} trials; "
				f"{len(already_linked_pairs)} pairs already linked; "
				f"{len(skipped_pairs)} pairs skipped (suppressed or manual)"
			)
		)
