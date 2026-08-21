"""
Tests for the author-outreach eligibility engine
(subscriptions/utils/author_outreach.py) — see docs/author-outreach-spec.md "Who
qualifies" and docs/author-outreach.md.

The two tests that matter most: `test_upcoming_mode_agrees_with_digest_subset`
asserts against `select_digest_articles` itself (not a reimplementation) —
the guard against outreach and the digest silently drifting apart — and
`test_already_sent_article_not_queued_in_upcoming_mode` locks in the
measured case from the spec (MS Weekly Digest: 12 candidates, only 5
never-sent).

Every fixture below is built with plain factory helper methods rather than
Django fixture files, per docs/author-outreach.md's "Tests" note.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.sites.models import Site
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles, ArticleSubjectRelevance, Authors, MLPredictions, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.subscription import select_digest_articles
from subscriptions.models import (
	AuthorContactOptOut,
	AuthorOutreach,
	AuthorOutreachCampaign,
	Lists,
	SentArticleNotification,
	Subscribers,
	SuppressionEvent,
)
from subscriptions.utils.author_outreach import eligible_authors


class AuthorOutreachEligibilityTests(TestCase):
	"""Each test builds its own isolated site/team/subject/list/campaign via
	`_new_world` so scenarios never bleed into one another, then layers on
	the articles/authors/predictions the scenario needs."""

	def _new_world(
		self,
		tag,
		mode=AuthorOutreachCampaign.MODE_UPCOMING,
		ml_threshold=0.8,
		article_sort_order="relevancy",
		article_limit=15,
		lookback_days=30,
		auto_predict=True,
		ml_consensus_type="any",
		campaign_kwargs=None,
		list_kwargs=None,
	):
		org = Organization.objects.create(name=f"Org {tag}", slug=f"org-{tag}")
		team = Team.objects.create(name=f"Team {tag}", organization=org, slug=f"team-{tag}")
		subject = Subject.objects.create(
			subject_name=f"Subject {tag}",
			team=team,
			subject_slug=f"subject-{tag}",
			auto_predict=auto_predict,
			ml_consensus_type=ml_consensus_type,
		)
		site = Site.objects.create(domain=f"{tag}.example.com", name=tag)
		CustomSetting.objects.create(site=site, title=f"CS {tag}", has_author_pages=True)

		list_defaults = dict(
			list_name=f"List {tag}",
			team=team,
			weekly_digest=True,
			article_sort_order=article_sort_order,
			article_limit=article_limit,
			lookback_days=lookback_days,
			ml_threshold=ml_threshold,
			site=site,
		)
		if list_kwargs:
			list_defaults.update(list_kwargs)
		digest_list = Lists.objects.create(**list_defaults)
		digest_list.subjects.add(subject)

		campaign_defaults = dict(
			site=site,
			name=f"Campaign {tag}",
			utm_campaign_slug=f"campaign-{tag}",
			mode=mode,
			enabled=True,
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

	def _author(self, tag, orcid=None, emails=None, verified=True, claimed=True):
		return Authors.objects.create(
			given_name="Test",
			family_name=f"Author {tag}",
			ORCID=orcid or f"orcid-{tag}",
			emails=emails if emails is not None else [f"{tag}@example.com"],
			orcid_verified_email=verified,
			orcid_claimed=claimed,
		)

	def _prediction(self, article, subject, algorithm, score, predicted_relevant=True, model_version="v1"):
		return MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			probability_score=score,
			predicted_relevant=predicted_relevant,
			model_version=model_version,
		)

	def _relevance(self, article, subject, is_relevant):
		return ArticleSubjectRelevance.objects.create(
			article=article, subject=subject, is_relevant=is_relevant
		)

	def _subscriber(self, tag):
		return Subscribers.objects.create(
			first_name="Sub", last_name=tag, email=f"sub-{tag}@example.com", active=True
		)

	def _mark_sent(self, article, digest_list, subscriber):
		return SentArticleNotification.objects.create(
			article=article, list=digest_list, subscriber=subscriber
		)

	# -- relevance gate: union, not intersection ---------------------------

	def test_featured_article_failing_relevance_gate_yields_nothing(self):
		w = self._new_world(
			"gate1",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			campaign_kwargs={"featured_within_days": 30},
		)
		article = self._article([w.subject], "gate1-a", published_date=timezone.now())
		author = self._author("gate1")
		article.authors.add(author)
		subscriber = self._subscriber("gate1")
		self._mark_sent(article, w.list, subscriber)
		# No ML prediction, no manual relevance record at all.

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_ml_consensus_only_article_yields_author(self):
		w = self._new_world("mlonly")
		article = self._article([w.subject], "mlonly-a", published_date=timezone.now())
		author = self._author("mlonly")
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		self.assertEqual(result[0].author, author)
		self.assertEqual([a.pk for a in result[0].articles], [article.pk])

	def test_curator_only_article_yields_author(self):
		w = self._new_world("curatoronly")
		article = self._article([w.subject], "curatoronly-a", published_date=timezone.now())
		author = self._author("curatoronly")
		article.authors.add(author)
		self._relevance(article, w.subject, True)

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		self.assertEqual(result[0].author, author)

	def test_explicit_irrelevant_on_one_subject_blocks_despite_digest_featuring_it(self):
		"""The one place outreach rules must diverge from the digest's own
		filter_articles_excluding_all_irrelevant: a subject explicitly
		marked is_relevant=False kills that subject's candidacy even when
		ML consensus passes for it, and does not get rescued by a second,
		merely-unreviewed list subject the way the digest itself is."""
		w = self._new_world("divergence")
		subject_b = Subject.objects.create(
			subject_name="Subject divergence B",
			team=w.team,
			subject_slug="subject-divergence-b",
			auto_predict=True,
			ml_consensus_type="any",
		)
		w.list.subjects.add(subject_b)

		article = self._article([w.subject, subject_b], "divergence-a", published_date=timezone.now())
		author = self._author("divergence")
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.95)
		self._relevance(article, w.subject, False)
		# "Not reviewed" for subject B is represented, per
		# ArticleSubjectRelevance.is_relevant's own docstring, by a row
		# with is_relevant=None -- not by the absence of a row.
		self._relevance(article, subject_b, None)

		digest_candidates, _ = select_digest_articles(w.list, w.list.lookback_days)
		self.assertIn(article.pk, list(digest_candidates.values_list("pk", flat=True)))

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_ml_threshold_read_from_featuring_list_not_a_constant(self):
		# article_sort_order="date" so select_digest_articles's own
		# candidate set is threshold-independent -- isolates ml_threshold
		# usage to the outreach engine's own relevance gate.
		w_high = self._new_world("thr-high", ml_threshold=0.95, article_sort_order="date")
		w_low = self._new_world("thr-low", ml_threshold=0.5, article_sort_order="date")

		article_high = self._article([w_high.subject], "thr-high-a", published_date=timezone.now())
		author_high = self._author("thr-high")
		article_high.authors.add(author_high)
		self._prediction(article_high, w_high.subject, "pubmed_bert", 0.7)

		article_low = self._article([w_low.subject], "thr-low-a", published_date=timezone.now())
		author_low = self._author("thr-low")
		article_low.authors.add(author_low)
		self._prediction(article_low, w_low.subject, "pubmed_bert", 0.7)

		self.assertEqual(eligible_authors(w_high.campaign), [])
		result_low = eligible_authors(w_low.campaign)
		self.assertEqual(len(result_low), 1)
		self.assertEqual(result_low[0].author, author_low)

	# -- article cap per author ---------------------------------------------

	def test_author_with_two_qualifying_papers_gets_one_row_with_both(self):
		w = self._new_world("twopapers")
		author = self._author("twopapers")
		articles = []
		for i in range(2):
			a = self._article(
				[w.subject], f"twopapers-{i}", published_date=timezone.now() - timedelta(days=i)
			)
			a.authors.add(author)
			self._prediction(a, w.subject, "pubmed_bert", 0.9)
			articles.append(a)

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		self.assertEqual({a.pk for a in result[0].articles}, {a.pk for a in articles})

	def test_author_with_four_qualifying_papers_keeps_three_most_recent(self):
		w = self._new_world("fourpapers")  # max_articles_per_email default 3
		author = self._author("fourpapers")
		articles = []
		for i in range(4):
			a = self._article(
				[w.subject], f"fourpapers-{i}", published_date=timezone.now() - timedelta(days=i)
			)
			a.authors.add(author)
			self._prediction(a, w.subject, "pubmed_bert", 0.9)
			articles.append(a)

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		# i=0..2 (days=0,1,2) are the three most recent; i=3 (days=3) drops.
		self.assertEqual(
			[a.pk for a in result[0].articles],
			[articles[0].pk, articles[1].pk, articles[2].pk],
		)

	# -- exclusions (each independently blocks a row) ------------------------

	def test_exclusion_existing_author_outreach_row_same_site(self):
		w = self._new_world("existing")
		author = self._author("existing")
		article = self._article([w.subject], "existing-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		AuthorOutreach.objects.create(
			campaign=w.campaign, site=w.site, author=author, email="existing@example.com"
		)

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_exclusion_author_contact_optout(self):
		w = self._new_world("optout")
		author = self._author("optout", emails=["optout-target@example.com"])
		article = self._article([w.subject], "optout-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		AuthorContactOptOut.objects.create(
			email="optout-target@example.com", reason=AuthorContactOptOut.REASON_OPT_OUT
		)

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_exclusion_deactivated_subscriber(self):
		w = self._new_world("deactivated")
		author = self._author("deactivated", emails=["deactivated-target@example.com"])
		article = self._article([w.subject], "deactivated-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		Subscribers.objects.create(
			first_name="X", last_name="Y", email="deactivated-target@example.com", active=False
		)

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_exclusion_latest_suppression_event_suppress_sending_true(self):
		w = self._new_world("suppressed")
		author = self._author("suppressed", emails=["suppressed-target@example.com"])
		article = self._article([w.subject], "suppressed-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		SuppressionEvent.objects.create(
			email="suppressed-target@example.com", changed_at=timezone.now(), suppress_sending=True
		)

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_suppression_event_reactivated_does_not_exclude(self):
		"""Exclusion looks at the *latest* SuppressionEvent, not "any
		suppression ever" -- an unsuppress after a suppress must un-block."""
		w = self._new_world("reactivated")
		author = self._author("reactivated", emails=["reactivated-target@example.com"])
		article = self._article([w.subject], "reactivated-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		SuppressionEvent.objects.create(
			email="reactivated-target@example.com",
			changed_at=timezone.now() - timedelta(days=2),
			suppress_sending=True,
		)
		SuppressionEvent.objects.create(
			email="reactivated-target@example.com",
			changed_at=timezone.now(),
			suppress_sending=False,
		)

		result = eligible_authors(w.campaign)
		self.assertEqual(len(result), 1)

	# -- upcoming mode vs the digest ------------------------------------------

	def test_upcoming_mode_agrees_with_digest_subset(self):
		"""Assert against select_digest_articles itself, not a
		reimplementation -- the guard against outreach and the digest
		drifting apart."""
		w = self._new_world("subsetcheck", article_limit=2)
		author = self._author("subsetcheck")
		for i in range(4):
			a = self._article(
				[w.subject], f"subsetcheck-{i}", published_date=timezone.now() - timedelta(days=i)
			)
			a.authors.add(author)
			self._prediction(a, w.subject, "pubmed_bert", 0.9)

		result = eligible_authors(w.campaign)
		digest_candidates, _ = select_digest_articles(w.list, w.list.lookback_days)
		digest_pks = set(digest_candidates.values_list("pk", flat=True))
		queued_pks = {a.pk for candidate in result for a in candidate.articles}

		self.assertTrue(queued_pks)  # non-trivial: guard against a vacuous subset check
		self.assertTrue(queued_pks.issubset(digest_pks))

	def test_already_sent_article_not_queued_in_upcoming_mode(self):
		"""The measured case: a digest candidate that was already sent for
		this list weeks ago (but is still inside the 30-day lookback) must
		never be queued -- MS Weekly Digest has 12 candidates, 5 never-sent."""
		w = self._new_world("alreadysent", lookback_days=30)
		author = self._author("alreadysent")

		article_old = self._article(
			[w.subject], "alreadysent-old", published_date=timezone.now() - timedelta(days=20)
		)
		article_old.authors.add(author)
		self._prediction(article_old, w.subject, "pubmed_bert", 0.9)
		subscriber = self._subscriber("alreadysent")
		self._mark_sent(article_old, w.list, subscriber)

		article_new = self._article([w.subject], "alreadysent-new", published_date=timezone.now())
		article_new.authors.add(author)
		self._prediction(article_new, w.subject, "pubmed_bert", 0.9)

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		queued_pks = {a.pk for a in result[0].articles}
		self.assertNotIn(article_old.pk, queued_pks)
		self.assertIn(article_new.pk, queued_pks)

	def test_article_ranked_below_article_limit_not_queued(self):
		w = self._new_world("ranklimit", article_limit=1, article_sort_order="relevancy")
		author = self._author("ranklimit")

		top_article = self._article([w.subject], "ranklimit-top", published_date=timezone.now())
		top_article.authors.add(author)
		self._relevance(top_article, w.subject, True)  # manual review -> priority 1000

		low_article = self._article(
			[w.subject], "ranklimit-low", published_date=timezone.now() - timedelta(hours=1)
		)
		low_article.authors.add(author)
		self._prediction(low_article, w.subject, "pubmed_bert", 0.9)  # ML-only -> priority 100

		result = eligible_authors(w.campaign)

		self.assertEqual(len(result), 1)
		queued_pks = {a.pk for a in result[0].articles}
		self.assertIn(top_article.pk, queued_pks)
		self.assertNotIn(low_article.pk, queued_pks)

	def test_date_sorted_list_with_no_ml_predictions_yields_nothing(self):
		"""Regression guard for the measured Neuroinflammation case: a
		date-sorted list with no ML predictions on its subjects must never
		produce an outreach candidate."""
		w = self._new_world("dateonly", article_sort_order="date")
		author = self._author("dateonly")
		article = self._article([w.subject], "dateonly-a", published_date=timezone.now())
		article.authors.add(author)
		# No MLPredictions, no ArticleSubjectRelevance at all.

		self.assertEqual(eligible_authors(w.campaign), [])

	def test_author_claimed_by_back_catalogue_campaign_not_requeued_by_second_campaign(self):
		"""UniqueConstraint(site, author) spans campaigns -- the mechanism
		that makes running a steady-state and a back-catalogue campaign on
		the same site safe."""
		w = self._new_world("crosscamp")
		author = self._author("crosscamp")
		article = self._article([w.subject], "crosscamp-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)

		back_catalogue_campaign = AuthorOutreachCampaign.objects.create(
			site=w.site,
			name="Back catalogue",
			utm_campaign_slug="crosscamp-retro",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			enabled=True,
			featured_within_days=90,
			max_articles_per_email=3,
		)
		AuthorOutreach.objects.create(
			campaign=back_catalogue_campaign, site=w.site, author=author, email="crosscamp@example.com"
		)

		self.assertEqual(eligible_authors(w.campaign), [])

	# -- since / --featured-since guard --------------------------------------

	def test_since_rejected_for_upcoming_campaign(self):
		w = self._new_world("sinceguard", mode=AuthorOutreachCampaign.MODE_UPCOMING)
		with self.assertRaises(ValueError):
			eligible_authors(w.campaign, since=30)

	def test_since_overrides_featured_within_days_in_retrospective_mode(self):
		w = self._new_world(
			"sinceoverride",
			mode=AuthorOutreachCampaign.MODE_RETROSPECTIVE,
			campaign_kwargs={"featured_within_days": 7},
		)
		author = self._author("sinceoverride")
		article = self._article([w.subject], "sinceoverride-a", published_date=timezone.now())
		article.authors.add(author)
		self._prediction(article, w.subject, "pubmed_bert", 0.9)
		subscriber = self._subscriber("sinceoverride")
		sent = self._mark_sent(article, w.list, subscriber)
		SentArticleNotification.objects.filter(pk=sent.pk).update(
			sent_at=timezone.now() - timedelta(days=20)
		)

		# Outside the campaign's own 7-day window...
		self.assertEqual(eligible_authors(w.campaign), [])
		# ...but within a wider window passed via since=.
		result = eligible_authors(w.campaign, since=30)
		self.assertEqual(len(result), 1)
