"""
Tests for the build_author_outreach management command — see
AUTHOR-OUTREACH-SPEC.md "Queue and approval" and AUTHOR-OUTREACH-PLAN.md
"PR 4 — Eligibility engine". Eligibility rule coverage itself lives in
test_author_outreach_eligibility.py; this file covers the command's own
behaviour: what it writes, --dry-run, --limit, and the --featured-since
guard rails (dry-run-only, retrospective-only).

Factory helper methods build fixtures directly rather than using Django
fixture files, per AUTHOR-OUTREACH-PLAN.md PR 4's "Tests" note.
"""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

from django.contrib.sites.models import Site
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles, Authors, MLPredictions, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	AuthorOutreach,
	AuthorOutreachCampaign,
	Lists,
	SentArticleNotification,
	Subscribers,
)


class BuildAuthorOutreachCommandTests(TestCase):
	def _new_world(self, tag, mode=AuthorOutreachCampaign.MODE_UPCOMING, enabled=True, campaign_kwargs=None):
		org = Organization.objects.create(name=f"Org {tag}", slug=f"org-{tag}")
		team = Team.objects.create(name=f"Team {tag}", organization=org, slug=f"team-{tag}")
		subject = Subject.objects.create(
			subject_name=f"Subject {tag}",
			team=team,
			subject_slug=f"subject-{tag}",
			auto_predict=True,
			ml_consensus_type="any",
		)
		site = Site.objects.create(domain=f"{tag}.example.com", name=tag)
		CustomSetting.objects.create(site=site, title=f"CS {tag}", has_author_pages=True)
		digest_list = Lists.objects.create(
			list_name=f"List {tag}",
			team=team,
			weekly_digest=True,
			article_sort_order="relevancy",
			article_limit=15,
			lookback_days=30,
			ml_threshold=0.8,
			site=site,
		)
		digest_list.subjects.add(subject)

		campaign_defaults = dict(
			site=site,
			name=f"Campaign {tag}",
			utm_campaign_slug=f"campaign-{tag}",
			mode=mode,
			enabled=enabled,
			max_articles_per_email=3,
		)
		if campaign_kwargs:
			campaign_defaults.update(campaign_kwargs)
		campaign = AuthorOutreachCampaign.objects.create(**campaign_defaults)

		return SimpleNamespace(
			org=org, team=team, subject=subject, site=site, list=digest_list, campaign=campaign
		)

	def _article(self, subjects, tag, published_date=None):
		article = Articles.objects.create(
			title=f"Article {tag}",
			link=f"https://example.com/{tag}",
			doi=f"10.9999/{tag}",
			published_date=published_date,
		)
		article.subjects.set(subjects)
		return article

	def _author(self, tag, emails=None):
		return Authors.objects.create(
			given_name="Test",
			family_name=f"Author {tag}",
			ORCID=f"orcid-{tag}",
			emails=emails if emails is not None else [f"{tag}@example.com"],
			orcid_verified_email=True,
			orcid_claimed=True,
		)

	def _prediction(self, article, subject, score=0.9, algorithm="pubmed_bert"):
		return MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			probability_score=score,
			predicted_relevant=True,
			model_version="v1",
		)

	def _qualifying_author(self, w, tag):
		author = self._author(tag)
		article = self._article([w.subject], tag, published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject)
		return author, article

	# -- basic build behaviour ------------------------------------------------

	def test_dry_run_writes_nothing(self):
		w = self._new_world("dryrun")
		self._qualifying_author(w, "dryrun-a")

		out = StringIO()
		call_command("build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, "--dry-run", stdout=out)

		self.assertEqual(AuthorOutreach.objects.count(), 0)
		self.assertIn("DRY RUN", out.getvalue())

	def test_writes_pending_rows_with_articles_attached(self):
		w = self._new_world("write")
		author, article = self._qualifying_author(w, "write-a")

		call_command("build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, stdout=StringIO())

		self.assertEqual(AuthorOutreach.objects.count(), 1)
		row = AuthorOutreach.objects.get()
		self.assertEqual(row.author, author)
		self.assertEqual(row.site, w.site)
		self.assertEqual(row.campaign, w.campaign)
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)
		self.assertEqual(list(row.articles.all()), [article])
		self.assertEqual(row.email, author.emails[0].lower())

	def test_rerun_creates_no_duplicates(self):
		w = self._new_world("rerun")
		self._qualifying_author(w, "rerun-a")

		call_command("build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, stdout=StringIO())
		self.assertEqual(AuthorOutreach.objects.count(), 1)

		call_command("build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, stdout=StringIO())
		self.assertEqual(AuthorOutreach.objects.count(), 1)

	def test_limit_option_caps_number_queued(self):
		w = self._new_world("limit")
		self._qualifying_author(w, "limit-a")
		self._qualifying_author(w, "limit-b")
		self._qualifying_author(w, "limit-c")

		call_command(
			"build_author_outreach",
			"--campaign",
			w.campaign.utm_campaign_slug,
			"--limit",
			"1",
			stdout=StringIO(),
		)

		self.assertEqual(AuthorOutreach.objects.count(), 1)

	def test_unknown_campaign_slug_exits_nonzero(self):
		with self.assertRaises(CommandError):
			call_command(
				"build_author_outreach", "--campaign", "does-not-exist", stdout=StringIO()
			)
		self.assertEqual(AuthorOutreach.objects.count(), 0)

	def test_disabled_campaign_refuses_real_build_but_allows_dry_run(self):
		w = self._new_world("disabled", enabled=False)
		self._qualifying_author(w, "disabled-a")

		with self.assertRaises(CommandError):
			call_command(
				"build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, stdout=StringIO()
			)
		self.assertEqual(AuthorOutreach.objects.count(), 0)

		# A preview must still work while a campaign is being configured.
		out = StringIO()
		call_command(
			"build_author_outreach",
			"--campaign",
			w.campaign.utm_campaign_slug,
			"--dry-run",
			stdout=out,
		)
		self.assertIn("DRY RUN", out.getvalue())
		self.assertEqual(AuthorOutreach.objects.count(), 0)

	# -- --featured-since guard rails -----------------------------------------

	def test_featured_since_on_upcoming_campaign_exits_nonzero(self):
		w = self._new_world("sinceupcoming", mode=AuthorOutreachCampaign.MODE_UPCOMING)

		with self.assertRaises(CommandError):
			call_command(
				"build_author_outreach",
				"--campaign",
				w.campaign.utm_campaign_slug,
				"--featured-since",
				"14",
				"--dry-run",
				stdout=StringIO(),
			)
		self.assertEqual(AuthorOutreach.objects.count(), 0)

	def test_featured_since_without_dry_run_exits_nonzero_and_writes_nothing(self):
		w = self._new_world(
			"sincenodry",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			campaign_kwargs={"featured_within_days": 7},
		)

		with self.assertRaises(CommandError):
			call_command(
				"build_author_outreach",
				"--campaign",
				w.campaign.utm_campaign_slug,
				"--featured-since",
				"30",
				stdout=StringIO(),
			)
		self.assertEqual(AuthorOutreach.objects.count(), 0)

	def test_featured_since_dry_run_on_retrospective_overrides_window(self):
		w = self._new_world(
			"sinceoverride",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			campaign_kwargs={"featured_within_days": 7},
		)
		author, article = self._qualifying_author(w, "sinceoverride-a")
		subscriber = Subscribers.objects.create(
			first_name="Sub", last_name="Since", email="since-sub@example.com", active=True
		)
		sent = SentArticleNotification.objects.create(
			article=article, list=w.list, subscriber=subscriber
		)
		SentArticleNotification.objects.filter(pk=sent.pk).update(
			sent_at=timezone.now() - timedelta(days=20)
		)

		# Outside the campaign's stored 7-day window: nothing queued, no write.
		out_default = StringIO()
		call_command(
			"build_author_outreach", "--campaign", w.campaign.utm_campaign_slug, "--dry-run", stdout=out_default
		)
		self.assertIn("Would queue 0 author", out_default.getvalue())

		# --featured-since 30, --dry-run: previews the author, still writes nothing.
		out_override = StringIO()
		call_command(
			"build_author_outreach",
			"--campaign",
			w.campaign.utm_campaign_slug,
			"--featured-since",
			"30",
			"--dry-run",
			stdout=out_override,
		)
		self.assertIn("Would queue 1 author", out_override.getvalue())
		self.assertEqual(AuthorOutreach.objects.count(), 0)
