"""
migrate_legacy_takeaways
========================
One-shot management command that copies existing ``Articles.takeaways`` values
into ``ArticleOrgContent`` rows for a single designated organisation.

Usage
-----
Interactive (prompts for target org)::

    python manage.py migrate_legacy_takeaways

Non-interactive (scripted / CI)::

    python manage.py migrate_legacy_takeaways --org-id 3 --noinput

Dry run (no writes)::

    python manage.py migrate_legacy_takeaways --dry-run

The source column (``Articles.takeaways``) is NOT dropped here.
Removal is handled in a follow-up migration after a release of co-existence.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from gregory.models import Articles, ArticleOrgContent
from organizations.models import Organization


class Command(BaseCommand):
	help = (
		'Copy Articles.takeaways into ArticleOrgContent for a chosen organisation. '
		'Idempotent — rows that already exist are skipped.'
	)

	def add_arguments(self, parser):
		parser.add_argument(
			'--org-id',
			type=int,
			dest='org_id',
			help='ID of the target organisation. Required when --noinput is set.',
		)
		parser.add_argument(
			'--noinput',
			'--no-input',
			action='store_true',
			dest='no_input',
			help='Do not prompt for confirmation. Requires --org-id.',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			dest='dry_run',
			help='Report how many rows would be created without writing anything.',
		)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _list_organisations(self):
		"""Print a table of organisations with their article counts."""
		orgs = (
			Organization.objects
			.annotate(
				article_count=Count(
					'teams__articles',
					filter=Q(teams__articles__takeaways__isnull=False)
					& ~Q(teams__articles__takeaways=''),
					distinct=True,
				)
			)
			.order_by('id')
		)
		self.stdout.write('\nAvailable organisations:\n')
		self.stdout.write(f'  {"ID":>5}  {"Name":<40}  {"Articles with takeaways":>23}')
		self.stdout.write('  ' + '-' * 72)
		for org in orgs:
			self.stdout.write(f'  {org.id:>5}  {str(org.name):<40}  {org.article_count:>23}')
		self.stdout.write('')

	def _prompt_org_id(self):
		"""Interactively ask the operator to choose an organisation."""
		self._list_organisations()
		while True:
			raw = input('Enter the target organisation ID: ').strip()
			if raw.isdigit():
				return int(raw)
			self.stderr.write('Please enter a numeric ID.\n')

	def _resolve_org(self, org_id):
		"""Return the Organisation for *org_id*, raising CommandError if not found."""
		try:
			return Organization.objects.get(pk=org_id)
		except Organization.DoesNotExist:
			raise CommandError(f'Organisation with id={org_id} does not exist.')

	# ------------------------------------------------------------------
	# Entry point
	# ------------------------------------------------------------------

	def handle(self, *args, **options):
		org_id = options['org_id']
		no_input = options['no_input']
		dry_run = options['dry_run']

		# --- resolve target organisation ---
		if org_id is None:
			if no_input:
				raise CommandError(
					'--org-id is required when --noinput is set.'
				)
			org_id = self._prompt_org_id()

		org = self._resolve_org(org_id)

		# --- confirmation (unless --noinput or --dry-run) ---
		if not no_input and not dry_run:
			confirm = input(
				f'\nAbout to migrate Articles.takeaways → ArticleOrgContent '
				f'for organisation "{org.name}" (id={org.id}).\n'
				f'Continue? [y/N] '
			).strip().lower()
			if confirm not in ('y', 'yes'):
				self.stdout.write('Aborted.')
				return

		# --- gather candidate articles ---
		# Only articles that belong to the chosen organisation (via a team).
		# Without this scope the command would copy legacy takeaways onto
		# articles that have no relationship to the target org.
		articles_qs = (
			Articles.objects
			.filter(takeaways__isnull=False, teams__organization=org)
			.exclude(takeaways='')
			.only('article_id', 'takeaways')
			.distinct()
		)
		total_candidates = articles_qs.count()

		if dry_run:
			# Count how many would actually create a new row (skip existing).
			existing_article_ids = set(
				ArticleOrgContent.objects
				.filter(organization=org, article_id__in=articles_qs.values('article_id'))
				.values_list('article_id', flat=True)
			)
			would_create = total_candidates - len(existing_article_ids)
			would_skip = len(existing_article_ids)
			self.stdout.write(
				self.style.WARNING(
					f'[DRY RUN] Would create {would_create} ArticleOrgContent row(s), '
					f'skip {would_skip} existing row(s) '
					f'(out of {total_candidates} candidate article(s)).'
				)
			)
			return

		# --- migrate ---
		created = 0
		skipped = 0

		with transaction.atomic():
			for article in articles_qs.iterator(chunk_size=500):
				_, was_created = ArticleOrgContent.objects.get_or_create(
					article=article,
					organization=org,
					defaults={'takeaways': article.takeaways},
				)
				if was_created:
					created += 1
				else:
					skipped += 1

		self.stdout.write(
			self.style.SUCCESS(
				f'Done. Created {created} ArticleOrgContent row(s), '
				f'skipped {skipped} existing row(s) '
				f'(out of {total_candidates} candidate article(s)).'
			)
		)
