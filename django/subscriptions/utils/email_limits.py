"""
Shared helpers for capping email payload size so an oversized send can never
repeat itself indefinitely.

See docs/subscriptions-audit-2026-07.md, P0 finding 1: nothing capped the
number of trials on the way into an email, so a bulk import of historical
trials rendered a body over Postmark's limit, the send failed, no
SentTrialNotification rows were written, and the next run rebuilt the
identical oversized payload.
"""

# Postmark rejects an HtmlBody over 5,242,880 characters (ErrorCode 300).
POSTMARK_MAX_BODY_CHARS = 5_242_880
# Headroom: the JSON payload also carries TextBody, and character count is not
# byte count. Shrink well before the hard limit.
SAFE_BODY_CHARS = 4_000_000

DEFAULT_ARTICLE_LIMIT = 15
DEFAULT_TRIAL_LIMIT = 15


def resolve_limits(list_obj):
	"""Return (article_limit, trial_limit), substituting defaults for None/0."""
	article_limit = getattr(list_obj, "article_limit", None) or DEFAULT_ARTICLE_LIMIT
	trial_limit = getattr(list_obj, "trial_limit", None) or DEFAULT_TRIAL_LIMIT
	return article_limit, trial_limit


def render_within_limit(
	render, articles, trials, latest_research=None, *, max_attempts=5
):
	"""
	Render an email, shrinking its content until the HTML fits SAFE_BODY_CHARS.

	`render(articles, trials)` must return (html, used_articles, used_trials) —
	the caller owns context building, so the returned "used" lists reflect what
	the content organizer actually placed in the template.

	Returns (html, used_articles, used_trials), or (None, [], []) when even a
	single article and a single trial will not fit.

	`latest_research`, when not None, is a third shrinkable list (opaque to
	this function — the weekly digest passes (category, article) pairs so its
	own render callback can regroup by category after a shrink). Passing it
	switches `render` to the 4-arg form `render(articles, trials,
	latest_research)` returning `(html, used_articles, used_trials,
	used_latest_research)`, and the same 4-tuple shape is returned here
	(all-empty on total failure). Omitting it keeps the original 3-tuple
	contract for callers that don't have a Latest Research section.
	"""
	current_articles = list(articles)
	current_trials = list(trials)
	track_latest_research = latest_research is not None
	current_latest_research = list(latest_research) if track_latest_research else []

	for _ in range(max_attempts):
		if track_latest_research:
			html, used_articles, used_trials, used_latest_research = render(
				current_articles, current_trials, current_latest_research
			)
		else:
			html, used_articles, used_trials = render(current_articles, current_trials)

		if len(html) <= SAFE_BODY_CHARS:
			if track_latest_research:
				return html, used_articles, used_trials, used_latest_research
			return html, used_articles, used_trials

		if (
			len(current_articles) <= 1
			and len(current_trials) <= 1
			and (not track_latest_research or len(current_latest_research) <= 1)
		):
			break

		current_articles = current_articles[: max(1, len(current_articles) // 2)]
		current_trials = current_trials[: max(1, len(current_trials) // 2)]
		if track_latest_research:
			current_latest_research = current_latest_research[
				: max(1, len(current_latest_research) // 2)
			]

	if track_latest_research:
		return None, [], [], []
	return None, [], []
