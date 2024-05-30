from api.serializers import (
	ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer, AuthorSerializer, 
	CategorySerializer, TeamSerializer,SubjectsSerializer,
	ArticlesByCategoryAndTeamSerializer
)
from datetime import datetime, timedelta
from django.db.models import Count
from django.db.models import Q
from django.db.models.functions import Length, TruncMonth
from django.shortcuts import get_object_or_404
from gregory.classes import SciencePaper
from gregory.models import Articles, Trials, Sources, Authors,Team,Subject,TeamCategory
from rest_framework import permissions
from rest_framework import viewsets, permissions, generics, filters
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView
import json

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
				# Source Will Be Removed
				source = source, 
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
	List all articles in the database by earliest discovery_date
	"""
	queryset = Articles.objects.all().order_by('-discovery_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$title','$summary']

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
			category = Categories.objects.filter(category_slug=category_slug).first()

			if category is None:
				# Returning an empty queryset
				return Articles.objects.none()

			return Articles.objects.filter(categories=category).order_by('-discovery_date')

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
		subject_id = self.kwargs.get('subject_id')
		return Articles.objects.filter(subjects__id=subject_id).order_by('-discovery_date')
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
	List relevant articles, by manual selection and Machine Learning using the Gausian Naive Bayes Model.
	"""
	model = Articles
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.filter(Q(relevant=True) | Q(ml_prediction_gnb=True)).order_by('-discovery_date')

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

		source_id = self.kwargs['source_id']
		return Articles.objects.filter(source=source_id)

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
# CATEGORIES
###

class CategoryViewSet(viewsets.ModelViewSet):
	"""
	List all categories in the database.
	"""
	queryset = TeamCategory.objects.all()
	serializer_class = CategorySerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class MonthlyCountsView(generics.ListAPIView):
	def get(self, request, category_slug):
			category = get_object_or_404(TeamCategory, category_slug=category_slug)
			# Monthly article counts
			articles = Articles.objects.filter(categories=category)
			articles = articles.annotate(month=TruncMonth('published_date'))
			article_counts = articles.values('month').annotate(count=Count('article_id')).order_by('month')
			article_counts = list(article_counts.values('month', 'count'))

			# Monthly trial counts
			trials = Trials.objects.filter(categories=category)
			trials = trials.annotate(month=TruncMonth('published_date'))
			trial_counts = trials.values('month').annotate(count=Count('trial_id')).order_by('month')
			trial_counts = list(trial_counts.values('month', 'count'))

			data = {
				'category_name': category.category_name,
				'category_slug': category.category_slug,
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

class TrialsByCategory(viewsets.ModelViewSet):
	"""
	Search Trials by the category field. Usage /trials/category/{{category_slug}}/
	"""
	def get_queryset(self):
			category_slug = self.kwargs.get('category_slug', None)
			category = TeamCategory.objects.filter(category_slug=category_slug).first()

			if category is None:
				# Returning an empty queryset
				return Trials.objects.none()

			return Trials.objects.filter(categories=category).order_by('-trial_id')

	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


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
		return Categories.objects.filter(team__id=team_id).order_by('-category_id')


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
        )