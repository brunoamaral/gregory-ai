from api.serializers import (
		ArticleSerializer, TrialSerializer, SourceSerializer, AuthorSerializer,
		CategorySerializer, CategoryTopAuthorSerializer, TeamSerializer, SubjectsSerializer,
		ArticlesByCategoryAndTeamSerializer, OrganizationSerializer
)
from api.pagination import FlexiblePagination
from datetime import datetime, timedelta
from django.db.models import Count, Q, Prefetch
from django.db.models.functions import Length, TruncMonth
from django.shortcuts import get_object_or_404
from gregory.classes import SciencePaper, ClinicalTrial
from gregory.models import Articles, Trials, Sources, Authors, Team, Subject, TeamCategory
from organizations.models import Organization
from rest_framework import permissions, viewsets, generics, filters, status
from rest_framework.decorators import api_view, action
from django_filters import rest_framework as django_filters
from api.filters import ArticleFilter, TrialFilter, AuthorFilter, SourceFilter, CategoryFilter, SubjectFilter
from rest_framework.response import Response
from django.http import Http404, StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
import json
import traceback
from django.utils.dateparse import parse_date

from api.utils.utils import checkValidAccess, getAPIKey, getIPAddress, find_trial_by_identifier
from api.models import APIAccessSchemeLog
from api.utils.exceptions import (
		APIAccessDeniedError, APIInvalidAPIKeyError, APIInvalidIPAddressError,
		APINoAPIKeyError, ArticleExistsError, ArticleNotSavedError, CrossOrgPayloadError,
		DoiNotFound, FieldNotFoundError, SourceNotFoundError
)
from api.utils.responses import (
		ACCESS_DENIED, CROSS_ORG_PAYLOAD, INVALID_API_KEY, INVALID_IP_ADDRESS, NO_API_KEY,
		UNEXPECTED, SOURCE_NOT_FOUND, FIELD_NOT_FOUND, ARTICLE_EXISTS, ARTICLE_NOT_SAVED, returnData, returnError
)

class OrgVisibilityMixin:
	"""
	Viewset mixin that scopes the queryset to organisations the caller can see.

	Uses ``request.visible_org_ids`` (set by ``VisibleOrgMiddleware``).  Falls
	back to the full queryset when the attribute is absent so tests and
	management commands that bypass middleware are not broken.

	Override ``_org_filter_path`` in the subclass to set the ORM lookup path
	from the model to the Organisation PK.  Defaults to
	``teams__organization_id`` (Articles and Trials via M2M teams relation).

	Examples:
	  - Team:          _org_filter_path = 'organization_id'
	  - Subject/Source/Category:  _org_filter_path = 'team__organization_id'
	"""

	_org_filter_path = 'teams__organization_id'
	# Set to False for viewsets that reach orgs via a simple FK (not M2M) to
	# avoid unnecessary DISTINCT overhead on those queries.
	_org_filter_distinct = True

	def get_queryset(self):
		qs = super().get_queryset()
		if not hasattr(self.request, 'visible_org_ids'):
			return qs
		qs = qs.filter(**{f'{self._org_filter_path}__in': self.request.visible_org_ids})
		return qs.distinct() if self._org_filter_distinct else qs


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
	Allows authenticated clients to add new articles or trials to the database.

	The ``kind`` field in the payload must match the ``source_for`` value of the
	indicated source.  Routing per kind:
	  - ``science paper``  → CrossRef enrichment, saved to Articles
	  - ``trials``         → saved to Trials (dedup by identifier then title)
	  - ``news article``   → saved to Articles, no CrossRef lookup
	"""
	access_scheme = None
	call_type = request.method + " " + request.path
	ip_addr = getIPAddress(request)
	post_data = json.loads(request.body)
	try:
		api_key = getAPIKey(request)
		access_scheme = checkValidAccess(api_key, ip_addr)

		# PR 7: keys without an org cannot post
		if access_scheme.organization is None:
			raise APIAccessDeniedError('API keys without an associated organisation cannot post articles.')

		# --- Field presence checks -------------------------------------------
		if 'kind' not in post_data or post_data['kind'] is None:
			raise FieldNotFoundError('field `kind` was not found in the payload')
		if 'source_id' not in post_data or post_data['source_id'] is None:
			raise FieldNotFoundError('source_id field not found in payload')
		if 'title' not in post_data and 'doi' not in post_data:
			raise FieldNotFoundError('field `doi` and `title` not in the payload. You need at least one.')

		# --- Source validation (existence, org, kind match) ------------------
		try:
			source = Sources.objects.get(pk=post_data['source_id'])
		except Sources.DoesNotExist:
			raise SourceNotFoundError(f'source_id {post_data["source_id"]} was not found in the database')
		if source.team is None:
			raise SourceNotFoundError(f'source_id {source.pk} has no team assigned')
		if source.team.organization_id != access_scheme.organization_id:
			raise CrossOrgPayloadError(
				f'source_id {source.pk} belongs to a different organisation than your API key.'
			)
		if post_data['kind'] != source.source_for:
			raise FieldNotFoundError(
				f'payload kind \'{post_data["kind"]}\' does not match source kind \'{source.source_for}\''
			)

		# Helper: coerce empty strings to None
		def _val(key):
			v = post_data.get(key)
			return None if v == '' else v

		kind = post_data['kind']

		# =================================================================
		# Branch: science paper
		# =================================================================
		if kind == 'science paper':
			new_article = {
				'title': _val('title'),
				'link': _val('link'),
				'doi': _val('doi'),
				'access': _val('access'),
				'summary': _val('summary'),
				'published_date': _val('published_date'),
				'kind': kind,
				'publisher': _val('publisher'),
				'container_title': _val('container_title'),
			}
			science_paper = SciencePaper(doi=new_article['doi'], title=new_article['title'])
			if science_paper.doi is None:
				science_paper.doi = science_paper.find_doi(title=science_paper.title)
			if science_paper.doi is not None:
				science_paper.refresh()
			if new_article['doi'] is None:
				new_article['doi'] = science_paper.doi
			if new_article['title'] is None:
				new_article['title'] = science_paper.title
			if new_article['link'] is None:
				new_article['link'] = science_paper.link
			if new_article['summary'] is None:
				new_article['summary'] = science_paper.clean_abstract()
			if new_article['published_date'] is None:
				new_article['published_date'] = science_paper.published_date
			if new_article['access'] is None:
				new_article['access'] = science_paper.access
			if new_article['publisher'] is None:
				new_article['publisher'] = science_paper.publisher
			if new_article['container_title'] is None:
				new_article['container_title'] = science_paper.journal

			# Dedup by DOI
			if new_article['doi'] is not None:
				existing = Articles.objects.filter(doi=new_article['doi'])
				if existing.exists():
					for article in existing:
						article.sources.add(source)
						article.teams.add(source.team)
						if source.subject:
							article.subjects.add(source.subject)
					raise ArticleExistsError(
						'There is already an article with the specified DOI. '
						'If the source, team, or subject were different, the article was updated.'
					)
			# Dedup by title
			if new_article['title'] is not None:
				if Articles.objects.filter(title=new_article['title']).exists():
					raise ArticleExistsError('There is already an article with the specified Title')

			save_article = Articles.objects.create(
				discovery_date=datetime.now(),
				title=new_article['title'],
				summary=new_article['summary'],
				link=new_article['link'],
				published_date=new_article['published_date'],
				doi=new_article['doi'],
				kind=kind,
				publisher=new_article['publisher'],
				container_title=new_article['container_title'],
			)
			save_article.sources.add(source)
			save_article.teams.add(source.team)
			if source.subject:
				save_article.subjects.add(source.subject)
			if save_article.pk is None:
				raise ArticleNotSavedError('Could not create the article')

			log_data = {'article_id': save_article.pk}
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 201, 'Article created', log_data)
			return returnData({
				'name': 'Gregory | API', 'version': '0.1b',
				'data_received': post_data,
				'data_processed_from_doi': new_article,
				'article_id': save_article.article_id,
			})

		# =================================================================
		# Branch: trials
		# =================================================================
		elif kind == 'trials':
			trial_data = ClinicalTrial(
				title=_val('title'),
				summary=_val('summary'),
				link=_val('link'),
				published_date=_val('published_date'),
				identifiers=post_data.get('identifiers') or {},
			)

			# Dedup: identifiers first (via helper), then title (mirrors feedreader_trials logic)
			existing_trial = find_trial_by_identifier(trial_data.identifiers or {}).first()
			if existing_trial is None and trial_data.title:
				existing_trial = Trials.objects.filter(title__iexact=trial_data.title).first()

			if existing_trial:
				existing_trial.sources.add(source)
				existing_trial.teams.add(source.team)
				if source.subject:
					existing_trial.subjects.add(source.subject)
				raise ArticleExistsError(
					'There is already a trial matching the provided identifiers or title. '
					'If the source, team, or subject were different, the trial was updated.'
				)

			save_trial = Trials.objects.create(
				discovery_date=datetime.now(),
				title=trial_data.title,
				summary=trial_data.summary,
				link=trial_data.link,
				published_date=trial_data.published_date,
				identifiers=trial_data.identifiers or {},
			)
			save_trial.sources.add(source)
			save_trial.teams.add(source.team)
			if source.subject:
				save_trial.subjects.add(source.subject)
			if save_trial.pk is None:
				raise ArticleNotSavedError('Could not create the trial')

			log_data = {'trial_id': save_trial.pk}
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 201, 'Trial created', log_data)
			return returnData({
				'name': 'Gregory | API', 'version': '0.1b',
				'data_received': post_data,
				'trial_id': save_trial.trial_id,
			})

		# =================================================================
		# Branch: news article
		# =================================================================
		elif kind == 'news article':
			new_article = {
				'title': _val('title'),
				'link': _val('link'),
				'summary': _val('summary'),
				'published_date': _val('published_date'),
				'kind': kind,
			}
			# Dedup by title
			if new_article['title'] is not None:
				if Articles.objects.filter(title=new_article['title']).exists():
					raise ArticleExistsError('There is already an article with the specified Title')
			# Dedup by link
			if new_article['link'] is not None:
				if Articles.objects.filter(link=new_article['link']).exists():
					raise ArticleExistsError('There is already an article with the specified link')

			save_article = Articles.objects.create(
				discovery_date=datetime.now(),
				title=new_article['title'],
				summary=new_article['summary'],
				link=new_article['link'],
				published_date=new_article['published_date'],
				kind=kind,
			)
			save_article.sources.add(source)
			save_article.teams.add(source.team)
			if source.subject:
				save_article.subjects.add(source.subject)
			if save_article.pk is None:
				raise ArticleNotSavedError('Could not create the news article')

			log_data = {'article_id': save_article.pk}
			generateAccessSchemeLog(call_type, ip_addr, access_scheme, 201, 'News article created', log_data)
			return returnData({
				'name': 'Gregory | API', 'version': '0.1b',
				'data_received': post_data,
				'article_id': save_article.article_id,
			})

		else:
			raise FieldNotFoundError(f'Unsupported kind \'{kind}\'')

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
	except SourceNotFoundError as exception:
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 404, str(exception), str(post_data))
		return returnError(SOURCE_NOT_FOUND, str(exception), 404)
	except FieldNotFoundError as exception:
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 400, str(exception), str(post_data))
		return returnError(FIELD_NOT_FOUND, str(exception), 400)
	except CrossOrgPayloadError as exception:
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 400, str(exception), str(post_data))
		return returnError(CROSS_ORG_PAYLOAD, str(exception), 400)
	except ArticleExistsError as exception:
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 200, str(exception), str(post_data))
		return returnError(ARTICLE_EXISTS, str(exception), 200)
	except ArticleNotSavedError as exception:
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 500, str(exception), str(post_data))
		return returnError(ARTICLE_NOT_SAVED, str(exception), 500)
	except Exception as exception:
		print(traceback.format_exc())
		generateAccessSchemeLog(call_type, ip_addr, access_scheme, 500, str(exception), str(post_data))
		return returnError(UNEXPECTED, str(exception), 500)

###
# ARTICLES
### 
class ArticleViewSet(OrgVisibilityMixin, viewsets.ModelViewSet):
	"""
	List all articles in the database with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.
	
	# Query Parameters:
	- **team_id** - filter by team ID 
	- **doi** - filter by exact DOI (case-insensitive)
	- **subject_id** - filter by subject ID (used with team_id)
	- **subjects** - comma-separated list of subject IDs with AND semantics — returns only articles tagged with *all* listed subjects (e.g., `?subjects=1,2`)
	- **author_id** - filter by author ID
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **journal_slug** - filter by journal (convert spaces to dashes)
	- **source_id** - filter by source ID
	- **search** - search in title and summary
	- **ordering** - order results by field (e.g., -published_date, title)
	- **page** - page number for pagination
	- **page_size** - items per page (max 100)
	- **all_results** - set to 'true' to bypass pagination and get all results (useful for CSV export)

	# Special Article Types:
	- **relevant** - filter for relevant articles (true/false). When combined with **subject_id**, relevance is scoped to that specific subject — only articles that are relevant *for that subject* (via ML predictions or manual marking) are returned. Without subject_id, relevance is checked across all subjects.
	- **ml_threshold** - minimum ML prediction confidence (float 0.0-1.0, e.g., 0.75). Also scoped to subject_id when provided.
	- **open_access** - filter for open access articles (true/false)
	- **last_days** - filter for articles from last N days (number)
	- **week** - filter for specific week number (requires year parameter)
	- **year** - year for week filtering (used with week parameter)
	
	# Examples:
	- By DOI: `/articles/?doi=10.1016/j.procs.2023.01.401`
	- Team articles: `/articles/?team_id=1`
	- Team + subject: `/articles/?team_id=1&subject_id=4`
	- Multi-subject AND: `/articles/?subjects=1,2`
	- With search: `/articles/?team_id=1&search=stem+cells`
	- Category by slug: `/articles/?team_id=1&category_slug=natalizumab`
	- Category by ID: `/articles/?team_id=1&category_id=5`
	- Relevant articles: `/articles/?relevant=true`
	- Relevant with ML threshold: `/articles/?relevant=true&ml_threshold=0.75`
	- Relevant from last 15 days: `/articles/?relevant=true&last_days=15`
	- Relevant from specific week: `/articles/?relevant=true&week=52&year=2024`
	- Open access articles: `/articles/?open_access=true`
	- CSV export all results: `/articles/?format=csv&all_results=true`
	- Complex filter: `/articles/?team_id=1&subject_id=4&author_id=123&search=regeneration&relevant=true&ml_threshold=0.8&ordering=-published_date`
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_class = FlexiblePagination
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = ArticleFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
	ordering = ['-discovery_date']

	def finalize_response(self, request, response, *args, **kwargs):
		"""
		Override to handle CSV streaming. If CSV format is requested, convert the response
		to a StreamingHttpResponse with proper headers.
		"""
		# Call parent finalize_response first
		response = super().finalize_response(request, response, *args, **kwargs)
		
		# Check if this is a CSV response
		if request.query_params.get('format', '').lower() == 'csv':
			# Render the response to get the content
			response.render()
			
			# Get the CSV bytes from the response content
			csv_bytes = response.content if isinstance(response.content, bytes) else response.content.encode('utf-8')
			
			# Create a simple generator that yields the bytes
			def csv_stream():
				yield csv_bytes
			
			# Determine the filename from the renderer
			from api.direct_streaming import DirectStreamingCSVRenderer
			renderer = DirectStreamingCSVRenderer()
			filename = renderer.get_filename({'request': request})
			
			# Create StreamingHttpResponse
			streaming_response = StreamingHttpResponse(
				streaming_content=csv_stream(),
				content_type='text/csv; charset=utf-8'
			)
			
			# Set all the proper headers
			streaming_response['Content-Disposition'] = f'attachment; filename="{filename}"'
			streaming_response['Content-Type'] = 'text/csv; charset=utf-8'
			
			return streaming_response
		
		return response


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
	- **get_categories** - comma-separated list of category IDs (e.g., 1,2,3)
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
	- Multiple categories by ID: `GET /categories/?get_categories=1,2,3`
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

		# --- Org visibility: only categories whose team's org is visible ---
		if hasattr(self.request, 'visible_org_ids'):
			queryset = queryset.filter(team__organization_id__in=self.request.visible_org_ids)
		
		# Apply filters without expensive annotations
		team_id = self.request.query_params.get('team_id')
		subject_id = self.request.query_params.get('subject_id')
		category_id = self.request.query_params.get('category_id')
		get_categories = self.request.query_params.get('get_categories')
		
		if team_id:
			queryset = queryset.filter(team_id=team_id)
		
		if subject_id:
			queryset = queryset.filter(subjects__id=subject_id)
			
		if category_id:
			queryset = queryset.filter(id=category_id)

		# Support fetching multiple categories by ID: get_categories=1,2,3
		if get_categories:
			ids = []
			for part in get_categories.split(','):
				try:
					ids.append(int(part.strip()))
				except (ValueError, TypeError):
					continue
			if len(ids) > 0:
				queryset = queryset.filter(id__in=ids)
		
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

###
# TRIALS
### 

class TrialViewSet(OrgVisibilityMixin, viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date with comprehensive filtering options.
	CSV responses are automatically streamed for better performance with large datasets.
	
	# Core Query Parameters:
	- **trial_id** - filter by specific trial ID
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	- **subjects** - comma-separated list of subject IDs with AND semantics — returns only trials tagged with *all* listed subjects (e.g., `?subjects=1,2`)
	- **category_slug** - filter by category slug
	- **category_id** - filter by category ID
	- **source_id** - filter by source ID
	- **status/recruitment_status** - filter by recruitment status
	- **search** - search in title and summary
	- **page** - page number for pagination
	- **page_size** - items per page (max 100)
	- **all_results** - set to 'true' to bypass pagination and get all results (useful for CSV export)
	
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
	
	# Examples:
	- All trials as CSV: `/trials/?format=csv&all_results=true`
	- Filtered trials: `/trials/?team_id=1&status=Recruiting&format=csv&all_results=true`
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_class = FlexiblePagination
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
	filterset_class = TrialFilter
	search_fields = ['title', 'summary']
	ordering_fields = ['discovery_date', 'published_date', 'title', 'trial_id', 'last_updated']
	ordering = ['-discovery_date']

	def list(self, request, *args, **kwargs):
		response = super().list(request, *args, **kwargs)
		# Only inject stats into JSON responses (not CSV)
		if isinstance(response.data, dict):
			filtered_qs = self.filter_queryset(self.get_queryset())
			# Single aggregation query — clear ordering to prevent GROUP BY pollution
			status_counts = {
				item['recruitment_status']: item['count']
				for item in filtered_qs.order_by().values('recruitment_status').annotate(count=Count('trial_id'))
			}
			def _sum(*keys):
				return sum(status_counts.get(k, 0) for k in keys)
			response.data['stats'] = {
				'total': sum(status_counts.values()),
				'no_status': status_counts.get(None, 0),
				'recruiting': _sum('Recruiting', 'RECRUITING'),
				'active_not_recruiting': _sum('ACTIVE_NOT_RECRUITING', 'Not recruiting', 'Not Recruiting'),
				'not_yet_recruiting': _sum('NOT_YET_RECRUITING'),
				'completed': _sum('COMPLETED'),
				'enrolling_by_invitation': _sum('ENROLLING_BY_INVITATION'),
				'terminated': _sum('TERMINATED'),
				'suspended': _sum('SUSPENDED'),
				'withdrawn': _sum('WITHDRAWN'),
				'available': _sum('AVAILABLE'),
				'not_available': _sum('Not Available'),
				'withheld': _sum('WITHHELD'),
				'authorised': _sum('Authorised'),
			}
		return response

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

class SourceViewSet(OrgVisibilityMixin, viewsets.ModelViewSet):
	"""
	List all sources of data with optional filters for team and subject.
	
	# Query Parameters:
	- **team_id** - filter by team ID
	- **subject_id** - filter by subject ID
	"""
	_org_filter_path = 'team__organization_id'
	_org_filter_distinct = False
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
	- **given_name** - search by author's given name (case-insensitive)
	- **family_name** - search by author's family name (case-insensitive)
	- **orcid** - filter by ORCID identifier (case-insensitive contains search)
	- **country** - filter by country code (exact match)
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
	- Search by given name: `?given_name=John`
	- Search by family name: `?family_name=Smith`
	- Filter by ORCID: `?orcid=0000-0000-0000-0001`
	- Filter by country: `?country=US`
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
	filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter]
	filterset_class = AuthorFilter
	search_fields = ['full_name', 'ORCID']
	ordering_fields = ['author_id', 'full_name', 'country', 'article_count']
	ordering = ['author_id']
	
	def get_queryset(self):
		queryset = Authors.objects.all()

		# --- Org visibility: only authors with at least one article in a visible org ---
		if hasattr(self.request, 'visible_org_ids'):
			queryset = queryset.filter(
				articles__teams__organization_id__in=self.request.visible_org_ids
			).distinct()
		
		# Get query parameters
		author_id = self.request.query_params.get('author_id')
		full_name = self.request.query_params.get('full_name')
		orcid = self.request.query_params.get('orcid')
		country = self.request.query_params.get('country')
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
		
		if orcid:
			# Filter by ORCID (case-insensitive contains search)
			queryset = queryset.filter(ORCID__contains=orcid)
		
		if country:
			# Filter by country (exact match)
			queryset = queryset.filter(country=country)
		
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
		
		# Apply team/subject/category filters using single-phase approach
		count_filters = {}   # Used for Count annotation on Authors queryset
		
		# Validate that team_id is provided when using subject_id or category filters
		if (subject_id or category_slug or category_id) and not team_id:
			# Return empty queryset if team_id is missing for subject/category filtering
			return Authors.objects.none()
		
		if team_id:
			try:
				team_id = int(team_id)
				count_filters['articles__teams__id'] = team_id
			except ValueError:
				pass
		
		if subject_id:
			try:
				subject_id = int(subject_id)
				count_filters['articles__subjects__id'] = subject_id
			except ValueError:
				pass
		
		if category_slug:
			count_filters['articles__team_categories__category_slug'] = category_slug
		
		if category_id:
			try:
				category_id = int(category_id)
				count_filters['articles__team_categories__id'] = category_id
			except ValueError:
				pass
		
		# Add date filters to count filters
		count_filters.update(date_filters)

		# Build an org-visibility filter for the Count annotation so that
		# article_count always reflects only visible articles (fixes sorting/
		# filtering by article_count leaking hidden-org data).
		has_org_scope = hasattr(self.request, 'visible_org_ids')
		org_q = Q(articles__teams__organization_id__in=self.request.visible_org_ids) if has_org_scope else Q()

		# Add article count annotation for sorting
		if sort_by == 'article_count':
			if count_filters:
				combined_q = Q(**count_filters) & org_q if has_org_scope else Q(**count_filters)
				queryset = queryset.annotate(
					article_count=Count('articles', filter=combined_q, distinct=True)
				).filter(article_count__gt=0)
			elif has_org_scope:
				queryset = queryset.annotate(
					article_count=Count('articles', filter=org_q, distinct=True)
				)
			else:
				queryset = queryset.annotate(
					article_count=Count('articles', distinct=True)
				)
		elif count_filters:
			# Even if not sorting by article_count, we still need to filter authors
			# to only those who have articles matching the criteria
			combined_q = Q(**count_filters) & org_q if has_org_scope else Q(**count_filters)
			queryset = queryset.annotate(
				article_count=Count('articles', filter=combined_q, distinct=True)
			).filter(article_count__gt=0)
		
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

class TeamsViewSet(OrgVisibilityMixin, viewsets.ModelViewSet):
	"""
	List all teams
	"""
	_org_filter_path = 'organization_id'
	_org_filter_distinct = False
	queryset = Team.objects.all().order_by('id')
	serializer_class = TeamSerializer
	permission_classes  = [permissions.IsAuthenticatedOrReadOnly]

###
# ORGANISATIONS
###

class OrganizationsViewSet(viewsets.ReadOnlyModelViewSet):
	"""
	List organisations visible to the caller.

	Anonymous callers see only organisations where ``make_api_public=True``.
	Authenticated users and API-key holders see their own org; add
	``?include_public=true`` to also see public orgs.

	Detail endpoint (``/organizations/<id>/``) returns 404 rather than 403
	when the organisation is not visible (hide-existence rule).
	"""
	serializer_class = OrganizationSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		qs = Organization.objects.all().order_by('id')
		if not hasattr(self.request, 'visible_org_ids'):
			return qs
		return qs.filter(id__in=self.request.visible_org_ids)

###
# SUBJECTS
###

class SubjectsViewSet(OrgVisibilityMixin, viewsets.ModelViewSet):
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
	_org_filter_path = 'team__organization_id'
	_org_filter_distinct = False
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
		if hasattr(self.request, 'visible_org_ids'):
			if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
				raise Http404
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
		if hasattr(self.request, 'visible_org_ids'):
			if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
				raise Http404
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
		if hasattr(self.request, 'visible_org_ids'):
			if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
				raise Http404
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
		if hasattr(self.request, 'visible_org_ids'):
			if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
				raise Http404
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
				if hasattr(self.request, 'visible_org_ids'):
						if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
								raise Http404
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
    - ordering: Order results by field (e.g., -discovery_date, -published_date, title, article_id)
    
    Results are ordered by discovery date (newest first) by default.
    
    To download all search results as CSV, add format=csv and all_results=true to the query parameters.
    Example: /articles/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
    """
    serializer_class = ArticleSerializer
    permission_classes = [permissions.AllowAny]  # Allow access to anyone since we require team_id and subject_id
    filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = ArticleFilter
    search_fields = ['title', 'summary']
    ordering_fields = ['discovery_date', 'published_date', 'title', 'article_id']
    ordering = ['-discovery_date']  # Default ordering by newest first
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

        # Cast to int early — non-numeric values get a 404 rather than a 500.
        try:
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            raise Http404

        # Visibility check: hidden teams return 404 (before the broad except block)
        if hasattr(self.request, 'visible_org_ids'):
            if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
                raise Http404

        try:
            # Start with articles filtered by team and subject
            # Remove distinct constraint to allow proper ordering
            queryset = Articles.objects.filter(
                teams__id=team_id, 
                subjects__id=subject_id
            ).distinct()
            
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
    
    def filter_queryset(self, queryset):
        """
        Filter the queryset and handle ordering from both GET and POST requests.
        """
        # First apply standard filters
        queryset = super().filter_queryset(queryset)
        
        # Handle ordering for POST requests manually (OrderingFilter only checks query_params by default)
        if self.request.method == 'POST':
            ordering = self.request.data.get('ordering')
            if ordering:
                # Validate that ordering field is in allowed fields
                if ordering.lstrip('-') in [f.replace('-', '') for f in self.ordering_fields]:
                    queryset = queryset.order_by(ordering)
        
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
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            return Response({"error": "team_id and subject_id must be integers"}, status=400)

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
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            return Response({"error": "team_id and subject_id must be integers"}, status=400)

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
    - ordering: Order results by field (e.g., -discovery_date, -published_date, title, trial_id, -last_updated)
    
    Results are ordered by discovery date (newest first) by default.
	
    To download all search results as CSV, add format=csv and all_results=true to the query parameters.
    Example: /trials/search/?team_id=1&subject_id=1&search=covid&format=csv&all_results=true
    """
    serializer_class = TrialSerializer
    permission_classes = [permissions.AllowAny]  # Allow access to anyone since we require team_id and subject_id
    filter_backends = [filters.SearchFilter, django_filters.DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = TrialFilter
    search_fields = ['title', 'summary']
    ordering_fields = ['discovery_date', 'published_date', 'title', 'trial_id', 'last_updated']
    ordering = ['-discovery_date']  # Default ordering by newest first
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

        # Cast to int early — non-numeric values get a 404 rather than a 500.
        try:
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            raise Http404

        # Visibility check: hidden teams return 404 (before the broad except block)
        if hasattr(self.request, 'visible_org_ids'):
            if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
                raise Http404

        try:
            # Check if team and subject exist
            team = Team.objects.get(id=team_id)
            subject = Subject.objects.get(id=subject_id, team=team)
        except (Team.DoesNotExist, Subject.DoesNotExist):
            return Trials.objects.none()
        
        # Start with trials filtered by team and subject
        # Remove distinct constraint to allow proper ordering
        queryset = Trials.objects.filter(teams=team, subjects=subject).distinct()
        
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
    
    def filter_queryset(self, queryset):
        """
        Filter the queryset and handle ordering from both GET and POST requests.
        """
        # First apply standard filters
        queryset = super().filter_queryset(queryset)
        
        # Handle ordering for POST requests manually (OrderingFilter only checks query_params by default)
        if self.request.method == 'POST':
            ordering = self.request.data.get('ordering')
            if ordering:
                # Validate that ordering field is in allowed fields
                if ordering.lstrip('-') in [f.replace('-', '') for f in self.ordering_fields]:
                    queryset = queryset.order_by(ordering)
        
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
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            return Response({"error": "team_id and subject_id must be integers"}, status=400)

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
            team_id = int(team_id)
            subject_id = int(subject_id)
        except (TypeError, ValueError):
            return Response({"error": "team_id and subject_id must be integers"}, status=400)

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

    def _check_team_visibility(self, team_id):
        """Raise Http404 if team_id is not in the caller's visible orgs."""
        if hasattr(self.request, 'visible_org_ids'):
            if not Team.objects.filter(id=team_id, organization_id__in=self.request.visible_org_ids).exists():
                raise Http404

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

        self._check_team_visibility(team_id)
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

        self._check_team_visibility(team_id)
        # Delegate to the list method
        return self.list(request, *args, **kwargs)


###
# STATS
###

class StatsView(APIView):
	"""
	Returns aggregate statistics about the data in the system.
	All counts can be optionally scoped to one or more teams via ?team=1 or ?team=1,2,3.

	Counts are filtered to organisations visible to the caller when
	``request.visible_org_ids`` is present (set by VisibleOrgMiddleware).
	If the attribute is absent — e.g. in management commands or tests that
	bypass middleware — the fallback is unscoped querysets that span all
	organisations.  In production the middleware is always active, so callers
	will always receive org-filtered results.
	"""
	permission_classes = [permissions.AllowAny]

	def get(self, request):
		from urllib.parse import urlparse
		from subscriptions.models import Subscribers

		# --- Org visibility ------------------------------------------------
		visible_org_ids = getattr(request, 'visible_org_ids', None)

		# Parse team IDs from query params
		team_param = request.query_params.get('team', None)
		team_ids = None
		if team_param:
			try:
				team_ids = [int(t.strip()) for t in team_param.split(',') if t.strip()]
			except ValueError:
				return Response(
					{'error': 'Invalid team parameter. Expected integer or comma-separated integers.'},
					status=status.HTTP_400_BAD_REQUEST
				)
			# 404 if any requested team is not in a visible org
			if visible_org_ids is not None:
				visible = Team.objects.filter(id__in=team_ids, organization_id__in=visible_org_ids)
				if visible.count() != len(set(team_ids)):
					raise Http404

		# Base querysets scoped to visible orgs (no-op when middleware absent)
		if visible_org_ids is not None:
			articles_base     = Articles.objects.filter(teams__organization_id__in=visible_org_ids).distinct()
			trials_base       = Trials.objects.filter(teams__organization_id__in=visible_org_ids).distinct()
			authors_base      = Authors.objects.filter(articles__teams__organization_id__in=visible_org_ids).distinct()
			sources_base      = Sources.objects.filter(team__organization_id__in=visible_org_ids)
			subscribers_base  = Subscribers.objects.filter(subscriptions__team__organization_id__in=visible_org_ids)
		else:
			articles_base     = Articles.objects.all()
			trials_base       = Trials.objects.all()
			authors_base      = Authors.objects.all()
			sources_base      = Sources.objects.all()
			subscribers_base  = Subscribers.objects.all()

		# Articles count
		if team_ids:
			articles_count = articles_base.filter(teams__in=team_ids).distinct().count()
		else:
			articles_count = articles_base.count()

		# Trials count
		if team_ids:
			trials_count = trials_base.filter(teams__in=team_ids).distinct().count()
		else:
			trials_count = trials_base.count()

		# Subscribers count (active only)
		if team_ids:
			subscribers_count = subscribers_base.filter(
				active=True,
				subscriptions__team__in=team_ids
			).distinct().count()
		else:
			subscribers_count = subscribers_base.filter(active=True).distinct().count()

		# Authors count (distinct authors linked to articles in scope)
		if team_ids:
			authors_count = authors_base.filter(
				articles__teams__in=team_ids
			).distinct().count()
		else:
			authors_count = authors_base.count()

		# Sources queryset scoped to team(s)
		if team_ids:
			sources_qs = sources_base.filter(team__in=team_ids)
		else:
			sources_qs = sources_base

		def extract_domain(url):
			if not url:
				return None
			try:
				return urlparse(url).netloc or None
			except Exception:
				return None

		source_data = list(sources_qs.values('link', 'source_for'))

		# sources.total = number of unique domains
		all_domains = set()
		for s in source_data:
			d = extract_domain(s['link'])
			if d:
				all_domains.add(d)

		# sources.by_type = unique domain count per source type
		type_domains = {}
		for s in source_data:
			d = extract_domain(s['link'])
			if d:
				type_domains.setdefault(s['source_for'], set()).add(d)
		sources_by_type = {k: len(v) for k, v in type_domains.items()}

		# sources.by_domain = each unique domain with count of individual feeds
		domain_feed_count = {}
		for s in source_data:
			d = extract_domain(s['link'])
			if d:
				domain_feed_count[d] = domain_feed_count.get(d, 0) + 1
		sources_by_domain = sorted(
			[{'domain': d, 'count': c} for d, c in domain_feed_count.items()],
			key=lambda x: x['count'],
			reverse=True
		)

		return Response({
			'articles': articles_count,
			'trials': trials_count,
			'subscribers': subscribers_count,
			'authors': authors_count,
			'sources': {
				'total': len(all_domains),
				'by_type': sources_by_type,
				'by_domain': sources_by_domain,
			}
		})
