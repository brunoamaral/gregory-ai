from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from gregory.models import Articles, Trials, ArticleTrialReference
from gregory.utils.trial_acronyms import eligible_acronym, find_mentions
from gregory.utils.trial_identifiers import (
	extract_identifiers,
	extract_identifiers_from_trial_identifiers,
	may_contain_identifier,
)


class Command(BaseCommand):
	help = (
		"Detects trial identifiers and acronyms in the title and summary of every "
		"article and creates the ArticleTrialReference rows that are missing"
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
				"Links added by an editor, links an editor confirmed and links an "
				"editor unlinked are kept."
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
			"--skip-acronyms",
			action="store_true",
			help="Only look for registry identifiers; skip the trial-acronym pass.",
		)
		parser.add_argument(
			"--acronym-max-articles",
			type=int,
			default=50,
			help=(
				"An acronym mentioned in more than this many articles is a field term, "
				"not a trial name, and links nothing (default: 50)."
			),
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be created but don't save to database",
		)

	def handle(self, *args, **options):
		# Handle optional reset of auto-detected references. Manual links are an
		# editor's work, unlinked ones (suppressed) and confirmed ones are an
		# editor's decision, so none is something detection may regenerate or throw away.
		# Rows a --reset preview would have deleted: still in the database on a
		# dry run, so the scan below must not count them as already linked.
		reset_pks = set()
		if options["reset"]:
			resettable = ArticleTrialReference.objects.filter(
				source=ArticleTrialReference.SOURCE_AUTO,
				suppressed=False,
				confirmed=False,
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

		dry_run = options["dry_run"]
		verbose = options["verbosity"] >= 2
		restricted = bool(options["article_id"] or options["recent"])

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

		do_acronyms = not options["skip_acronyms"]
		if options["limit"]:
			articles = articles.order_by("article_id")[: options["limit"]]
			self.stdout.write(f"Limiting to {options['limit']} articles")
			if do_acronyms:
				# An acronym's article count needs the whole corpus.
				do_acronyms = False
				self.stdout.write(
					"Acronym pass skipped: --limit hides part of the corpus, "
					"so acronym article counts would be wrong"
				)

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

		# Acronym -> [trial_id, ...] over every trial, not just --trial-id: an
		# acronym two trials share is ambiguous whichever one was asked for.
		acronym_index = defaultdict(list)
		if do_acronyms:
			trial_rows = (
				Trials.objects.exclude(acronym__isnull=True)
				.exclude(acronym="")
				.values_list("trial_id", "acronym")
			)
			for trial_id, acronym in trial_rows:
				token = eligible_acronym(acronym)
				if token:
					acronym_index[token].append(trial_id)

		# Every (article, trial, identifier_type) that already has a row, loaded
		# once so a full scan only writes what is missing. get_or_create below
		# stays the actual write, so a concurrent run cannot duplicate a row.
		existing_keys = set(
			ArticleTrialReference.objects.exclude(pk__in=reset_pks).values_list(
				"article_id", "trial_id", "identifier_type"
			)
		)

		stats = {
			"created": 0,
			"linked_articles": set(),
			"linked_trials": set(),
			"scanned": 0,
		}
		already_linked_pairs = set()
		skipped_pairs = set()
		# Per acronym: how many articles mention it (whole corpus), and the
		# (article, acronym) mentions that sit next to trial wording. Only these
		# two are kept per article, never the texts.
		article_count = defaultdict(int)
		adjacent_mentions = []

		def scan_identifiers(article_id, text):
			if not may_contain_identifier(text):
				return
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

					stats["created"] += 1
					stats["linked_articles"].add(article_id)
					stats["linked_trials"].add(trial_id)
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

		def scan_acronyms(article_id, title, summary):
			mentions = find_mentions(f"{title or ''} . {summary or ''}", acronym_index)
			for acronym, adjacent in mentions.items():
				article_count[acronym] += 1
				if adjacent:
					adjacent_mentions.append((article_id, acronym))

		# One streamed pass over the articles: the corpus is large, so only the
		# three columns detection needs are read. The acronym counts need every
		# article, so when the identifier scan is narrowed (--article-id,
		# --recent) the whole corpus is read a second time for them.
		single_pass = do_acronyms and not restricted
		rows = articles.values_list("article_id", "title", "summary")
		for article_id, title, summary in rows.iterator(chunk_size=2000):
			stats["scanned"] += 1
			scan_identifiers(article_id, f"{title or ''} {summary or ''}")
			if single_pass and acronym_index:
				scan_acronyms(article_id, title, summary)

		scope_ids = None
		if do_acronyms and acronym_index and not single_pass:
			scope_ids = set(articles.values_list("article_id", flat=True))
			all_rows = Articles.objects.values_list("article_id", "title", "summary")
			for article_id, title, summary in all_rows.iterator(chunk_size=2000):
				scan_acronyms(article_id, title, summary)

		acronym_created = 0
		too_common = set()
		ambiguous = 0
		if do_acronyms and adjacent_mentions:
			acronym_created, too_common, ambiguous = self._link_acronyms(
				adjacent_mentions=adjacent_mentions,
				acronym_index=acronym_index,
				article_count=article_count,
				existing_keys=existing_keys,
				max_articles=options["acronym_max_articles"],
				scope_ids=scope_ids,
				only_trial_id=options["trial_id"],
				dry_run=dry_run,
				verbose=verbose,
				stats=stats,
			)

		created = stats["created"] + acronym_created
		linked_articles = stats["linked_articles"]
		linked_trials = stats["linked_trials"]
		action = "Would have created" if dry_run else "Created"
		message = (
			f"Scanned {stats['scanned']} articles against {trial_count} trials. "
			f"{action} {created} article-trial links "
			f"involving {len(linked_articles)} articles and {len(linked_trials)} trials; "
			f"{len(already_linked_pairs)} pairs already linked; "
			f"{len(skipped_pairs)} pairs skipped (suppressed or manual)"
		)
		if do_acronyms:
			message += (
				f". Acronym links: {acronym_created} created (counted above), "
				f"{len(too_common)} acronyms skipped as too common, "
				f"{ambiguous} mentions skipped as ambiguous"
			)
		self.stdout.write(self.style.SUCCESS(message))

	def _link_acronyms(
		self,
		adjacent_mentions,
		acronym_index,
		article_count,
		existing_keys,
		max_articles,
		scope_ids,
		only_trial_id,
		dry_run,
		verbose,
		stats,
	):
		"""Turn adjacent acronym mentions into links. Returns
		``(created, too_common_acronyms, ambiguous_count)``.

		A mention links only when exactly one trial carrying the acronym shares a
		subject with the article, and the pair has no row of any type yet.
		Never deletes anything: an acronym row stays even if its acronym later
		passes the cap or the trial's acronym changes.
		"""
		too_common = set()
		kept = []
		for article_id, acronym in adjacent_mentions:
			if article_count[acronym] > max_articles:
				too_common.add(acronym)
				continue
			kept.append((article_id, acronym))
		if not kept:
			return 0, too_common, 0

		# Subject through tables, loaded once: trial -> subjects, and
		# article -> subjects for the articles still in play.
		trial_ids = {t for acronym in {a for _, a in kept} for t in acronym_index[acronym]}
		trial_subjects = defaultdict(set)
		for trial_id, subject_id in Trials.subjects.through.objects.filter(
			trials_id__in=trial_ids
		).values_list("trials_id", "subject_id"):
			trial_subjects[trial_id].add(subject_id)
		article_subjects = defaultdict(set)
		for article_id, subject_id in Articles.subjects.through.objects.filter(
			articles_id__in={a for a, _ in kept}
		).values_list("articles_id", "subject_id"):
			article_subjects[article_id].add(subject_id)

		# Pairs with a row of any type (suppressed, manual, identifier, or one
		# written earlier in this run). Rows a --reset --dry-run would delete are
		# already left out of existing_keys.
		linked_pairs = {(a, t) for a, t, _ in existing_keys}

		created = 0
		ambiguous = 0
		for article_id, acronym in kept:
			if scope_ids is not None and article_id not in scope_ids:
				continue
			candidates = {
				t
				for t in acronym_index[acronym]
				if trial_subjects[t] & article_subjects[article_id]
			}
			if not candidates:
				continue
			if len(candidates) > 1:
				ambiguous += 1
				continue
			trial_id = next(iter(candidates))
			if only_trial_id and trial_id != only_trial_id:
				continue
			pair = (article_id, trial_id)
			if pair in linked_pairs:
				continue
			linked_pairs.add(pair)
			existing_keys.add((article_id, trial_id, "acronym"))
			created += 1
			stats["linked_articles"].add(article_id)
			stats["linked_trials"].add(trial_id)
			if verbose:
				prefix = "Would create" if dry_run else "Created"
				self.stdout.write(
					f"{prefix}: Article {article_id} -> Trial {trial_id} via acronym={acronym}"
				)
			if not dry_run:
				ArticleTrialReference.objects.get_or_create(
					article_id=article_id,
					trial_id=trial_id,
					identifier_type="acronym",
					defaults={
						"identifier_value": acronym,
						"source": ArticleTrialReference.SOURCE_AUTO,
						"confirmed": False,
					},
				)
		return created, too_common, ambiguous
