from django.shortcuts import render

# Create your views here.
from django.contrib.syndication.views import Feed
from gregory.models import Articles, Trials

class LatestArticlesFeed(Feed):
	title = "Gregory MS - latest research articles"
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
		return 'https://gregory-ms.com/articles/' + str(item.pk) + '/' 

class LatestTrialsFeed(Feed):
	title = "Gregory MS - latest clinical trials"
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
		return 'https://gregory-ms.com/trials/' + str(item.pk) + '/'

class MachineLearningFeed(Feed):
	title = "Gregory MS - Relevant articles by machine learning"
	link = "/articles/"
	description = "Real time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=True)[:20]
	
	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://gregory-ms.com/articles/' + str(item.pk) + '/' 

class ToPredictFeed(Feed):
	title = "Gregory MS - Relevant articles by machine learning"
	link = "/articles/"
	description = "Real time results for research on Multiple Sclerosis."

	def items(self):
		return Articles.objects.filter(ml_prediction_gnb=None)[:20]
	
	def item_title(self, item):
		return item.title

	def item_description(self, item):
		return item.summary

	# # item_link is only needed if NewsItem has no get_absolute_url method.
	def item_link(self, item):
		return 'https://gregory-ms.com/articles/' + str(item.pk) + '/' 


class Twitter(Feed):

	title = "Gregory MS - post to twitter"
	link = "/feed/twitter/"
	description = "Real time results for relevant research on Multiple Sclerosis."

	def items(self):
		import itertools
		from django.db.models import Q
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
		return None

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