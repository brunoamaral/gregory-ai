from django.shortcuts import render
from django.contrib.syndication.views import Feed
from django.contrib.sites.models import Site
from gregory.models import Articles, Authors, Trials, Sources, TeamCategory
from django.urls import reverse
from django.contrib.sites.models import Site
from .models import CustomSetting

def get_website_domain():
	current_site = Site.objects.get_current()
	custom_setting = CustomSetting.objects.filter(site=current_site).first()
	return custom_setting.admin_email if custom_setting else current_site.domain

class LatestArticlesFeed(Feed):
	title = "Latest research articles"
	link = "/articles/"
	description = "Real-time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/" 

	def item_pubdate(self, item):
		return item.published_date


class ArticlesBySubjectFeed(Feed):
	title = "Latest research articles by Subject"
	link = "/articles/"
	description = "Real-time results for research on Multiple Sclerosis."

	def get_object(self, request, subject):
		subject = subject.replace('-', ' ')
		return Sources.objects.filter(subject__iregex=subject)

	def items(self, subject):
		return Articles.objects.filter(source__in=subject).order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class ArticlesByCategoryFeed(Feed):
	title = "Latest research articles by Subject"
	link = "/articles/"
	description = "Real-time results for research on Multiple Sclerosis."

	def get_object(self, request, team_id, category_slug):
		category = TeamCategory.objects.filter(team__id=team_id, category_slug=category_slug).first()
		if not category:
			raise Http404("Category does not exist")
		return category

	def items(self, obj):
		return Articles.objects.filter(team_categories=obj).order_by('-discovery_date')[:10]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		# item_link is only needed if NewsItem has no get_absolute_url method.
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class LatestTrialsFeed(Feed):
	title = "Latest clinical trials"
	link = "/trials/"
	description = "Real-time results for research on Multiple Sclerosis."

	def items(self):
		return Trials.objects.order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/trials/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class MachineLearningFeed(Feed):
	title = "Relevant articles by machine learning"
	link = "/articles/"
	description = "Real-time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=True).order_by('-discovery_date')[:20]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.link

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class ToPredictFeed(Feed):
	title = "Relevant articles by machine learning"
	link = "/articles/"
	description = "Real-time prediction results."

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=None)[:20]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class OpenAccessFeed(Feed):
	title = "Articles listed as open access on unpaywall.org"
	link = "/articles/"
	description = ""

	def items(self):
		return Articles.objects.filter(access='open').order_by('-discovery_date')[:20]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date


class ArticlesByAuthorFeed(Feed):
	title = "Articles by Author"
	link = "/feed/articles/"
	description = "RSS feed for articles by a specific author."

	def get_object(self, request, author_id):
		return Authors.objects.get(pk=author_id)

	def items(self, obj):
		return Articles.objects.filter(authors=obj)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return f"https://api.{get_website_domain()}/articles/{str(item.pk)}/"

	def item_pubdate(self, item):
		return item.published_date