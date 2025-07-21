from api.serializers import (
		ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer, AuthorSerializer, 
		CategorySerializer, CategoryTopAuthorSerializer, TeamSerializer, SubjectsSerializer, ArticlesByCategoryAndTeamSerializer
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
from api.filters import ArticleFilter, TrialFilter, AuthorFilter, SourceFilter, CategoryFilter, SubjectFilter
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

def add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint, message=None):
	"""
	Utility function to add deprecation headers to API responses.
	This helps prepare clients for the transition to new endpoints.
	"""
	if message is None:
		message = f'This endpoint is deprecated. Use {replacement_endpoint} instead.'
	
	response['X-Deprecation-Warning'] = message
	response['X-Migration-Guide'] = replacement_endpoint
	response['X-Deprecated-Endpoint'] = deprecated_endpoint
	return response
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
	✅ **PREFERRED ENDPOINT**: This is the main articles endpoint that supports all filtering options.
	
	List all articles in the database with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.
	
	This endpoint replaces the legacy team-based URLs and specific article type endpoints:
	- Instead of `/teams/1/articles/` → use `/articles/?team_id=1`
	- Instead of `/teams/1/articles/subject/4/` → use `/articles/?team_id=1&subject_id=4`
	- Instead of `/articles/relevant/` → use `/articles/?relevant=true`
	- Instead of `/articles/relevant/last/15/` → use `/articles/?relevant=true&last_days=15`
	- Instead of `/articles/relevant/week/2024/52/` → use `/articles/?relevant=true&week=52&year=2024`
	- Instead of `/articles/open-access/` → use `/articles/?open_access=true`
	- Instead of `/articles/unsent/` → use `/articles/?unsent=true`
	
	# Query Parameters:
	- **team_id** - filter by team ID (replaces /teams/{id}/articles/)
	- **subject_id** - filter by subject ID (used with team_id)
	- **author_id** - filter by author ID
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **journal_slug** - filter by journal (convert spaces to dashes)
	- **source_id** - filter by source ID
	- **search** - search in title and summary
	- **ordering** - order results by field (e.g., -published_date, title)
	- **page** - page number for pagination
	- **page_size** - items per page (max 100)
	
	# Special Article Types:
	- **relevant** - filter for relevant articles (true/false)
	- **open_access** - filter for open access articles (true/false)
	- **unsent** - filter for articles not sent to subscribers (true/false)
	- **last_days** - filter for articles from last N days (number)
	- **week** - filter for specific week number (requires year parameter)
	- **year** - year for week filtering (used with week parameter)
	
	# Examples:
	- Team articles: `/articles/?team_id=1`
	- Team + subject: `/articles/?team_id=1&subject_id=4`
	- With search: `/articles/?team_id=1&search=stem+cells`
	- Category by slug: `/articles/?team_id=1&category_slug=natalizumab`
	- Category by ID: `/articles/?team_id=1&category_id=5`
	- Relevant articles: `/articles/?relevant=true`
	- Relevant from last 15 days: `/articles/?relevant=true&last_days=15`
	- Relevant from specific week: `/articles/?relevant=true&week=52&year=2024`
	- Open access articles: `/articles/?open_access=true`
	- Unsent articles: `/articles/?unsent=true`
	- Complex filter: `/articles/?team_id=1&subject_id=4&author_id=123&search=regeneration&relevant=true&ordering=-published_date`
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = ArticleFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
	ordering = ['-discovery_date']

class RelatedArticles(viewsets.ModelViewSet):
	"""
	Search related articles by the noun_phrases field. This search accepts regular expressions such as /articles/related/?search=<noun_phrase>|<noun_phrase>
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$noun_phrases']


class AllArticleViewSet(generics.ListAPIView):
	"""
	List all articles 
	"""
	pagination_class = None
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class ArticlesBySource(viewsets.ModelViewSet):
	"""
	⚠️ DEPRECATED: This endpoint will be removed in a future version.
	Please use /articles/?team_id={team_id}&source_id={source_id} instead.
	
	List all articles for a specific team and source combination.
	
	Migration Path:
	- Old: GET /teams/1/articles/source/123/
	- New: GET /articles/?team_id=1&source_id=123
	"""
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		source_id = self.kwargs.get('source_id')
		return Articles.objects.filter(teams__id=team_id, sources__source_id=source_id).order_by('-discovery_date')
	
	def list(self, request, *args, **kwargs):
		"""Override list to add deprecation warning header"""
		response = super().list(request, *args, **kwargs)
		team_id = self.kwargs.get('team_id')
		source_id = self.kwargs.get('source_id')
		deprecated_endpoint = f'/teams/{team_id}/articles/source/{source_id}/'
		replacement_endpoint = f'/articles/?team_id={team_id}&source_id={source_id}'
		return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)

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


###
# CATEGORIES
###

class CategoryViewSet(viewsets.ModelViewSet):
	"""
	List all categories in the database with optional filters for team and subject.
	Now includes author statistics for each category.
	
	# Query Parameters:
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID  
	- **category_id** - filter by specific category ID
	- **include_authors** - Include top authors data (default: true)
	- **max_authors** - Maximum number of top authors to return per category (default: 10, max: 50)
	- **date_from** - Filter articles from this date (YYYY-MM-DD)
	- **date_to** - Filter articles to this date (YYYY-MM-DD)
	- **timeframe** - 'year', 'month', 'week' (relative to current date)
	- **monthly_counts** - Include monthly article/trial counts with ML predictions (default: false)
	- **ml_threshold** - ML prediction probability threshold when monthly_counts=true (0.0-1.0, default: 0.5)
	
	# Response includes:
	- Category basic information
	- Total article and trial counts  
	- Authors count (unique authors in category)
	- Top authors with their article counts in this category
	- Monthly counts (when monthly_counts=true)
	
	# Additional Actions:
	- `/categories/{id}/authors/` - Get detailed author statistics for a specific category
	
	# Examples:
	- Basic: `GET /categories/?team_id=1`
	- With subject: `GET /categories/?team_id=1&subject_id=2`
	- Date filtered: `GET /categories/?team_id=1&timeframe=year`
	- More authors: `GET /categories/?team_id=1&max_authors=20`
	- Without authors: `GET /categories/?team_id=1&include_authors=false`
	- Monthly counts: `GET /categories/?category_id=6&monthly_counts=true&ml_threshold=0.8`
	- Single category with monthly counts: `GET /categories/?category_id=6&monthly_counts=true`
	"""
	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = CategoryFilter
	search_fields = ['category_name', 'category_description']
	ordering_fields = ['category_name', 'id', 'article_count_annotated', 'authors_count_annotated']
	ordering = ['category_name']
	
	def get_queryset(self):
		"""
		Optimized queryset that avoids complex COUNT annotations which cause hanging queries.
		
		Instead of using expensive Count() annotations with multiple JOINs, we:
		1. Use simple filtering and prefetch_related for efficiency
		2. Calculate counts in the serializer using prefetched data when possible
		3. Use select_related for foreign keys
		"""
		queryset = TeamCategory.objects.all()
		
		# Apply filters without expensive annotations
		team_id = self.request.query_params.get('team_id')
		subject_id = self.request.query_params.get('subject_id')
		category_id = self.request.query_params.get('category_id')
		
		if team_id:
			queryset = queryset.filter(team_id=team_id)
		
		if subject_id:
			queryset = queryset.filter(subjects__id=subject_id)
			
		if category_id:
			queryset = queryset.filter(id=category_id)
		
		# Use efficient prefetching instead of annotations
		# This avoids the complex GROUP BY queries that are hanging the database
		queryset = queryset.select_related('team').prefetch_related(
			'subjects',
			# Only prefetch essential fields to reduce memory usage
			Prefetch(
				'articles',
				queryset=Articles.objects.select_related().only(
					'article_id', 'title', 'published_date', 'discovery_date'
				)
			),
			Prefetch(
				'trials', 
				queryset=Trials.objects.select_related().only(
					'trial_id', 'title', 'published_date', 'discovery_date'
				)
			)
		)
		
		return queryset.distinct()
	
	def get_serializer_context(self):
		"""Add author parameters to serializer context"""
		context = super().get_serializer_context()
		
		# Get query parameters for author data
		include_authors = self.request.query_params.get('include_authors', 'true').lower() == 'true'
		try:
			max_authors = min(int(self.request.query_params.get('max_authors', 10)), 50)
		except (ValueError, TypeError):
			max_authors = 10
		
		# Get monthly counts parameter
		monthly_counts = self.request.query_params.get('monthly_counts', 'false').lower() == 'true'
		ml_threshold = self.request.query_params.get('ml_threshold', 0.5)
		try:
			ml_threshold = float(ml_threshold)
		except (ValueError, TypeError):
			ml_threshold = 0.5
		
		date_filters = self._build_date_filters(
			self.request.query_params.get('date_from'),
			self.request.query_params.get('date_to'),
			self.request.query_params.get('timeframe')
		)
		
		context['author_params'] = {
			'include_authors': include_authors,
			'max_authors': max_authors,
			'date_filters': date_filters
		}
		
		context['monthly_counts_params'] = {
			'include_monthly_counts': monthly_counts,
			'ml_threshold': ml_threshold
		}
		
		return context
	
	def _build_date_filters(self, date_from, date_to, timeframe):
		"""Build date filters for articles"""
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
				date_filters['articles__published_date__gte'] = date_from
			except (ValueError, TypeError):
				pass
		
		if date_to:
			try:
				if isinstance(date_to, str):
					date_to = parse_date(date_to) or datetime.strptime(date_to, '%Y-%m-%d').date()
				date_filters['articles__published_date__lte'] = date_to
			except (ValueError, TypeError):
				pass
		
		return date_filters
	
	
	@action(detail=True, methods=['get'])
	def authors(self, request, pk=None):
		"""
		Get detailed author statistics for a specific category.
		
		# Query Parameters:
		- **min_articles** - Minimum articles per author (default: 1)
		- **sort_by** - 'articles_count', 'author_name' (default: 'articles_count')
		- **order** - 'asc', 'desc' (default: 'desc')
		- Date filtering parameters (same as main endpoint)
		
		**URL:** `/categories/{id}/authors/`
		"""
		category = self.get_object()
		
		# Get query parameters
		try:
			min_articles = int(request.query_params.get('min_articles', 1))
		except (ValueError, TypeError):
			min_articles = 1
		sort_by = request.query_params.get('sort_by', 'articles_count')
		order = request.query_params.get('order', 'desc')
		date_from = request.query_params.get('date_from')
		date_to = request.query_params.get('date_to')
		timeframe = request.query_params.get('timeframe')
		
		# Build date filters
		date_filters = self._build_date_filters(date_from, date_to, timeframe)
		
		# Build filter for articles in this category
		article_filter = Q(team_categories=category)
		if date_filters:
			# Adjust the date filter keys for the Articles model
			articles_date_filters = {}
			for key, value in date_filters.items():
				if key.startswith('articles__'):
					articles_date_filters[key.replace('articles__', '')] = value
			article_filter &= Q(**articles_date_filters)
		
		# Get authors with article counts in this category
		authors_queryset = Authors.objects.filter(
			articles__team_categories=category
		).annotate(
			category_articles_count=Count(
				'articles', 
				filter=Q(articles__team_categories=category) & 
				       Q(**{f'articles__{k}': v for k, v in date_filters.items() if k.startswith('articles__')}),
				distinct=True
			)
		).filter(
			category_articles_count__gte=min_articles
		)
		
		# Apply sorting
		if sort_by == 'articles_count':
			order_prefix = '-' if order == 'desc' else ''
			authors_queryset = authors_queryset.order_by(f'{order_prefix}category_articles_count', 'author_id')
		elif sort_by == 'author_name':
			order_prefix = '-' if order == 'desc' else ''
			authors_queryset = authors_queryset.order_by(f'{order_prefix}full_name', 'author_id')
		else:
			authors_queryset = authors_queryset.order_by('-category_articles_count', 'author_id')
		
		# Paginate results
		page = self.paginate_queryset(authors_queryset)
		if page is not None:
			serializer = CategoryTopAuthorSerializer(page, many=True)
			return self.get_paginated_response(serializer.data)
		
		serializer = CategoryTopAuthorSerializer(authors_queryset, many=True)
		return Response(serializer.data)

class MonthlyCountsView(APIView):
	"""
	Get monthly counts of articles and trials for a specific team category.
	
	# Query Parameters:
	- **ml_threshold** - ML prediction probability threshold (0.0-1.0, default: 0.5)
	  - Returns count of articles with ML predictions above this threshold for each model
	
	# Returns:
	- `monthly_article_counts` - Total articles by month
	- `monthly_ml_article_counts_by_model` - Articles with ML predictions >= threshold by month for each model
	- `monthly_trial_counts` - Total trials by month
	- `ml_threshold` - The threshold value used for ML filtering
	- `available_models` - List of ML models found in the data
	
	# Examples:
	- Default threshold: `/teams/1/categories/natalizumab/monthly_counts/`
	- Custom threshold: `/teams/1/categories/natalizumab/monthly_counts/?ml_threshold=0.8`
	"""
	def get(self, request, team_id, category_slug):
		"""Optimized monthly counts to avoid complex ML prediction queries"""
		team_category = get_object_or_404(TeamCategory, team__id=team_id, category_slug=category_slug)
		
		# Get ML prediction threshold parameter (default to 0.5 if not provided)
		ml_threshold = request.query_params.get('ml_threshold', 0.5)
		try:
			ml_threshold = float(ml_threshold)
		except (ValueError, TypeError):
			ml_threshold = 0.5
		
		# Monthly article counts (simple and fast)
		articles = Articles.objects.filter(team_categories=team_category)
		articles = articles.annotate(month=TruncMonth('published_date'))
		article_counts = articles.values('month').annotate(count=Count('article_id')).order_by('month')
		article_counts = list(article_counts.values('month', 'count'))

		# Get available ML models using a simpler approach
		from gregory.models import MLPredictions
		from django.db.models import Max
		
		# Get distinct algorithms with a simple query
		available_models = MLPredictions.objects.filter(
			article__team_categories=team_category
		).values_list('algorithm', flat=True).distinct()
		available_models = list(available_models)
		
		# Optimized ML counts - avoid complex nested subqueries
		ml_counts_by_model = {}
		for model in available_models:
			# Simplified approach: get articles with ML predictions above threshold for this model
			# Use a direct query instead of complex subqueries
			article_ids_with_ml = MLPredictions.objects.filter(
				article__team_categories=team_category,
				algorithm=model,
				probability_score__gte=ml_threshold
			).values_list('article_id', flat=True).distinct()
			
			# Get monthly counts for these articles using a simple query
			articles_with_ml = Articles.objects.filter(
				article_id__in=list(article_ids_with_ml),  # Convert to list to avoid subquery
				team_categories=team_category
			).annotate(month=TruncMonth('published_date'))
			
			ml_article_counts = articles_with_ml.values('month').annotate(count=Count('article_id', distinct=True)).order_by('month')
			ml_counts_by_model[model] = list(ml_article_counts.values('month', 'count'))

		# Monthly trial counts (simple and fast)
		trials = Trials.objects.filter(team_categories=team_category)
		trials = trials.annotate(month=TruncMonth('published_date'))
		trial_counts = trials.values('month').annotate(count=Count('trial_id')).order_by('month')
		trial_counts = list(trial_counts.values('month', 'count'))

		data = {
				'category_name': team_category.category_name,
				'category_slug': team_category.category_slug,
				'ml_threshold': ml_threshold,
				'available_models': available_models,
				'monthly_article_counts': article_counts,
				'monthly_ml_article_counts_by_model': ml_counts_by_model,
				'monthly_trial_counts': trial_counts,
		}

		return Response(data)

###
# TRIALS
### 

class TrialViewSet(viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.
	
	# Core Query Parameters:
	- **trial_id** - filter by specific trial ID
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **source_id** - filter by source ID
	- **status/recruitment_status** - filter by recruitment status
	- **search** - search in title and summary
	
	# Trial-Specific Parameters:
	- **internal_number** - filter by WHO internal number
	- **phase** - filter by trial phase (Phase I, II, III, etc.)
	- **study_type** - filter by study type (Interventional, Observational)
	- **primary_sponsor** - filter by sponsor organization
	- **source_register** - filter by source registry
	- **countries** - filter by trial countries
	
	# Medical/Research Parameters:
	- **condition** - filter by medical condition
	- **intervention** - filter by intervention type
	- **therapeutic_areas** - filter by therapeutic areas
	- **inclusion_agemin/agemax** - filter by age inclusion criteria
	- **inclusion_gender** - filter by gender inclusion criteria
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = TrialFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'trial_id']
	ordering = ['-discovery_date']

class AllTrialViewSet(generics.ListAPIView):
	"""
	List all clinical trials by discovery date
	"""
	pagination_class = None
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


###
# SOURCES
### 

class SourceViewSet(viewsets.ModelViewSet):
	"""
	List all sources of data with optional filters for team and subject.
	
	# Query Parameters:
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	"""
	queryset = Sources.objects.all().order_by('name')
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = SourceFilter
	search_fields = ['name', 'description']
	ordering_fields = ['name', 'source_id']
	ordering = ['name']

###
# AUTHORS
### 

class AuthorsViewSet(viewsets.ModelViewSet):
	"""
	Enhanced Authors API with sorting and filtering capabilities.
	
	# Query Parameters:
	
	- **author_id** - filter by specific author ID
	- **full_name** - search by author's full name (case-insensitive)
	- **sort_by** - 'article_count' (default: 'author_id')
	- **order** - 'asc' or 'desc' (default: 'desc' for article_count, 'asc' for others)
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **category_slug** - filter by team category slug
	- **category_id** - filter by team category ID
	- **date_from** - filter articles from this date (YYYY-MM-DD)
	- **date_to** - filter articles to this date (YYYY-MM-DD)
	- **timeframe** - 'year', 'month', 'week' (relative to current date)
	
	# Examples:
	
	- Get specific author: `?author_id=380002`
	- Search by name: `?full_name=John%20Smith`
	- Sort by article count: `?sort_by=article_count&order=desc`
	- Filter by timeframe: `?sort_by=article_count&timeframe=year`
	- Team and subject filter: `?team_id=1&subject_id=5&sort_by=article_count`
	- Count per category: `?team_id=1&category_slug=natalizumab&sort_by=article_count&order=desc`
	- Category with ID: `?team_id=1&category_id=5&sort_by=article_count&order=desc`
	- Category with timeframe: `?team_id=1&category_slug=natalizumab&timeframe=year&sort_by=article_count`
	- Date range: `?date_from=2024-06-01&date_to=2024-12-31&team_id=1&subject_id=1&sort_by=article_count`
	"""
	serializer_class = AuthorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	
	def get_queryset(self):
		queryset = Authors.objects.all()
		
		# Get query parameters
		author_id = self.request.query_params.get('author_id')
		full_name = self.request.query_params.get('full_name')
		sort_by = self.request.query_params.get('sort_by', 'author_id')
		order = self.request.query_params.get('order', 'desc' if sort_by == 'article_count' else 'asc')
		team_id = self.request.query_params.get('team_id')
		subject_id = self.request.query_params.get('subject_id')
		category_slug = self.request.query_params.get('category_slug')
		category_id = self.request.query_params.get('category_id')
		date_from = self.request.query_params.get('date_from')
		date_to = self.request.query_params.get('date_to')
		timeframe = self.request.query_params.get('timeframe')
		
		# Apply simple filters first
		if author_id:
			try:
				author_id = int(author_id)
				queryset = queryset.filter(author_id=author_id)
			except ValueError:
				pass
		
		if full_name:
			# Use uppercase search for better performance with GIN index
			upper_value = full_name.upper()
			queryset = queryset.filter(ufull_name__contains=upper_value)
		
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
		
		# Validate that team_id is provided when using subject_id or category filters
		if (subject_id or category_slug or category_id) and not team_id:
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
		
		if category_id:
			try:
				category_id = int(category_id)
				author_filters['team_categories__id'] = category_id
				count_filters['articles__team_categories__id'] = category_id
			except ValueError:
				pass
		
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
		- category_slug OR category_id (required): Team category slug or ID
		- Additional filters from main queryset apply
		"""
		team_id = request.query_params.get('team_id')
		category_slug = request.query_params.get('category_slug')
		category_id = request.query_params.get('category_id')
		
		if not team_id or (not category_slug and not category_id):
			return Response(
				{"error": "team_id and either category_slug or category_id are required"}, 
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
	✅ **PREFERRED ENDPOINT**: This is the main subjects endpoint that supports filtering options.
	
	List all subjects in the database with optional team filtering.
	
	# Query Parameters:
	- **team_id** - filter by team ID (replaces /teams/{id}/subjects/)
	- **search** - search in subject name and description
	- **ordering** - order by 'id', 'subject_name', 'team' (add '-' for reverse)
	
	# Examples:
	- Filter by team: `/subjects/?team_id=1`
	- Search subjects: `/subjects/?search=multiple`
	- Team filter with search: `/subjects/?team_id=1&search=sclerosis`
	- Order by name: `/subjects/?ordering=subject_name`
	"""
	queryset = Subject.objects.all().order_by('id')
	serializer_class = SubjectsSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = SubjectFilter
	search_fields = ['subject_name', 'description']
	ordering_fields = ['id', 'subject_name', 'team']
	ordering = ['id']

class ArticlesByTeam(viewsets.ModelViewSet):
	"""
	⚠️ DEPRECATED: This endpoint will be removed in a future version.
	Please use /articles/?team_id={team_id} instead.
	
	List all articles for a specific team by ID.
	
	Migration Path:
	- Old: GET /teams/1/articles/?search=keyword
	- New: GET /articles/?team_id=1&search=keyword
	
	Now supports enhanced filtering while maintaining backward compatibility.
	You can use all the same filters as the main /articles/ endpoint:
	- ?author_id=X - Filter by author ID
	- ?category_slug=slug - Filter by category slug
	- ?category_id=X - Filter by category ID
	- ?journal_slug=slug - Filter by journal (URL-encoded)
	- ?source_id=Y - Filter by source ID
	- ?search=keyword - Search in title and summary
	- ?ordering=field - Order results
	"""
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = ArticleFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
	ordering = ['-discovery_date']

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Articles.objects.filter(teams__id=team_id).order_by('-discovery_date')
	
	def list(self, request, *args, **kwargs):
		"""Override list to add deprecation warning header"""
		response = super().list(request, *args, **kwargs)
		team_id = self.kwargs.get('team_id')
		deprecated_endpoint = f'/teams/{team_id}/articles/'
		replacement_endpoint = f'/articles/?team_id={team_id}'
		return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)

class ArticlesBySubject(viewsets.ModelViewSet):
	"""
	⚠️ DEPRECATED: This endpoint will be removed in a future version.
	Please use /articles/?team_id={team_id}&subject_id={subject_id} instead.
	
	List all articles for a specific team and subject combination.
	
	Migration Path:
	- Old: GET /teams/1/articles/subject/4/?search=keyword
	- New: GET /articles/?team_id=1&subject_id=4&search=keyword
	
	Now supports enhanced filtering while maintaining backward compatibility.
	You can use all the same filters as the main /articles/ endpoint:
	- ?author_id=X - Filter by author ID
	- ?category_slug=slug - Filter by category slug
	- ?category_id=X - Filter by category ID
	- ?journal_slug=slug - Filter by journal (URL-encoded)
	- ?source_id=Y - Filter by source ID
	- ?search=keyword - Search in title and summary
	- ?ordering=field - Order results
	"""
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = ArticleFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
	ordering = ['-discovery_date']

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		subject_id = self.kwargs.get('subject_id')
		return Articles.objects.filter(subjects__id=subject_id, teams=team_id).order_by('-discovery_date')
	
	def list(self, request, *args, **kwargs):
		"""Override list to add deprecation warning header"""
		response = super().list(request, *args, **kwargs)
		team_id = self.kwargs.get('team_id')
		subject_id = self.kwargs.get('subject_id')
		deprecated_endpoint = f'/teams/{team_id}/articles/subject/{subject_id}/'
		replacement_endpoint = f'/articles/?team_id={team_id}&subject_id={subject_id}'
		return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)

class SubjectsByTeam(viewsets.ModelViewSet):
	"""
	⚠️ DEPRECATED: This endpoint will be removed in a future version.
	Please use /subjects/?team_id={team_id} instead.
	
	List all research subjects for a specific team by ID.
	
	Migration Path:
	- Old: GET /teams/1/subjects/?search=keyword
	- New: GET /subjects/?team_id=1&search=keyword
	
	Now supports enhanced filtering while maintaining backward compatibility.
	You can use all the same filters as the main /subjects/ endpoint:
	- ?search=keyword - Search in subject name and description
	- ?ordering=field - Order results
	"""
	serializer_class = SubjectsSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = SubjectFilter
	search_fields = ['subject_name', 'description']
	ordering_fields = ['id', 'subject_name']
	ordering = ['id']

	def get_queryset(self):
		team_id = self.kwargs.get('team_id')
		return Subject.objects.filter(team__id=team_id).order_by('-id')
	
	def list(self, request, *args, **kwargs):
		"""Override list to add deprecation warning header"""
		response = super().list(request, *args, **kwargs)
		team_id = self.kwargs.get('team_id')
		deprecated_endpoint = f'/teams/{team_id}/subjects/'
		replacement_endpoint = f'/subjects/?team_id={team_id}'
		return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)

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
		⚠️ DEPRECATED: This endpoint will be removed in a future version.
		Please use /articles/?team_id={team_id}&category_slug={category_slug} instead.
		
		List all articles for a specific category and team.
		
		Migration Path:
		- Old: GET /teams/1/articles/category/natalizumab/
		- New: GET /articles/?team_id=1&category_slug=natalizumab
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
		
		def list(self, request, *args, **kwargs):
			"""Override list to add deprecation warning header"""
			response = super().list(request, *args, **kwargs)
			team_id = self.kwargs.get('team_id')
			category_slug = self.kwargs.get('category_slug')
			deprecated_endpoint = f'/teams/{team_id}/articles/category/{category_slug}/'
			replacement_endpoint = f'/articles/?team_id={team_id}&category_slug={category_slug}'
			return add_deprecation_headers(response, deprecated_endpoint, replacement_endpoint)

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
        # For POST requests, validate required parameters
        team_id = request.data.get('team_id')
        subject_id = request.data.get('subject_id')
        
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
            
        # Delegate to the list method which uses get_queryset
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
        # For POST requests, validate required parameters
        team_id = request.data.get('team_id')
        subject_id = request.data.get('subject_id')
        
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
            
        # Delegate to the list method which uses get_queryset
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
        # For POST requests, validate required parameters
        team_id = request.data.get('team_id')
        subject_id = request.data.get('subject_id')
        
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
