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
	title = "Articles by Author"
	link = "/feed/author/"
	description = "RSS feed for articles by a specific author."

	def get_object(self, request, orcid):
		# Support ORCID provided as full URL or plain ID; also fallback to numeric author_id
		normalized = normalize_orcid(orcid)
		if normalized:
			try:
				return Authors.objects.get(ORCID=normalized)
			except Authors.DoesNotExist:
				pass
		# Backward compatibility: allow /feed/author/<author_id>/
		if orcid.isdigit():
			return Authors.objects.get(pk=int(orcid))
		# Not found
		raise Authors.DoesNotExist

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