from django.shortcuts import render
from django.contrib.syndication.views import Feed
from django.contrib.sites.models import Site
from gregory.models import Articles, Authors
from django.urls import reverse
from django.contrib.sites.models import Site
from sitesettings.models import CustomSetting
from gregory.functions import normalize_orcid

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
		return Authors.objects.get(ORCID=normalized)

	# Feed metadata (dynamic per author)
	def title(self, obj):
		return f"Articles by {obj.full_name or 'Author'}"

	def link(self, obj):
		# Link to the author page using ORCID
		return f"https://{get_website_domain()}/authors/{obj.ORCID}/"

	description = "RSS feed for articles by a specific author."

	def items(self, obj):
		return Articles.objects.filter(authors=obj)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date