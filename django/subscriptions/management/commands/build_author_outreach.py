"""
Build the AuthorOutreach queue for one campaign. See
docs/author-outreach-spec.md "Queue and approval" and docs/author-outreach.md.

This command only ever writes status="pending" rows via
subscriptions.utils.author_outreach.eligible_authors. It never sends
anything — that is send_author_outreach, a later PR. A human reviews and
approves the queue in Django admin between the two.
"""

from django.core.management.base import BaseCommand, CommandError

from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign
from subscriptions.utils.author_outreach import eligible_authors


class Command(BaseCommand):
	help = (
		"Evaluate eligibility for one AuthorOutreachCampaign and write "
		"status=pending AuthorOutreach rows for every qualifying author. "
		"Never sends anything."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--campaign",
			required=True,
			help="AuthorOutreachCampaign.utm_campaign_slug to build the queue for.",
		)
		parser.add_argument(
			"--featured-since",
			type=int,
			default=None,
			dest="featured_since",
			help=(
				"Override campaign.featured_within_days for this run only. "
				"retrospective campaigns only, and requires --dry-run: a "
				"real queue is always built from the campaign's own stored "
				"fields, so the rules behind any real send stay "
				"recoverable from the campaign record rather than from "
				"shell history."
			),
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Evaluate and print eligibility without writing any AuthorOutreach row.",
		)
		parser.add_argument(
			"--limit",
			type=int,
			default=None,
			help="Stop after this many authors (useful for a smoke test).",
		)

	def handle(self, *args, **options):
		slug = options["campaign"]
		featured_since = options["featured_since"]
		dry_run = options["dry_run"]
		limit = options["limit"]

		try:
			campaign = AuthorOutreachCampaign.objects.get(utm_campaign_slug=slug)
		except AuthorOutreachCampaign.DoesNotExist:
			raise CommandError(
				f"No AuthorOutreachCampaign with utm_campaign_slug={slug!r}."
			)

		if featured_since is not None:
			if not dry_run:
				raise CommandError(
					"--featured-since requires --dry-run. A real queue is "
					"always built from the campaign's stored "
					"featured_within_days, so the rules behind any real "
					"send stay recoverable from the campaign record rather "
					"than from someone's shell history."
				)
			if campaign.mode != AuthorOutreachCampaign.MODE_RETROSPECTIVE:
				raise CommandError(
					f"--featured-since only applies to retrospective "
					f"campaigns; campaign {slug!r} is in {campaign.mode!r} mode."
				)

		if not campaign.enabled and not dry_run:
			# AuthorOutreachCampaign.enabled's help_text states this
			# explicitly: "build_author_outreach (PR 4) only writes rows
			# for an enabled campaign." A --dry-run preview is harmless
			# and useful while a campaign is still being configured, so it
			# is allowed regardless of enabled.
			raise CommandError(
				f"Campaign {slug!r} is not enabled. Enable it in the admin "
				"before building a real queue, or pass --dry-run to preview."
			)

		candidates = eligible_authors(campaign, since=featured_since)
		if limit is not None:
			candidates = candidates[:limit]

		if dry_run:
			self.stdout.write(
				self.style.WARNING("DRY RUN — no AuthorOutreach rows written")
			)
			for candidate in candidates:
				titles = "; ".join(article.title for article in candidate.articles)
				self.stdout.write(f"  {candidate.author} <{candidate.email}> — {titles}")
			self.stdout.write(
				f"Would queue {len(candidates)} author(s) for campaign {slug!r}."
			)
			return

		created = 0
		for candidate in candidates:
			row = AuthorOutreach.objects.create(
				campaign=campaign,
				site=campaign.site,
				author=candidate.author,
				email=candidate.email,
				status=AuthorOutreach.STATUS_PENDING,
				basis_note=(
					f"Selected under legitimate interest (GDPR Art. "
					f"6(1)(f)) by build_author_outreach for campaign "
					f"'{campaign.utm_campaign_slug}' ({campaign.mode}); "
					f"one-time contact, never repeated."
				),
			)
			row.articles.set(candidate.articles)
			created += 1

		self.stdout.write(
			self.style.SUCCESS(f"Queued {created} author(s) for campaign {slug!r}.")
		)
