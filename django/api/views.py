from api.serializers import ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer, AuthorSerializer
from django.db.models.functions import Length
from gregory.models import Articles, Trials, Sources, Authors, Categories
from rest_framework import viewsets, permissions, generics, filters
from django.db.models import Q
from rest_framework.decorators import api_view
import json
from datetime import datetime, timedelta

import os
from sitesettings.models import CustomSetting
from gregory.classes import SciencePaper

site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))

# Stuff needed for the API with authorization
import traceback
from api.utils.utils import (checkValidAccess, getAPIKey, getIPAddress)
from api.models import APIAccessSchemeLog
from api.utils.exceptions import (
										APIAccessDeniedError,
										APIInvalidAPIKeyError,
										APIInvalidIPAddressError,
										APINoAPIKeyError, 
										ArticleExistsError, 
										ArticleNotSavedError,
										DoiNotFound, 
										FieldNotFoundError, 
										SourceNotFoundError, 
										)
from api.utils.responses import (ACCESS_DENIED, INVALID_API_KEY,
										 INVALID_IP_ADDRESS, NO_API_KEY,
										 UNEXPECTED, SOURCE_NOT_FOUND, FIELD_NOT_FOUND, ARTICLE_EXISTS, ARTICLE_NOT_SAVED, returnData, returnError)

def getDateRangeFromWeek(p_year,p_week):
	firstdayofweek = datetime.strptime(f'{p_year}-W{int(p_week )- 1}-1', "%Y-W%W-%w")
	lastdayofweek = firstdayofweek + timedelta(days=6.9)
	return (firstdayofweek,lastdayofweek)

# Util function that creates an instance of the access log model
def generateAccessSchemeLog(call_type, ip_addr, access_scheme, http_code, error_message, post_data):
	log = APIAccessSchemeLog()
	log.call_type = call_type
	log.ip_addr = ip_addr
	log.api_access_scheme = access_scheme
	log.http_code = http_code
	if error_message != None and len(error_message) > 499:
		log.error_message = error_message[0:499]
	if error_message == None:
		log.error_message = error_message
	else:
		log.error_message = error_message
	log.payload_received = post_data
	log.save()

###
# API Post
###

@api_view(['POST'])
def post_article(request):
		"""
		Allows authenticated clients to add new articles to the database
		"""
		call_type = request.method + " " + request.path
		ip_addr = getIPAddress(request)
		post_data = json.loads(request.body)
		try:
			api_key = getAPIKey(request)

			# This checks if this API key and IP address are authorized to access
			# this API endpoint. If so, the valid client access scheme is returned
			access_scheme = checkValidAccess(api_key, ip_addr)

			# At this point, the API client is authorized
			# Check for fields
			if 'kind' not in post_data or post_data['kind'] == None:
				raise FieldNotFoundError('field `kind` was not found in the payload')
			if 'title' not in post_data and 'doi' not in post_data:
				if post_data['title'] == None and post_data['doi'] == None:
					raise FieldNotFoundError('field `doi` and `title` not in the payload. You need at least one.')
			if 'source_id' not in post_data or post_data['source_id'] == None:
					raise FieldNotFoundError('source_id field not found in payload')
			
			new_article = {
				"title": None if 'title' not in post_data or post_data['title'] == '' else post_data['title'],
				"link": None if 'link' not in post_data or post_data['link'] == '' else post_data['link'],
				"doi": None if 'doi' not in post_data or post_data['doi'] == '' else post_data['doi'],
				"access": None if 'access' not in post_data or post_data['access'] == '' else post_data['access'],
				"summary": None if 'summary' not in post_data or post_data['summary'] == '' else post_data['summary'],
				"source_id": None if 'source_id' not in post_data or post_data['source_id'] == '' else post_data['source_id'],
				"published_date": None if 'published_date' not in post_data or post_data['published_date'] == '' else post_data['published_date'],
				# Not sure if and how we should post the authors
				# "authors": post_data['authors'],
				"kind": None if 'kind' not in post_data or post_data['kind'] == '' else post_data['kind'],
				"access": None if 'access' not in post_data or post_data['access'] == '' else post_data['access'],
				"publisher": None if 'publisher' not in post_data or post_data['publisher'] == '' else post_data['publisher'],
				"container_title": None if 'container_title' not in post_data or post_data['container_title'] == '' else post_data['container_title']
			}


			science_paper = None
			if new_article['kind'] == 'science paper':
				science_paper = SciencePaper(doi=new_article['doi'],title=new_article['title'])
			if science_paper.doi == None:
				science_paper.doi = science_paper.find_doi(title=science_paper.title)

			if science_paper.doi != None:
				science_paper.refresh()
			if new_article['doi'] == None:
				new_article['doi'] = science_paper.doi
			if new_article['title'] == None:
				new_article['title'] = science_paper.title
			if new_article['link'] == None:
				new_article['link'] = science_paper.link
			if new_article['summary'] == None:
				new_article['summary'] = science_paper.clean_abstract()
			if new_article['published_date'] == None:
				new_article['published_date'] = science_paper.published_date
			if new_article['access'] == None:
				new_article['access'] = science_paper.access
			if new_article['publisher'] == None:
				new_article['publisher'] = science_paper.publisher
			if new_article['container_title'] == None:
				new_article['container_title'] = science_paper.journal
			if new_article['access'] == None:
				new_article['access'] == science_paper.access
			
			article_on_gregory = None
			if new_article['doi'] != None:
				article_on_gregory = Articles.objects.filter(doi=new_article['doi'])
			if article_on_gregory != None and article_on_gregory.count() > 0:
				raise ArticleExistsError('There is already an article with the specified DOI')

			if new_article['title'] != None:
				article_on_gregory = Articles.objects.filter(title=new_article['title'])
			if article_on_gregory != None and article_on_gregory.count() > 0:
				raise ArticleExistsError('There is already an article with the specified Title')

			source = Sources.objects.get(pk=new_article['source_id'])
			if source.pk == None:
				raise SourceNotFoundError('source_id was not found in the database')

			save_article = Articles.objects.create(discovery_date=datetime.now(), title = new_article['title'], summary = new_article['summary'], link = new_article['link'], published_date = new_article['published_date'], source = source, doi = new_article['doi'], kind = new_article['kind'],
			publisher=new_article['publisher'], container_title=new_article['container_title'])
			if save_article.pk == None:
				raise ArticleNotSavedError('Could not create the article')
			
			# Prepare some data to be returned to the API client
			data = {
				'name': site.title + '| API',
				'version': '0.1b',
				"data_received": json.loads(request.body),
				'data_processed_from_doi': new_article,
				'article_id': save_article.article_id,
			}
			log_data = {
				'name': site.title + '| API',
				'version': '0.1b',
				"article_id": save_article.pk,
			}
			# This creates an access log for this client in the DB
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 201, '', log_data)
			# Actually return the data to the API client
			return returnData(data)
		except APINoAPIKeyError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 401, str(exception), str(post_data))
			return returnError(NO_API_KEY, str(exception), 401)
		except APIInvalidAPIKeyError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 401, str(exception), str(post_data))
			return returnError(INVALID_API_KEY, str(exception), 401)
		except APIInvalidIPAddressError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 401, str(exception), str(post_data))
			return returnError(INVALID_IP_ADDRESS, str(exception), 401)
		except APIAccessDeniedError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 403, str(exception), str(post_data))
			return returnError(ACCESS_DENIED, str(exception), 403)
		except FieldNotFoundError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 202, str(exception), str(post_data))
			return returnError(FIELD_NOT_FOUND, str(exception), 200)
		except ArticleExistsError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 204, str(exception), str(post_data))
			return returnError(ARTICLE_EXISTS, str(exception), 200)
		except ArticleNotSavedError as exception:
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 204, str(exception), str(post_data))
			return returnError(ARTICLE_NOT_SAVED, str(exception), 204)
		except Exception as exception:
			print(traceback.format_exc())
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 500, str(exception), str(post_data))
			return returnError(UNEXPECTED, str(exception), 500)

###
# ARTICLES
### 
class ArticleViewSet(viewsets.ModelViewSet):
	"""
	List all articles in the database by descending article_id
	"""
	queryset = Articles.objects.all().order_by('-article_id')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$title','$summary']

class RelatedArticles(viewsets.ModelViewSet):
	"""
	Search related articles by the noun_phrases field. This search accepts regular expressions such as /articles/related/?search=<noun_phrase>|<noun_phrase>
	"""
	queryset = Articles.objects.all().order_by('-published_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$noun_phrases']


class ArticlesByCategory(viewsets.ModelViewSet):
	"""
	Search articles by the category field. Usage /articles/category/{{category}}/
	"""
	def get_queryset(self):
		category = self.kwargs.get('category', None)
		category = Categories.objects.filter(category_name__iregex=category)
		return Articles.objects.filter(categories=category.first()).order_by('-article_id')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class ArticlesBySubject(viewsets.ModelViewSet):
	"""
	Search articles by the subject field. Usage /articles/subject/{{subject}}/.
	Subject should be lower case and spaces should be replaced by dashes, for example: Multiple Sclerosis becomes multiple-sclerosis.
	"""
	def get_queryset(self):
		subject = self.kwargs.get('subject', None)
		subject = subject.replace('-', ' ')
		subject = Sources.objects.filter(subject__subject_name__iregex=subject)
		return Articles.objects.filter(source__in=subject).order_by('-article_id')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class ArticlesByJournal(viewsets.ModelViewSet):
	"""
	Search articles by the journal field. Usage /articles/journal/{{journal}}/.
	Journal should be lower case and spaces should be replaced by dashes, for example: 	"The Lancet Neurology" becomes the-lancet-neurology.
	"""
	def get_queryset(self):
		journal = self.kwargs.get('journal', None)
		journal = '^' + journal.replace('-', ' ') + '$'
		return Articles.objects.filter(container_title__iregex=journal).order_by('-article_id')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class AllArticleViewSet(generics.ListAPIView):
	"""
	List all articles 
	"""
	pagination_class = None
	queryset = Articles.objects.all().order_by('-article_id')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class RelevantList(generics.ListAPIView):
	"""
	List relevant articles, by manual selection and Machine Learning using the Gausian Naive Bayes Model.
	"""
	model = Articles
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.filter(Q(relevant=True) | Q(ml_prediction_gnb=True)).order_by('-article_id')

class UnsentList(generics.ListAPIView):
	"""
	Lists the articles that have not been sent to subscribers
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.all().exclude(sent_to_subscribers = True)

class newsletterByWeek(viewsets.ModelViewSet):
	"""
	Search relevant articles. /articles/relevant/week/{year}/{week}/.
	For a given week number, returns articles flagged as relevant by the admin team or the Machine Learning models.
	"""
	def get_queryset(self):
		p_week = self.kwargs.get('week')
		p_year = self.kwargs.get('year')
		week = getDateRangeFromWeek(p_year=p_year,p_week=p_week)
		articles = Articles.objects.filter(Q(discovery_date__gte=week[0].astimezone(),discovery_date__lte=week[1].astimezone())).filter(Q(ml_prediction_gnb=True) | Q(relevant=True))
		return articles

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class lastXdays(viewsets.ModelViewSet):
	"""
	Search relevant articles. /articles/relevant/last/{days}/.
	For a given number of days, returns articles flagged as relevant by the admin team or the Machine Learning models.
	"""
	def get_queryset(self):
		days_to_subtract = self.kwargs.get('days', None)
		days = datetime.today() - timedelta(days=days_to_subtract)
		articles = Articles.objects.filter(Q(discovery_date__gte=days.astimezone())).filter(Q(ml_prediction_gnb=True) | Q(relevant=True))
		return articles

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
class ArticlesBySourceList(generics.ListAPIView):
	"""
	Lists the articles that come from the specified source_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		source = self.kwargs['source']
		return Articles.objects.filter(source=source)

class ArticlesByAuthorList(generics.ListAPIView):
	"""
	Lists the articles that include the specified author_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		author = self.kwargs['author']
		return Articles.objects.filter(authors=author)

# class ArticleRelevant(generics.RetrieveUpdateAPIView):
# 	"""
# 	Change the value of relevancy of the article
# 	"""
# 	http_method_names = ['get','put']
# 	serializer_class = ArticleSerializer
# 	lookup_field = 'article_id'
# 	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
# 	def get_queryset(self):
# 		return Articles.objects.filter(article_id = self.kwargs['article_id'])

# 	def update(self,request,article_id=None,*args, **kwargs):
# 		instance = self.get_object()
# 		value = self.request.data.get("value", None)  # read data from request

# 		if value == 1:
# 			value = True
# 		elif value == 0:
# 			value = False
# 		data = {"relevant": value}
# 		serializer = ArticleSerializer(instance,data,partial=True)
# 		if serializer.is_valid():
# 			serializer.save()
# 			return HttpResponse(serializer.data)
# 		else: 
# 			return HttpResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ArticlesByKeyword(generics.ListAPIView):
	"""
	List articles by keyword
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields = ['title','summary']

class ArticlesPredictionNone(generics.ListAPIView):
	"""
	List articles where the Machine Learning prediction is Null and summary length greater than 0 characters.    
	To override the default summary length pass the summary_length argument to the url as `/articles/prediction/none/?summary_length=42` 
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = Articles.objects.annotate(summary_len=Length('summary')).filter( summary_len__gt = 0).filter(ml_prediction_gnb=None)
		summary_length = self.request.query_params.get('summary_length')
		if summary_length is not None:
			queryset = Articles.objects.annotate(summary_len=Length('summary')).filter( summary_len__gt = summary_length).filter(ml_prediction_gnb=None)
		return queryset

class ArticlesCount(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""

	queryset = Articles.objects.raw('select count(*),article_id from articles group by article_id limit 1;')
	serializer_class = CountArticlesSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_classes = None

	# def get_queryset(self):
	# 	return HttpResponse(Articles.objects.count())
	# def list(self, request, *args, **kwargs):
	# 	queryset = self.filter_queryset(self.get_queryset())
	# 	# If you want response all the results, without pagination, 
	# 	# stop calling the self.paginate_queryset method, use queryset directly
	# 	page = self.paginate_queryset(queryset) or queryset
	# 	serializer = self.get_serializer(page, many=True)
	# 	return HttpResponse(json.dumps(serializer.data))

class OpenAccessArticles(generics.ListAPIView):
	"""
	List all articles in the database that are registered as open access on unpaywall.org
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = Articles.objects.filter(access='open')
		return queryset 
	
###
# TRIALS
### 

class TrialViewSet(viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date. Accepts regular expressions in search.
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$title','$summary']

class AllTrialViewSet(generics.ListAPIView):
	"""
	List all clinical trials by discovery date
	"""
	pagination_class = None
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class TrialsBySourceList(generics.ListAPIView):
	serializer_class = ArticleSerializer

	def get_queryset(self):
		"""
		Lists the clinical trials that come from the specified source_id
		"""
		source = self.kwargs['source']
		return Trials.objects.filter(source=source)

# class TrialsByKeyword(generics.ListAPIView):
# 	"""
# 	List clinical trials by keyword
# 	"""
# 	serializer_class = TrialSerializer
# 	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]
# 	filter_backends = [filters.SearchFilter]
# 	search_fields = ['$title','$summary']



###
# SOURCES
### 

class SourceViewSet(viewsets.ModelViewSet):
	"""
	List all sources of data
	"""
	queryset = Sources.objects.all().order_by('name')
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


###
# AUTHORS
### 

class AuthorsViewSet(viewsets.ModelViewSet):
	"""
	List all authors
	"""
	queryset = Authors.objects.all().order_by('author_id')
	serializer_class = AuthorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


