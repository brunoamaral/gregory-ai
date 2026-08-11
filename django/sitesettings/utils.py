from .models import CustomSetting


def author_page_base(site, custom_settings=None):
	"""Base URL for this site's author profile pages, or "" when it has none.

	Callers append "/<orcid>/" to build the full link. Resolves the
	CustomSetting itself when not passed — CustomSetting.site is a plain
	FK (not unique), so the lookup is ordered by setting_id for a
	deterministic choice if more than one row ever exists for a site.
	"""
	if site is None:
		return ""

	if custom_settings is None:
		custom_settings = (
			CustomSetting.objects.filter(site=site).order_by("setting_id").first()
		)

	if custom_settings is None or not custom_settings.has_author_pages:
		return ""

	domain = site.domain.strip() if site.domain else ""
	if not domain:
		return ""

	scheme = "http" if domain in ("localhost", "127.0.0.1") else "https"
	return f"{scheme}://{domain}/authors"
