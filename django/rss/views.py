from django.shortcuts import render

# Create your views here.
from django.contrib.syndication.views import Feed
from django.conf import settings
from gregory.models import Articles, Authors, Trials, Sources, TeamCategory
from django.urls import reverse



class LatestArticlesFeed(Feed):
	title = "Latest research articles"
	link = "/articles/"
	description = "Real time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://'+ settings.WEBSITE_DOMAIN + '/articles/' + str(item.pk) + '/' 

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

class ArticlesBySubjectFeed(Feed):
	title = "Latest research articles by Subject"
	link = "/articles/"
	description = "Real time results for research on Multiple Sclerosis."
	def get_object(self, request, subject):
			subject = subject.replace('-', ' ')
			subject = Sources.objects.filter(subject__iregex=subject)
			return subject

	def items(self, subject):
		return Articles.objects.filter(source__in=subject).order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://'+ settings.WEBSITE_DOMAIN + '/articles/' + str(item.pk) + '/' 

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

class ArticlesByCategoryFeed(Feed):
	title = "Latest research articles by Subject"
	link = "/articles/"
	description = "Real-time results for research on Multiple Sclerosis."

	def get_object(self, request, team_id, category_slug):
		# Retrieve the team category based on team_id and category_slug
		category = TeamCategory.objects.filter(team__id=team_id, category_slug=category_slug).first()
		if not category:
			raise Http404("Category does not exist")
		return category

	def items(self, obj):
		# Return the latest articles for the given category
		return Articles.objects.filter(team_categories=obj).order_by('-discovery_date')[:10]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		# item_link is only needed if NewsItem has no get_absolute_url method.
		return 'https://' + settings.WEBSITE_DOMAIN + '/articles/' + str(item.pk) + '/'

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

class LatestTrialsFeed(Feed):
	title = "Latest clinical trials"
	link = "/trials/"
	description = "Real time results for research on Multiple Sclerosis."

	def items(self):
		return Trials.objects.order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://api.' + settings.WEBSITE_DOMAIN + '/trials/' + str(item.pk) + '/'

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

class MachineLearningFeed(Feed):
	title = "Relevant articles by machine learning"
	link = "/articles/"
	description = "Real time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=True).order_by('-discovery_date')[:20]
	
	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.link

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://'+ settings.WEBSITE_DOMAIN + '/articles/' + str(item.pk) + '/' 

class ToPredictFeed(Feed):
	title = "Relevant articles by machine learning"
	link = "/articles/"
	description = "Real time prediction results"

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=None)[:20]
	
	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://'+ settings.WEBSITE_DOMAIN + '/articles/' + str(item.pk) + '/' 

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
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

	def item_pubdate(self, item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.published_date

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return item.link
	

class ArticlesByAuthorFeed(Feed):
	title = "Articles by Author"
	link = "/feed/articles/"
	description = "RSS feed for articles by a specific author. Use `/feed/articles/author/<author_id>/`"

	def __init__(self, **kwargs):
		self.author_id = kwargs.get('author_id')
		super().__init__(**kwargs)

	def get(self, request, *args, **kwargs):
		self.author_id = kwargs.get('author_id')
		return super().get(request, *args, **kwargs)

	def get_object(self, request, author_id, *args, **kwargs):
		return Authors.objects.get(pk=author_id)

	def items(self, obj):
		return Articles.objects.filter(authors=obj)

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	def item_link(self, item):
		return item.link

	def item_guid(self, item):
		return item.link

	def item_pubdate(self, item):
		return item.published_date

	def item_author_name(self, item):
		return ', '.join([author.full_name for author in item.authors.all()])

	def item_categories(self, item):
		return [category.category_name for category in item.categories.all()]

	def link(self, obj):
		return reverse('articles_by_author_feed', kwargs={'author_id': obj.pk})