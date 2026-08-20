"""
Shared UTM parameter construction for outbound email links.

All four senders (weekly digest, admin summary, trial notification,
announcement) build their base utm_params dict through build_utm_params()
so utm_source and utm_campaign can never drift apart between them.
utm_content is filled in per link slot at render time by the
gregory_tags.with_utm_content template filter, not here — this only sets
a default for the send's primary content type.
"""

from django.utils.text import slugify


def build_utm_params(source, list_obj, content):
	"""
	Build the base UTM parameter dict for a send.

	Args:
	    source: utm_source — the same string as the Postmark Tag already
	        passed to send_email (e.g. "weekly_summary", "admin_summary",
	        "trial_notification", "announcement").
	    list_obj: The Lists row this send is attributed to. Its
	        utm_campaign_slug is used verbatim. Lists.save() backfills
	        this from the list name on every list, so a blank slug here
	        would mean list_obj wasn't saved through the model — fall
	        back to slugifying list_name so a send is never left
	        completely untagged.
	    content: Default utm_content for this send, used by any link
	        that doesn't override it via with_utm_content.

	Returns:
	    dict with utm_medium, utm_source, utm_campaign, utm_content.
	"""
	campaign = getattr(list_obj, "utm_campaign_slug", "") or slugify(
		getattr(list_obj, "list_name", "") or ""
	)
	return {
		"utm_medium": "email",
		"utm_source": source,
		"utm_campaign": campaign or "unknown",
		"utm_content": content,
	}
