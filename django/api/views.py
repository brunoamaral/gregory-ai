from api.serializers import (
		ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer, AuthorSerializer, 
		CategorySerializer, TeamSerializer, SubjectsSerializer, ArticlesByCategoryAndTeamSerializer
)
from api.pagination import FlexiblePagination
from datetime import datetime, timedelta
from django.db.models import Count, Q, Prefetch
from django.db.models.functions import Length, TruncMonth
from django.shortcuts import get_object_or_404
from gregory.classes import SciencePaper
from gregory.models import Articles, Trials, Sources, Authors, Team, Subject, TeamCategory
from rest_framework import permissions, viewsets, generics, filters, status
from rest_framework.decorators import api_view, action
from django_filters import rest_framework as django_filters
from api.filters import ArticleFilter, TrialFilter, AuthorFilter
from rest_framework.response import Response
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
import json
import traceback
from django.utils.dateparse import parse_date

from api.utils.utils import checkValidAccess, getAPIKey, getIPAddress
from api.models import APIAccessSchemeLog
from api.utils.exceptions import (
		APIAccessDeniedError, APIInvalidAPIKeyError, APIInvalidIPAddressError,
		APINoAPIKeyError, ArticleExistsError, ArticleNotSavedError, DoiNotFound, 
		FieldNotFoundError, SourceNotFoundError
)
from api.utils.responses import (
		ACCESS_DENIED, INVALID_API_KEY, INVALID_IP_ADDRESS, NO_API_KEY,
		UNEXPECTED, SOURCE_NOT_FOUND, FIELD_NOT_FOUND, ARTICLE_EXISTS, ARTICLE_NOT_SAVED, returnData, returnError
)
def getDateRangeFromWeek(p_year,p_week):
	firstdayofweek = datetime.strptime(f'{p_year}-W{int(p_week )- 1}-1', "%Y-W%W-%w")
	lastdayofweek = firstdayofweek + timedelta(days=6.9)
	return (firstdayofweek,lastdayofweek)

# Util function that creates an instance of the access log model
def generateAccessSchemeLog(call_type, ip_addr, access_scheme, http_code, error_message, post_data):
	log = APIAccessSchemeLog()
	log.call_type = call_type
	log.ip_addr = ip_addr
	if access_scheme is not None:
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
		access_scheme = None  # Define access_scheme at the start
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
				for article in article_on_gregory:
					new_source = Sources.objects.get(pk=new_article['source_id'])
					article.sources.add(new_source)
					article.teams.add(new_source.team)
					article.subjects.add(new_source.subject)
				raise ArticleExistsError('There is already an article with the specified DOI. If the source, team, or subject were different, the article was updated.')

			if new_article['title'] != None:
				article_on_gregory = Articles.objects.filter(title=new_article['title'])
			if article_on_gregory != None and article_on_gregory.count() > 0:
				raise ArticleExistsError('There is already an article with the specified Title')

			source = Sources.objects.get(pk=new_article['source_id'])
			if source.pk == None:
				raise SourceNotFoundError('source_id was not found in the database')
			save_article = Articles.objects.create(
				discovery_date=datetime.now(),
				title = new_article['title'],
				summary = new_article['summary'],
				link = new_article['link'],
				published_date = new_article['published_date'], 
				doi = new_article['doi'], kind = new_article['kind'],
				publisher=new_article['publisher'], container_title=new_article['container_title'])
			save_article.sources.add(source)
			save_article.teams.add(source.team)
			save_article.subjects.add(source.subject)

			if save_article.pk == None:
				raise ArticleNotSavedError('Could not create the article')
			save_article.sources.add(source)
			# Prepare some data to be returned to the API client
			data = {
				'name': 'Gregory | API',
				'version': '0.1b',
				"data_received": json.loads(request.body),
				'data_processed_from_doi': new_article,
				'article_id': save_article.article_id,
			}
			log_data = {
				'name': 'Gregory | API',
				'version': '0.1b',
				"article_id": save_article.pk,
			}
			# This creates an access log for this client in the DB
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 201, 'Article created', log_data)
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
			if access_scheme is not None:
					generateAccessSchemeLog(call_type, ip_addr, access_scheme, 403, str(exception), str(post_data))
			else:
					generateAccessSchemeLog(call_type, ip_addr, None, 403, str(exception), str(post_data))
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
	List all articles in the database by earliest discovery_date.
	CSV responses are automatically streamed for better performance with large datasets.
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
	search_fields  = ['$title','$summary']
	filterset_class = ArticleFilter

class RelatedArticles(viewsets.ModelViewSet):
	"""
	Search related articles by the noun_phrases field. This search accepts regular expressions such as /articles/related/?search=<noun_phrase>|<noun_phrase>
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$noun_phrases']


class ArticlesByCategory(viewsets.ModelViewSet):
	"""
	Search articles by the category field. Usage /articles/category/{{category_slug}}/
	"""
	def get_queryset(self):
			category_slug = self.kwargs.get('category_slug', None)
			category = TeamCategory.objects.filter(category_slug=category_slug).first()

			if category is None:
				# Returning an empty queryset
				return Articles.objects.none()

			return Articles.objects.filter(team_categories=category).order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class ArticlesByTeam(viewsets.ModelViewSet):
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Articles.objects.filter(teams__id=team_id).order_by('-discovery_date')

class ArticlesBySubject(viewsets.ModelViewSet):
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		subject_id = self.kwargs.get('subject_id')
		return Articles.objects.filter(subjects__id=subject_id, teams=team_id).order_by('-discovery_date')
class ArticlesByJournal(viewsets.ModelViewSet):
	"""
	Search articles by the journal field. Usage /articles/journal/{{journal}}/.
	Journal should be lower case and spaces should be replaced by dashes, for example: 	"The Lancet Neurology" becomes the-lancet-neurology.
	"""
	def get_queryset(self):
		journal_slug = self.kwargs.get('journal_slug', None)
		journal_slug = '^' + journal_slug.replace('-', ' ') + '$'
		return Articles.objects.filter(container_title__iregex=journal_slug).order_by('-discovery_date')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class AllArticleViewSet(generics.ListAPIView):
	"""
	List all articles 
	"""
	pagination_class = None
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class RelevantList(generics.ListAPIView):
	"""
	List relevant articles, by manual selection and Machine Learning predictions.
	"""
	model = Articles
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.filter(
			Q(ml_predictions_detail__predicted_relevant=True) |
			Q(article_subject_relevances__is_relevant=True)
		).distinct().order_by('-discovery_date')

class UnsentList(generics.ListAPIView):
	"""
	Lists the articles that have not been sent to subscribers
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.all().exclude(sent_to_subscribers = True).order_by('-discovery_date')

class newsletterByWeek(viewsets.ModelViewSet):
	"""
	Search relevant articles. /articles/relevant/week/{year}/{week}/.
	For a given week number, returns articles flagged as relevant by the admin team or the Machine Learning models.
	"""
	def get_queryset(self):
		p_week = self.kwargs.get('week')
		p_year = self.kwargs.get('year')
		week = getDateRangeFromWeek(p_year=p_year,p_week=p_week)
		articles = Articles.objects.filter(
			Q(discovery_date__gte=week[0].astimezone(),discovery_date__lte=week[1].astimezone())
		).filter(
			Q(ml_predictions_detail__predicted_relevant=True) | 
			Q(article_subject_relevances__is_relevant=True)
		).distinct().order_by('-discovery_date')
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
		articles = Articles.objects.filter(
			Q(discovery_date__gte=days.astimezone())
		).filter(
			Q(ml_predictions_detail__predicted_relevant=True) | 
			Q(article_subject_relevances__is_relevant=True)
		).distinct().order_by('-discovery_date')
		return articles

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
class ArticlesBySource(viewsets.ModelViewSet):
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		source_id = self.kwargs.get('source_id')
		return Articles.objects.filter(teams__id=team_id, sources__source_id=source_id).order_by('-discovery_date')

class ArticlesByAuthorList(generics.ListAPIView):
	"""
	Lists the articles that include the specified author_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		author_id = self.kwargs['author_id']
		return Articles.objects.filter(authors=author_id).order_by('-published_date')

class ArticlesByKeyword(generics.ListAPIView):
	"""
	List articles by keyword
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields = ['title','summary']
	
	def get_queryset(self):
		return Articles.objects.all().order_by('-discovery_date')

class ArticlesPredictionNone(generics.ListAPIView):
	"""
	List articles where the Machine Learning prediction is Null and summary length greater than 0 characters.    
	To override the default summary length pass the summary_length argument to the url as `/articles/prediction/none/?summary_length=42` 
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = Articles.objects.annotate(summary_len=Length('summary')).filter(summary_len__gt=0).exclude(
			ml_predictions_detail__isnull=False
		).order_by('-discovery_date')
		summary_length = self.request.query_params.get('summary_length')
		if summary_length is not None:
			queryset = Articles.objects.annotate(summary_len=Length('summary')).filter(summary_len__gt=summary_length).exclude(
				ml_predictions_detail__isnull=False
			).order_by('-discovery_date')
		return queryset

class ArticlesCount(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""

	queryset = Articles.objects.raw('select count(*),article_id from articles group by article_id limit 1;')
	serializer_class = CountArticlesSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_classes = None


class OpenAccessArticles(generics.ListAPIView):
	"""
	List all articles in the database that are registered as open access on unpaywall.org
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = Articles.objects.filter(access='open').order_by('-discovery_date')
		return queryset
	
###
# CATEGORIES
###

class CategoryViewSet(viewsets.ModelViewSet):
	"""
	List all categories in the database.
	"""
	queryset = TeamCategory.objects.all()
	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	
	def get_queryset(self):
		return TeamCategory.objects.annotate(
			article_count_annotated=Count('articles', distinct=True),
			trials_count_annotated=Count('trials', distinct=True)
		).all()

class MonthlyCountsView(APIView):
	def get(self, request, team_id, category_slug):
			team_category = get_object_or_404(TeamCategory, team__id=team_id, category_slug=category_slug)
			
			# Monthly article counts
			articles = Articles.objects.filter(team_categories=team_category)
			articles = articles.annotate(month=TruncMonth('published_date'))
			article_counts = articles.values('month').annotate(count=Count('article_id')).order_by('month')
			article_counts = list(article_counts.values('month', 'count'))

			# Monthly trial counts
			trials = Trials.objects.filter(team_categories=team_category)
			trials = trials.annotate(month=TruncMonth('published_date'))
			trial_counts = trials.values('month').annotate(count=Count('trial_id')).order_by('month')
			trial_counts = list(trial_counts.values('month', 'count'))

			data = {
					'category_name': team_category.category_name,
					'category_slug': team_category.category_slug,
					'monthly_article_counts': article_counts,
					'monthly_trial_counts': trial_counts,
			}

			return Response(data)

###
# TRIALS
### 

class TrialViewSet(viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date. Accepts regular expressions in search.
	CSV responses are automatically streamed for better performance with large datasets.
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
	search_fields  = ['$title','$summary']
	filterset_class = TrialFilter

class AllTrialViewSet(generics.ListAPIView):
	"""
	List all clinical trials by discovery date
	"""
	pagination_class = None
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class TrialsBySource(generics.ListAPIView):
	serializer_class = TrialSerializer

	def get_queryset(self):
		"""
		Lists the clinical trials that come from the specified source_id
		"""
		team_id = self.kwargs['team_id']
		source_id = self.kwargs['source_id']
		return Trials.objects.filter(teams__id=team_id, source__source_id=source_id).order_by('-discovery_date')

class TrialsByCategory(viewsets.ModelViewSet):
	"""
	Search Trials by the category field. Usage /trials/category/{{category_slug}}/
	"""
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
			team_id = self.kwargs['team_id']
			category_slug = self.kwargs.get('category_slug', None)
			category = get_object_or_404(TeamCategory, category_slug=category_slug, team_id=team_id)

			return Trials.objects.filter(teams=team_id, team_categories=category).order_by('-discovery_date')

class TrialsBySubject(viewsets.ModelViewSet):
	"""
	Search Trials by the subject field and team ID. Usage /teams/<team_id>/trials/subject/<subject_id>/
	"""
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs['team_id']
		subject_id = self.kwargs['subject_id']
		get_object_or_404(Subject, id=subject_id, team_id=team_id)

		return Trials.objects.filter(teams=team_id, subjects=subject_id).order_by('-discovery_date')


###
# SOURCES
### 

class SourceViewSet(viewsets.ModelViewSet):
	"""
	List all sources of data with optional filters for team and subject.
	"""
	queryset = Sources.objects.all().order_by('name')
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = super().get_queryset()
		team_id = self.request.query_params.get('team_id')
		subject_id = self.request.query_params.get('subject_id')

		if team_id:
			queryset = queryset.filter(team__id=team_id)
		if subject_id:
			queryset = queryset.filter(subject__id=subject_id)

		return queryset

###
# AUTHORS
### 

class AuthorsViewSet(viewsets.ModelViewSet):
	"""
	Enhanced Authors API with sorting and filtering capabilities.
	
	**Query Parameters:**
	
	* **sort_by**: 'article_count' (default: 'author_id')
	* **order**: 'asc' or 'desc' (default: 'desc' for article_count, 'asc' for others)
	* **team_id**: filter by team ID
	* **subject_id**: filter by subject ID
	* **category_slug**: filter by team category slug
	* **date_from**: filter articles from this date (YYYY-MM-DD)
	* **date_to**: filter articles to this date (YYYY-MM-DD)
	* **timeframe**: 'year', 'month', 'week' (relative to current date)
	
	**Examples:**
	
	* Sort by article count: `?sort_by=article_count&order=desc`
	* Filter by timeframe: `?sort_by=article_count&timeframe=year`
	* Team and subject filter: `?team_id=1&subject_id=5&sort_by=article_count`
	* Count per category: `?team_id=1&category_slug=natalizumab&sort_by=article_count&order=desc`
	* Category with timeframe: `?team_id=1&category_slug=natalizumab&timeframe=year&sort_by=article_count`
	* Date range: `?date_from=2024-06-01&date_to=2024-12-31&team_id=1&subject_id=1&sort_by=article_count`
	"""
	serializer_class = AuthorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	
	def get_queryset(self):
		queryset = Authors.objects.all()
		
		# Get query parameters
		sort_by = self.request.query_params.get('sort_by', 'author_id')
		order = self.request.query_params.get('order', 'desc' if sort_by == 'article_count' else 'asc')
		team_id = self.request.query_params.get('team_id')
		subject_id = self.request.query_params.get('subject_id')
		category_slug = self.request.query_params.get('category_slug')
		date_from = self.request.query_params.get('date_from')
		date_to = self.request.query_params.get('date_to')
		timeframe = self.request.query_params.get('timeframe')
		
		# Build date filter for articles
		date_filters = {}
		
		if timeframe:
			now = datetime.now()
			if timeframe == 'year':
				date_from = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
			elif timeframe == 'month':
				date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
			elif timeframe == 'week':
				# Get Monday of current week
				days_since_monday = now.weekday()
				date_from = now - timedelta(days=days_since_monday)
				date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
		
		if date_from:
			try:
				if isinstance(date_from, str):
					date_from = parse_date(date_from) or datetime.strptime(date_from, '%Y-%m-%d').date()
				date_filters['published_date__gte'] = date_from
			except (ValueError, TypeError):
				pass
		
		if date_to:
			try:
				if isinstance(date_to, str):
					date_to = parse_date(date_to) or datetime.strptime(date_to, '%Y-%m-%d').date()
				date_filters['published_date__lte'] = date_to
			except (ValueError, TypeError):
				pass
		
		# Apply team/subject/category filters
		author_filters = {}  # Used for filtering Articles to get author IDs
		count_filters = {}   # Used for Count annotation on Authors queryset
		
		# Validate that team_id is provided when using subject_id or category_slug
		if (subject_id or category_slug) and not team_id:
			# Return empty queryset if team_id is missing for subject/category filtering
			return Authors.objects.none()
		
		if team_id:
			try:
				team_id = int(team_id)
				author_filters['teams__id'] = team_id
				count_filters['articles__teams__id'] = team_id
			except ValueError:
				pass
		
		if subject_id:
			try:
				subject_id = int(subject_id)
				author_filters['subjects__id'] = subject_id
				count_filters['articles__subjects__id'] = subject_id
			except ValueError:
				pass
		
		if category_slug:
			author_filters['team_categories__category_slug'] = category_slug
			count_filters['articles__team_categories__category_slug'] = category_slug
		
		# Add date filters to both author and count filters
		author_filters.update(date_filters)
		# For count filters, we need to add the articles__ prefix to date filters
		count_date_filters = {f'articles__{k}': v for k, v in date_filters.items()}
		count_filters.update(count_date_filters)
		
		# Filter authors if any filters are applied
		if author_filters:
			author_ids = Articles.objects.filter(**author_filters).values_list('authors', flat=True).distinct()
			queryset = queryset.filter(author_id__in=author_ids)
		
		# Add article count annotation for sorting
		if sort_by == 'article_count':
			if count_filters:
				queryset = queryset.annotate(
					article_count=Count('articles', filter=Q(**count_filters), distinct=True)
				)
			else:
				queryset = queryset.annotate(
					article_count=Count('articles', distinct=True)
				)
		
		# Apply sorting
		if sort_by == 'article_count':
			order_prefix = '-' if order == 'desc' else ''
			queryset = queryset.order_by(f'{order_prefix}article_count', 'author_id')
		else:
			# Default sorting by author_id or other fields
			order_prefix = '-' if order == 'desc' else ''
			queryset = queryset.order_by(f'{order_prefix}{sort_by}')
		
		return queryset.distinct()
	
	@action(detail=False, methods=['get'])
	def by_team_subject(self, request):
		"""
		Get authors filtered by team and subject with article counts
		
		Parameters:
		- team_id (required): Team ID
		- subject_id (required): Subject ID
		- Additional filters from main queryset apply
		"""
		team_id = request.query_params.get('team_id')
		subject_id = request.query_params.get('subject_id')
		
		if not team_id or not subject_id:
			return Response(
				{"error": "Both team_id and subject_id are required"}, 
				status=status.HTTP_400_BAD_REQUEST
			)
		
		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)
		
		if page is not None:
			serializer = self.get_serializer(page, many=True)
			return self.get_paginated_response(serializer.data)
		
		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)
	
	@action(detail=False, methods=['get'])
	def by_team_category(self, request):
		"""
		Get authors filtered by team category with article counts
		
		Parameters:
		- team_id (required): Team ID
		- category_slug (required): Team category slug
		- Additional filters from main queryset apply
		"""
		team_id = request.query_params.get('team_id')
		category_slug = request.query_params.get('category_slug')
		
		if not team_id or not category_slug:
			return Response(
				{"error": "Both team_id and category_slug are required"}, 
				status=status.HTTP_400_BAD_REQUEST
			)
		
		queryset = self.filter_queryset(self.get_queryset())
		page = self.paginate_queryset(queryset)
		
		if page is not None:
			serializer = self.get_serializer(page, many=True)
			return self.get_paginated_response(serializer.data)
		
		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)


###
# AUTHORIZATION
###
# The class below generates a new token at every successful call.
# But that token is not saved in the database and associated with the user.
# is that a problem?

class LoginView(TokenObtainPairView):
	permission_classes = (permissions.AllowAny,)

class ProtectedEndpointView(APIView):
	permission_classes = [permissions.IsAuthenticated]

	def get(self, request):
		return Response({"message": "You have accessed the protected endpoint!"})

###
# TEAMS
###

class TeamsViewSet(viewsets.ModelViewSet):
	"""
	List all teams
	"""
	queryset = Team.objects.all().order_by('id')
	serializer_class = TeamSerializer
	permission_classes  = [permissions.IsAuthenticatedOrReadOnly]

###
# SUBJECTS
###

class SubjectsViewSet(viewsets.ModelViewSet):
	"""
	List all subjects
	"""
	queryset = Subject.objects.all().order_by('id')
	serializer_class = SubjectsSerializer
	permission_classes  = [permissions.IsAuthenticatedOrReadOnly]

class ArticlesByTeam(viewsets.ModelViewSet):
		"""
		List all articles for a specific team by ID
		"""
		serializer_class = ArticleSerializer
		permission_classes = [permissions.IsAuthenticatedOrReadOnly]

		def get_queryset(self):
				team_id = self.kwargs.get('team_id')
				return Articles.objects.filter(teams__id=team_id).order_by('-discovery_date')
class TrialsByTeam(viewsets.ModelViewSet):
	"""
	List all clinical trials for a specific team by ID
	"""
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Trials.objects.filter(teams__id=team_id).order_by('-discovery_date')

class SubjectsByTeam(viewsets.ModelViewSet):
	"""
	List all research subjects for a specific team by ID
	"""
	serializer_class = SubjectsSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Subject.objects.filter(team__id=team_id).order_by('-id')

class SourcesByTeam(viewsets.ModelViewSet):
	"""
	List all sources for a specific team by ID
	"""
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Sources.objects.filter(team__id=team_id).order_by('-source_id')

class CategoriesByTeam(viewsets.ModelViewSet):
	"""
	List all categories for a specific team by ID
	"""
	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return TeamCategory.objects.filter(team__id=team_id).annotate(
			article_count_annotated=Count('articles', distinct=True),
			trials_count_annotated=Count('trials', distinct=True)
		).order_by('-id')

class CategoriesByTeamAndSubject(viewsets.ModelViewSet):
	"""
	List all categories for a specific team and subject combination
	"""
	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		subject_id = self.kwargs.get('subject_id')
		return TeamCategory.objects.filter(
			team__id=team_id,
			subjects__id=subject_id
		).annotate(
			article_count_annotated=Count('articles', distinct=True),
			trials_count_annotated=Count('trials', distinct=True)
		).order_by('-id')

class ArticlesByCategoryAndTeam(viewsets.ModelViewSet):
		"""
		List all articles for a specific category and team.
		"""
		serializer_class = ArticleSerializer
		permission_classes = [permissions.IsAuthenticatedOrReadOnly]

		def get_queryset(self):
				team_id = self.kwargs.get('team_id')
				category_slug = self.kwargs.get('category_slug')
				team_category = get_object_or_404(TeamCategory, team__id=team_id, category_slug=category_slug)
				return Articles.objects.filter(team_categories=team_category).prefetch_related(
						'team_categories', 'sources', 'authors', 'teams', 'subjects', 'ml_predictions'
				).order_by('-discovery_date')

class ArticleSearchView(generics.ListAPIView):
    """
    Advanced search for articles by title and abstract (summary).
    
    This endpoint accepts both GET and POST requests with team_id and subject_id parameters, 
    along with optional search parameters.
    
    Parameters (can be sent as query params for GET or in request body for POST):
    - title: Search only in title field
    - summary: Search only in summary/abstract field
    - search: Search in both title and summary fields
    - team_id: Required - Team ID to filter articles by (must be provided)
    - subject_id: Required - Subject ID to filter articles by (must be provided)
    - page: Page number for pagination (default: 1)
    - page_size: Number of results per page (default: 10, max: 100)
    - all_results: Set to 'true' to retrieve all results without pagination (useful for CSV export)
    
    Results are ordered by discovery date (newest first).
    
    To download all search results as CSV, add format=csv and all_results=true to the query parameters.
    Example: /articles/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
    """
    serializer_class = ArticleSerializer
    permission_classes = [permissions.AllowAny]  # Allow access to anyone since we require team_id and subject_id
    filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
    filterset_class = ArticleFilter
    search_fields = ['title', 'summary']
    pagination_class = FlexiblePagination
    http_method_names = ['get', 'post']  # Support both GET and POST
    
    def get_queryset(self):
        # This method handles both GET and POST requests
        if self.request.method == 'GET':
            params = self.request.query_params
        else:
            params = self.request.data
            
        # Extract required parameters
        team_id = params.get('team_id')
        subject_id = params.get('subject_id')
        
        # Validate required parameters
        if not team_id or not subject_id:
            return Articles.objects.none()
        
        try:
            # Start with articles filtered by team and subject
            # Use distinct('article_id') to eliminate duplicates from many-to-many relationships
            queryset = Articles.objects.filter(
                teams__id=team_id, 
                subjects__id=subject_id
            ).distinct('article_id').order_by('article_id', '-discovery_date')
            
            # Note: When using distinct('article_id'), the first field in order_by MUST be article_id
            # Then we can add '-discovery_date' to get the newest article for each unique article_id
            
            # Apply additional filters
            title = params.get('title')
            summary = params.get('summary')
            search = params.get('search')
            
            if title:
                queryset = queryset.filter(utitle__contains=title.upper())
            if summary:
                queryset = queryset.filter(usummary__contains=summary.upper())
            if search:
                upper_search = search.upper()
                queryset = queryset.filter(
                    Q(utitle__contains=upper_search) | Q(usummary__contains=upper_search)
                )
                
            return queryset
        except:
            return Articles.objects.none()
    
    def post(self, request, *args, **kwargs):
        # For POST requests, delegate to the list method which uses get_queryset
        return self.list(request, *args, **kwargs)
        
    def get(self, request, *args, **kwargs):
        # Validate required parameters for GET requests
        team_id = request.query_params.get('team_id')
        subject_id = request.query_params.get('subject_id')
        
        if not team_id or not subject_id:
            return Response(
                {"error": "Missing required parameters: team_id, subject_id"}, 
                status=400
            )
            
        try:
            # Check if team and subject exist
            Team.objects.get(id=team_id)
            Subject.objects.get(id=subject_id, team_id=team_id)
        except Team.DoesNotExist:
            return Response(
                {"error": f"Team with ID {team_id} not found"}, 
                status=404
            )
        except Subject.DoesNotExist:
            return Response(
                {"error": f"Subject with ID {subject_id} not found or does not belong to team {team_id}"}, 
                status=404
            )
            
        # Delegate to the list method
        return self.list(request, *args, **kwargs)

class TrialSearchView(generics.ListAPIView):
    """
    Advanced search for clinical trials by title, summary, and recruitment status.
    
    This endpoint accepts both GET and POST requests with team_id and subject_id parameters, 
    along with optional search parameters.
    
    Parameters (can be sent as query params for GET or in request body for POST):
    - title: Search only in title field
    - summary: Search only in summary/abstract field
    - search: Search in both title and summary fields
    - status: Filter by recruitment status (e.g., 'Recruiting', 'Completed')
    - team_id: Required - Team ID to filter trials by (must be provided)
    - subject_id: Required - Subject ID to filter trials by (must be provided)
    - page: Page number for pagination (default: 1)
    - page_size: Number of results per page (default: 10, max: 100)
    - all_results: Set to 'true' to retrieve all results without pagination (useful for CSV export)
    
    Results are ordered by discovery date (newest first).
	
    To download all search results as CSV, add format=csv and all_results=true to the query parameters.
    Example: /trials/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
    """
    serializer_class = TrialSerializer
    permission_classes = [permissions.AllowAny]  # Allow access to anyone since we require team_id and subject_id
    filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
    filterset_class = TrialFilter
    search_fields = ['title', 'summary']
    pagination_class = FlexiblePagination
    http_method_names = ['get', 'post']  # Support both GET and POST
    
    def get_queryset(self):
        # This method handles both GET and POST requests
        if self.request.method == 'GET':
            params = self.request.query_params
        else:
            params = self.request.data
            
        # Extract required parameters
        team_id = params.get('team_id')
        subject_id = params.get('subject_id')
        
        # Validate required parameters
        if not team_id or not subject_id:
            return Trials.objects.none()
            
        try:
            # Check if team and subject exist
            team = Team.objects.get(id=team_id)
            subject = Subject.objects.get(id=subject_id, team=team)
        except (Team.DoesNotExist, Subject.DoesNotExist):
            return Trials.objects.none()
        
        # Start with trials filtered by team and subject
        # Use distinct('trial_id') to eliminate duplicates from many-to-many relationships
        queryset = Trials.objects.filter(teams=team, subjects=subject).distinct('trial_id').order_by('trial_id', '-discovery_date')
        
        # Apply additional filters
        title = params.get('title')
        summary = params.get('summary')
        search = params.get('search')
        status = params.get('status')
        
        if title:
            queryset = queryset.filter(utitle__contains=title.upper())
        if summary:
            queryset = queryset.filter(usummary__contains=summary.upper())
        if search:
            upper_search = search.upper()
            queryset = queryset.filter(
                Q(utitle__contains=upper_search) | Q(usummary__contains=upper_search)
            )
        if status:
            queryset = queryset.filter(recruitment_status=status)
            
        return queryset
    
    def post(self, request, *args, **kwargs):
        # For POST requests, delegate to the list method which uses get_queryset
        return self.list(request, *args, **kwargs)

class AuthorSearchView(generics.ListAPIView):
    """
    Advanced search for authors by full name.

    Supports both GET and POST requests with required team_id and subject_id
    parameters. Filtering by full_name is case-insensitive and allows partial
    matches. Pagination and CSV export options mirror the article search
    endpoint.
    """
    serializer_class = AuthorSerializer
    permission_classes = [permissions.AllowAny]
    filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend]
    filterset_class = AuthorFilter
    search_fields = ['full_name']
    pagination_class = FlexiblePagination
    http_method_names = ['get', 'post']

    def get_queryset(self):
        params = self.request.query_params if self.request.method == 'GET' else self.request.data

        team_id = params.get('team_id')
        subject_id = params.get('subject_id')

        if not team_id or not subject_id:
            return Authors.objects.none()

        try:
            author_ids = Articles.objects.filter(
                teams__id=team_id,
                subjects__id=subject_id
            ).values_list('authors', flat=True).distinct()

            queryset = Authors.objects.filter(author_id__in=author_ids).order_by('author_id')

            full_name = params.get('full_name')
            if full_name:
                # URL decode the full_name parameter to handle %20 spaces and other encoded characters
                from urllib.parse import unquote
                full_name = unquote(full_name)
                # Use the new full_name database field for more efficient searching
                queryset = queryset.filter(full_name__icontains=full_name)

            return queryset
        except Exception as e:
            # Log the exception for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in AuthorSearchView.get_queryset: {str(e)}")
            return Authors.objects.none()

    def post(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
