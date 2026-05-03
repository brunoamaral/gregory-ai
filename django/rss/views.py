from django.shortcuts import render
from django.contrib.syndication.views import Feed
from django.contrib.sites.models import Site
from django.http import Http404
from gregory.models import Articles, Authors, Trials, Subject
from gregory.functions import normalize_orcid
from gregory.visibility import visible_org_ids as _visible_org_ids

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

		# Compute visibility and attach to self for use in items()
		self._visible_org_ids = _visible_org_ids(request)

		# 404 if author has no articles in any visible org
		if not author.articles_set.filter(
			teams__organization_id__in=self._visible_org_ids
		).exists():
			raise Http404

		return author

	# Feed metadata (dynamic per author)
	def title(self, obj):
		return f"Articles by {obj.full_name or 'Author'}"

	def link(self, obj):
		# Link to the author page using ORCID
		return f"https://{get_website_domain()}/authors/{obj.ORCID}/"

	description = "RSS feed for articles by a specific author."

	def items(self, obj):
		return (
			Articles.objects.filter(
				authors=obj,
				teams__organization_id__in=self._visible_org_ids,
			)
			.distinct()
			.order_by('-published_date')[:50]
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

		# Compute visibility and attach to self for use in items()
		self._visible_org_ids = _visible_org_ids(request)

		# 404 if subject belongs to an org that isn't visible
		if subject.team_id is not None:
			from gregory.models import Team as _Team
			try:
				team = _Team.objects.get(id=subject.team_id)
				if team.organization_id not in self._visible_org_ids:
					raise Http404
			except _Team.DoesNotExist:
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
			Trials.objects.filter(
				subjects=obj,
				teams__organization_id__in=self._visible_org_ids,
			)
			.distinct()
			.order_by('-discovery_date')[:50]
		)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		"""Build a rich description from available trial metadata."""
		parts = []
		
		# Primary summary - prefer plain English summary if available
		if item.summary_plain_english:
			parts.append(f"<p>{item.summary_plain_english}</p>")
		elif item.summary:
			parts.append(f"<p>{item.summary}</p>")
		
		# Trial metadata section
		metadata = []
		
		if item.recruitment_status:
			metadata.append(f"<strong>Status:</strong> {item.recruitment_status}")
		
		if item.phase:
			metadata.append(f"<strong>Phase:</strong> {item.phase}")
		
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
			eligibility.append(f"Age: {item.inclusion_agemin} - {item.inclusion_agemax}")
		elif item.inclusion_agemin:
			eligibility.append(f"Min Age: {item.inclusion_agemin}")
		elif item.inclusion_agemax:
			eligibility.append(f"Max Age: {item.inclusion_agemax}")
		
		if eligibility:
			metadata.append(f"<strong>Eligibility:</strong> {', '.join(eligibility)}")
		
		if item.target_size:
			metadata.append(f"<strong>Target Size:</strong> {item.target_size}")
		
		if item.date_registration:
			metadata.append(f"<strong>Registration Date:</strong> {item.date_registration.strftime('%Y-%m-%d')}")
		
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