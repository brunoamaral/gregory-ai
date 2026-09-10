from django.contrib.syndication.views import Feed
from django.contrib.sites.models import Site
from django.db.models import F
from django.http import Http404
from gregory.models import Articles, Authors, Trials, Subject
from gregory.functions import normalize_orcid
from gregory.visibility import visible_subject_ids as _visible_subject_ids
from sitesettings.utils import author_page_base


def get_website_domain():
	current_site = Site.objects.get_current()
	# Always return a domain, never an email
	return current_site.domain


class ArticlesByAuthorFeed(Feed):
	def get_object(self, request, orcid):
		# Resolve strictly by normalized ORCID only
		normalized = normalize_orcid(orcid)
		if not normalized:
			raise Authors.DoesNotExist
		author = Authors.objects.get(ORCID=normalized)

		# Compute visibility and attach to the per-request obj (not self)
		# so concurrent requests on the shared Feed instance don't interfere.
		author._visible_subject_ids = _visible_subject_ids(request)

		# 404 if the author has no articles under any visible subject
		if not author.articles_set.filter(
			subjects__in=author._visible_subject_ids
		).exists():
			raise Http404

		return author

	# Feed metadata (dynamic per author)
	def title(self, obj):
		return f"Articles by {obj.full_name or 'Author'}"

	def link(self, obj):
		# Link to the site's author page when it publishes one, else orcid.org
		base = author_page_base(Site.objects.get_current())
		if base:
			return f"{base}/{obj.ORCID}/"
		return f"https://orcid.org/{obj.ORCID}"

	description = "RSS feed for articles by a specific author."

	def items(self, obj):
		return (
			Articles.objects.filter(
				authors=obj,
				subjects__in=obj._visible_subject_ids,
			)
			.distinct()
			# nulls_last: articles ingested without a date (filled later from
			# CrossRef) must not pin to the top of the feed
			.order_by(F("published_date").desc(nulls_last=True))[:50]
		)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://{get_website_domain()}/articles/{str(item.pk)}/"

	def item_guid(self, item):
		if item.doi:
			return f"doi:{item.doi}"
		return f"urn:gregory:article:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		return item.published_date

	def item_updateddate(self, item):
		return item.discovery_date


class TrialsBySubjectFeed(Feed):
	"""RSS feed for clinical trials filtered by subject slug."""

	def get_object(self, request, subject_slug):
		subject = Subject.objects.get(subject_slug=subject_slug)

		# The subject IS the visibility unit now, so this is a set membership
		# test rather than a walk up to the owning team's organisation. It
		# also closes a hole the old check left open: a subject with no team
		# skipped the check entirely and was served to anyone, which is how
		# an internal-research subject could be read by slug.
		if subject.id not in _visible_subject_ids(request):
			raise Http404

		return subject

	# Feed metadata (dynamic per subject)
	def title(self, obj):
		return f"Clinical Trials - {obj.subject_name}"

	def link(self, obj):
		return f"https://{get_website_domain()}/trials/subject/{obj.subject_slug}/"

	def description(self, obj):
		return f"RSS feed for clinical trials related to {obj.subject_name}."

	def items(self, obj):
		return (
			# get_object() has already established that obj is a visible
			# subject, so matching on it is the whole visibility test — no
			# second filter to add.
			Trials.objects.filter(subjects=obj)
			.distinct()
			.order_by("-discovery_date")[:50]
		)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		"""Build a rich description from available trial metadata."""
		parts = []

		# Primary summary
		if item.summary:
			parts.append(f"<p>{item.summary}</p>")

		# Trial metadata section
		metadata = []

		if item.recruitment_status_normalized or item.recruitment_status:
			# Prefer the canonical label; for "other" the raw registry string is more
			# informative than the label.
			status_display = (
				item.get_recruitment_status_normalized_display()
				if item.recruitment_status_normalized
				and item.recruitment_status_normalized != "other"
				else item.recruitment_status
				or item.get_recruitment_status_normalized_display()
			)
			metadata.append(f"<strong>Status:</strong> {status_display}")

		if item.phase_normalized or item.phase:
			# Prefer the canonical label; for "other" the raw registry string
			# (e.g. "Retrospective study") is more informative than the label.
			phase_display = (
				item.get_phase_normalized_display()
				if item.phase_normalized and item.phase_normalized != "other"
				else item.phase or item.get_phase_normalized_display()
			)
			metadata.append(f"<strong>Phase:</strong> {phase_display}")

		if item.study_type:
			metadata.append(f"<strong>Study Type:</strong> {item.study_type}")

		if item.primary_sponsor:
			metadata.append(f"<strong>Sponsor:</strong> {item.primary_sponsor}")

		if item.countries:
			metadata.append(f"<strong>Countries:</strong> {item.countries}")

		if item.condition:
			metadata.append(f"<strong>Condition:</strong> {item.condition}")

		if item.intervention:
			metadata.append(f"<strong>Intervention:</strong> {item.intervention}")

		# Eligibility criteria
		eligibility = []
		if item.inclusion_gender:
			eligibility.append(f"Gender: {item.inclusion_gender}")
		if item.inclusion_agemin and item.inclusion_agemax:
			eligibility.append(
				f"Age: {item.inclusion_agemin} - {item.inclusion_agemax}"
			)
		elif item.inclusion_agemin:
			eligibility.append(f"Min Age: {item.inclusion_agemin}")
		elif item.inclusion_agemax:
			eligibility.append(f"Max Age: {item.inclusion_agemax}")

		if eligibility:
			metadata.append(f"<strong>Eligibility:</strong> {', '.join(eligibility)}")

		if item.target_size:
			metadata.append(f"<strong>Target Size:</strong> {item.target_size}")

		if item.date_registration:
			metadata.append(
				f"<strong>Registration Date:</strong> {item.date_registration.strftime('%Y-%m-%d')}"
			)

		if item.source_register:
			metadata.append(f"<strong>Registry:</strong> {item.source_register}")

		if metadata:
			parts.append("<p>" + " | ".join(metadata) + "</p>")

		return "".join(parts) if parts else item.title

	def item_link(self, item):
		return item.link

	def item_guid(self, item):
		return f"urn:gregory:trial:{item.pk}"

	item_guid_is_permalink = False

	def item_pubdate(self, item):
		return item.published_date

	def item_updateddate(self, item):
		return item.last_updated
