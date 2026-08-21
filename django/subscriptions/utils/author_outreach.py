"""
Eligibility engine for author outreach — see AUTHOR-OUTREACH-SPEC.md "Who
qualifies" and AUTHOR-OUTREACH-PLAN.md "PR 4 — Eligibility engine".

`eligible_authors(campaign, since=None)` is the only entry point
`build_author_outreach` (the management command in this same PR) uses. It is
read-only: it never writes an AuthorOutreach row, sends anything, or mutates
any other model — building rows from its output is the caller's job. That
makes it safe to call from --dry-run, from an admin preview, or from a test.

Central requirement, `upcoming` mode: eligibility is read from the digest's
own `select_digest_articles` (subscriptions/management/commands/utils/
subscription.py), never reimplemented. The outreach email promises "your
paper will be featured in the next digest" — if this module computed
candidates on its own, that promise could disagree with what the digest
actually sends, in exactly the cases nobody tests. `rank_and_limit_articles`,
the same helper the digest uses to truncate a subscriber's article set to
`article_limit`, is reused for the same reason.

A digest *candidate* is not the same as "will be featured", though:
`select_digest_articles` works over `list.lookback_days` (30 by default), so
its candidate set includes articles already sent weeks ago that no existing
subscriber will receive again. `_upcoming_candidate_ids` therefore narrows
the shared selector's output further — never sent for this list, and ranked
within `list.article_limit` — before anything downstream sees it. See the
spec's "Measured reality" for the numbers this closes: MS Weekly Digest has
12 raw candidates but only 5 never-sent.

The relevance gate below (rule 3 in the spec) is deliberately stricter than
the digest's own `filter_articles_excluding_all_irrelevant`, which only
drops an article rejected across *every* one of its list-shared subjects. A
paper rejected for the campaign's subject but unreviewed for another still
reaches the digest; it must not trigger an outreach email whose second claim
is curator approval for the subject it was rejected on. Do not "fix" this
divergence to match the digest — AUTHOR-OUTREACH-PLAN.md PR 4 calls it out
by name, and test_author_outreach_eligibility.py locks it in.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone

from api.filters import ml_relevant_articles_q
from gregory.models import Articles, ArticleSubjectRelevance, Authors
from subscriptions.management.commands.utils.subscription import (
	rank_and_limit_articles,
	select_digest_articles,
)
from subscriptions.models import (
	AuthorContactOptOut,
	AuthorOutreach,
	AuthorOutreachCampaign,
	Lists,
	SentArticleNotification,
	Subscribers,
	SuppressionEvent,
)

# Sort key floor for articles with no published_date, so they rank last
# within an author's row instead of a None/datetime comparison blowing up.
# A fixed sentinel (rather than None) keeps the sort well-defined even when
# every candidate article for an author is undated.
_UNDATED = datetime(1, 1, 1, tzinfo=dt_timezone.utc)


@dataclass(frozen=True)
class EligibleAuthor:
	"""
	One outreach candidate: an author, the address to use, and the
	qualifying papers to name in their email — already capped to
	`campaign.max_articles_per_email` and ordered most-recent-published_date
	first (spec "Who qualifies", rule 8, and "Up to 3 papers...").
	"""

	author: Authors
	email: str
	articles: list


def eligible_authors(campaign, since=None):
	"""
	Return the `EligibleAuthor` candidates for `campaign` right now,
	evaluating every rule in AUTHOR-OUTREACH-SPEC.md "Who qualifies"
	against the live database. Read-only.

	`since`, when given, is a number of days that overrides
	`campaign.featured_within_days` — `retrospective` mode only. It exists
	for `build_author_outreach --featured-since`, itself dry-run-only (see
	that command): a real queue is always built from the campaign's stored
	fields, so the rules behind any send stay recoverable from the campaign
	record rather than from a CLI flag. Passing `since` for an `upcoming`
	campaign is refused with ValueError — `upcoming` mode never reads
	`featured_within_days` in the first place, so there is nothing for it
	to override.
	"""
	if since is not None and campaign.mode != AuthorOutreachCampaign.MODE_RETROSPECTIVE:
		raise ValueError(
			"since (--featured-since) only applies to retrospective "
			f"campaigns; campaign '{campaign.utm_campaign_slug}' is in "
			f"'{campaign.mode}' mode."
		)

	lists = Lists.objects.filter(site=campaign.site, weekly_digest=True)
	campaign_subject_ids = set(campaign.subjects.values_list("id", flat=True))
	if campaign_subject_ids:
		# Rule 4: a non-empty campaign subject set restricts which lists
		# are processed to those sharing at least one subject with it.
		lists = lists.filter(subjects__id__in=campaign_subject_ids).distinct()

	# article_id -> Articles instance, deduped across every qualifying list
	# a campaign touches (an article can qualify via more than one list).
	qualifying_articles = {}
	# author_id -> set of article ids they qualify through.
	articles_by_author = {}

	for digest_list in lists.prefetch_related("subjects"):
		if campaign.mode == AuthorOutreachCampaign.MODE_UPCOMING:
			candidate_ids = _upcoming_candidate_ids(digest_list)
		else:
			cutoff_days = since if since is not None else campaign.featured_within_days
			candidate_ids = _retrospective_candidate_ids(digest_list, cutoff_days)

		if not candidate_ids:
			continue

		list_subjects = list(digest_list.subjects.all())
		relevant_ids = _relevance_gate(candidate_ids, list_subjects, digest_list.ml_threshold)
		if not relevant_ids:
			continue

		for article in Articles.objects.filter(pk__in=relevant_ids).prefetch_related("authors"):
			qualifying_articles[article.pk] = article
			for author in article.authors.all():
				articles_by_author.setdefault(author.pk, set()).add(article.pk)

	if not articles_by_author:
		return []

	results = []
	for author in Authors.objects.filter(pk__in=articles_by_author.keys()):
		if not _author_qualifies(author):
			continue
		email = _primary_email(author)
		if email is None:
			continue
		if _is_excluded(campaign, author, email):
			continue
		articles = sorted(
			(qualifying_articles[aid] for aid in articles_by_author[author.pk]),
			key=lambda a: a.published_date or _UNDATED,
			reverse=True,
		)[: campaign.max_articles_per_email]
		results.append(EligibleAuthor(author=author, email=email, articles=articles))

	# Deterministic order for callers/tests — doesn't matter for
	# correctness, but a stable run-to-run order makes --limit predictable.
	results.sort(key=lambda r: r.author.pk)
	return results


def _upcoming_candidate_ids(digest_list):
	"""
	Spec "Who qualifies", mode `upcoming`, rules 1-2: call the digest's own
	selector, keep only articles with no `SentArticleNotification` row for
	this list (never sent to anyone), then rank+limit exactly as a real
	send would — `rank_and_limit_articles` is the same helper
	`send_weekly_summary` uses to truncate a subscriber's unsent set to
	`article_limit`, so this can't drift on ranking either.
	"""
	candidates, scores = select_digest_articles(digest_list, digest_list.lookback_days)

	sent_article_ids = set(
		SentArticleNotification.objects.filter(list=digest_list, article__in=candidates)
		.values_list("article_id", flat=True)
		.distinct()
	)
	never_sent = candidates.exclude(pk__in=sent_article_ids)

	ranked = rank_and_limit_articles(
		never_sent,
		digest_list.article_limit,
		digest_list.article_sort_order,
		all_articles=False,
		article_priority_scores=scores,
	)
	return {article.pk for article in ranked}


def _retrospective_candidate_ids(digest_list, featured_within_days):
	"""Spec "Who qualifies", mode `retrospective`, rules 1-2: a
	SentArticleNotification exists for (article, list) within
	featured_within_days of now."""
	cutoff = timezone.now() - timedelta(days=featured_within_days)
	return set(
		SentArticleNotification.objects.filter(list=digest_list, sent_at__gte=cutoff)
		.values_list("article_id", flat=True)
		.distinct()
	)


def _relevance_gate(candidate_ids, list_subjects, ml_threshold):
	"""
	Spec "Who qualifies", shared rule 3: an article passes for at least one
	of `list_subjects` when ML consensus (via
	`api.filters.ml_relevant_articles_q`, the same consensus shape the API
	and the ML-drift check use, scored at `ml_threshold`) OR a manual
	`ArticleSubjectRelevance.is_relevant=True` exists — evaluated, and
	excluded, per subject rather than per article. An explicit
	`is_relevant=False` on subject S kills S's candidacy even when ML
	consensus passes for S; it does not disqualify the article via a
	different subject T that has its own positive signal. This is what
	makes the gate stricter than the digest's
	`filter_articles_excluding_all_irrelevant`, which only drops an article
	rejected across *every* one of its list-shared subjects.
	"""
	passing = set()
	for subject in list_subjects:
		ml_relevant_ids = set(
			Articles.objects.filter(pk__in=candidate_ids)
			.filter(ml_relevant_articles_q(threshold=ml_threshold, subject_ids=[subject.pk]))
			.values_list("pk", flat=True)
		)
		manual_relevant_ids = set(
			ArticleSubjectRelevance.objects.filter(
				article_id__in=candidate_ids, subject=subject, is_relevant=True
			).values_list("article_id", flat=True)
		)
		manual_irrelevant_ids = set(
			ArticleSubjectRelevance.objects.filter(
				article_id__in=candidate_ids, subject=subject, is_relevant=False
			).values_list("article_id", flat=True)
		)
		passing |= (ml_relevant_ids | manual_relevant_ids) - manual_irrelevant_ids
	return passing


def _author_qualifies(author):
	"""Spec "Who qualifies", shared rule 5: a non-empty Authors.emails, a
	verified ORCID email, and a claimed ORCID record."""
	return (
		bool(author.emails)
		and bool(author.ORCID)
		and author.orcid_verified_email is True
		and author.orcid_claimed is True
	)


def _primary_email(author):
	"""Spec "Addressing and sending": Authors.emails[0], lowercased. One
	address only — no fallback to a second entry on failure."""
	if not author.emails:
		return None
	raw = author.emails[0]
	if not raw:
		return None
	return str(raw).strip().lower()


def _is_excluded(campaign, author, email):
	"""Spec "Who qualifies", shared rule 6-7: every independent reason an
	otherwise-qualifying author must not be queued. Each check stands
	alone — losing on any single one is enough to exclude the author."""
	if AuthorOutreach.objects.filter(site=campaign.site, author=author).exists():
		# Rule 7. Keyed on (site, author), not (campaign, author) — see
		# AuthorOutreach's UniqueConstraint — so a slot claimed by any
		# campaign on this site (including a different one) burns it here.
		return True
	return is_contact_blocked(email)


def is_contact_blocked(email):
	"""
	Spec "Who qualifies", shared rule 6: every independent, address-only
	reason an outreach email must not go to `email`. This is the exact
	set of checks `_is_excluded` above runs at build time (minus the
	build-time-only "does an AuthorOutreach row already exist" rule 7,
	which doesn't apply once a row exists), factored out so
	send_author_outreach (PR 5) can run the identical checks again
	immediately before every individual send — see
	AUTHOR-OUTREACH-SPEC.md "Safety limits": "The guards are evaluated
	before every individual send, not once per run." An address opted
	out, suppressed, or deactivated in the time between build and send
	must not receive the email just because it passed this check once
	at queue-build time.
	"""
	if AuthorContactOptOut.objects.filter(email__iexact=email).exists():
		return True
	if Subscribers.objects.filter(email__iexact=email, active=False).exists():
		return True
	latest_suppression = (
		SuppressionEvent.objects.filter(email__iexact=email).order_by("-changed_at").first()
	)
	if latest_suppression is not None and latest_suppression.suppress_sending:
		return True
	return False
