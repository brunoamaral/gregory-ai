from django.shortcuts import render

# Create your views here.
from django.contrib.syndication.views import Feed
from django.conf import settings
from gregory.models import Articles, Authors, Trials, Sources, Categories
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
	description = "Real time results for research on Multiple Sclerosis."
	def get_object(self, request, category):
			category = category.replace('-', ' ')
			category = Categories.objects.filter(category_name__iregex=category)
			return category

	def items(self, category):
		return Articles.objects.filter(categories=category.first()).order_by('-discovery_date')[:5]

	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
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



class Twitter(Feed):

	title = "post to twitter"
	link = "/feed/twitter/"
	description = "Real time results for relevant research"

	def items(self):
		import itertools
		from operator import attrgetter

		articles_list_1 = Articles.objects.filter(relevant=True).order_by('-article_id')
		articles_list_2 = Articles.objects.filter(ml_prediction_gnb=True).order_by('-article_id')
		# articles_list = Articles.objects.filter(criterion1 or criterion2)[:10]
		trials_list = Trials.objects.all().order_by('-trial_id')[:10]
		result_list = sorted( itertools.chain(articles_list_1[:10],articles_list_2[:10], trials_list),key=attrgetter('discovery_date'),reverse=True)
		return result_list
	
	def item_title(self, item):
		object_type = '#ClinicalTrial '
		if hasattr(item, 'article_id'):
			if item.ml_prediction_gnb == True:
				object_type = '#Article #ML '
			if item.relevant == True:
				object_type = '#Article #Manual '
			if item.relevant == True and item.ml_prediction_gnb == True:
				object_type = '#Article #Manual #ML '


		item.title = object_type + item.title[:100] + '...'
		return item.title

	def item_description(self, item):
		object_type = '#ClinicalTrial '
		if hasattr(item, 'article_id'):
			if item.ml_prediction_gnb == True:
				object_type = '#Article #ML '
			if item.relevant == True:
				object_type = '#Article #Manual '
			if item.relevant == True and item.ml_prediction_gnb == True:
				object_type = '#Article #Manual #ML '

		if hasattr(item,'takeaways') and item.takeaways != None:
			item.description = object_type + item.takeaways
		else:
			item.description = None
		return item.description

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		object_type = 'trials/'
		if hasattr(item, 'article_id'):
			object_type = 'articles/'
		return item.link 

	def item_pubdate(self,item):
		"""
		Takes an item, as returned by items(), and returns the item's
		pubdate.
		"""
		return item.discovery_date

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