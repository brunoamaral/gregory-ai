from rest_framework import serializers
from gregory.models import Articles, Trials, Sources, Authors, Subject, Team, MLPredictions, ArticleSubjectRelevance, TeamCategory, ArticleTrialReference, ArticleOrgContent, TrialOrgContent
from organizations.models import Organization
from sitesettings.models import CustomSetting
from django.contrib.sites.models import Site
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q

from api.serializers.mixins import OrgScopedSerializerMixin, _resolve_per_org_fields_org  # noqa: F401

def get_custom_settings():
		try:
			return CustomSetting.objects.get(site=settings.SITE_ID)
		except ObjectDoesNotExist:
			return None

def get_site():
		try:
			return Site.objects.get(pk=settings.SITE_ID)
		except ObjectDoesNotExist:
			return None

class TeamSerializer(serializers.ModelSerializer):
	class Meta:
		model = Team
		fields = '__all__'

class SubjectsSerializer(serializers.ModelSerializer):
	team_id = serializers.IntegerField(source='team.id', read_only=True)

	class Meta:
		model = Subject
		fields = ['id','subject_name', 'description', 'team_id']

class TeamCategorySerializer(serializers.ModelSerializer):
	class Meta:
		model = TeamCategory
		fields = ['id', 'category_name', 'category_description', 'category_slug', 'category_terms', 'category_type']

class ArticleSubjectRelevanceSerializer(serializers.ModelSerializer):
	subject = SubjectsSerializer(read_only=True)
	subject_id = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all(), source='subject', write_only=True)

	class Meta:
		model = ArticleSubjectRelevance
		fields = ['subject', 'subject_id', 'is_relevant']

class MLPredictionsSerializer(serializers.ModelSerializer):
	class Meta:
		model = MLPredictions
		fields = ['id', 'algorithm', 'model_version', 'probability_score', 'predicted_relevant', 'created_date', 'subject']

class CategoryTopAuthorSerializer(serializers.ModelSerializer):
	"""Serializer for top authors within a category"""
	articles_count = serializers.SerializerMethodField()
	country = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = ['author_id', 'given_name', 'family_name', 'full_name', 'ORCID', 'country', 'articles_count']

	def get_articles_count(self, obj):
		return getattr(obj, 'category_articles_count', 0)
	
	def get_country(self, obj):
		return obj.country.code if obj.country else None

class CategorySerializer(serializers.ModelSerializer):
	article_count_total = serializers.SerializerMethodField()
	trials_count_total = serializers.SerializerMethodField()
	authors_count = serializers.SerializerMethodField()
	top_authors = serializers.SerializerMethodField()
	monthly_counts = serializers.SerializerMethodField()
	
	class Meta:
		model = TeamCategory
		fields = [
			'id', 'category_description', 'category_name', 'category_slug', 'category_terms', 
			'article_count_total', 'trials_count_total', 'authors_count', 'top_authors', 'monthly_counts'
		]
	
	def get_article_count_total(self, obj):
		"""Optimized article count using prefetched data when available"""
		# Use prefetched data if available to avoid additional queries
		if hasattr(obj, '_prefetched_objects_cache') and 'articles' in obj._prefetched_objects_cache:
			return len(obj._prefetched_objects_cache['articles'])
		
		# If the queryset has annotated article_count, use it for efficiency
		if hasattr(obj, 'article_count_annotated'):
			return obj.article_count_annotated
		
		# Fallback to simple count query (avoid obj.article_count() which may be complex)
		return obj.articles.count()
	
	def get_trials_count_total(self, obj):
		"""Optimized trials count using prefetched data when available"""
		# Use prefetched data if available to avoid additional queries  
		if hasattr(obj, '_prefetched_objects_cache') and 'trials' in obj._prefetched_objects_cache:
			return len(obj._prefetched_objects_cache['trials'])
		
		# If the queryset has annotated trials_count, use it for efficiency
		if hasattr(obj, 'trials_count_annotated'):
			return obj.trials_count_annotated
		
		# Fallback to simple count query
		return obj.trials.count()
	
	def get_authors_count(self, obj):
		"""Optimized authors count avoiding complex JOINs"""
		# Use annotated count if available
		if hasattr(obj, 'authors_count_annotated'):
			return obj.authors_count_annotated
		
		# Use a more efficient subquery approach instead of complex JOINs
		# This avoids the hanging queries by using EXISTS instead of JOIN + COUNT + GROUP BY
		from django.db.models import Exists, OuterRef
		from gregory.models import Authors
		
		return Authors.objects.filter(
			Exists(
				obj.articles.filter(authors=OuterRef('pk'))
			)
		).count()
	
	def get_top_authors(self, obj):
		"""Optimized top authors calculation avoiding complex JOINs"""
		# Get author parameters from serializer context
		context = self.context
		author_params = context.get('author_params', {})
		
		# Check if author data should be included
		if not author_params.get('include_authors', False):
			return []
		
		# Get parameters
		max_authors = author_params.get('max_authors', 10)
		date_filters = author_params.get('date_filters', {})
		
		# Use a more efficient approach with subqueries instead of complex JOINs
		# Get article IDs for this category first
		article_ids = obj.articles.values_list('article_id', flat=True)
		
		# Apply date filters if needed
		if date_filters:
			articles_qs = obj.articles.all()
			# Adjust the date filter keys for the Articles model
			articles_date_filters = {}
			for key, value in date_filters.items():
				if key.startswith('articles__'):
					articles_date_filters[key.replace('articles__', '')] = value
				else:
					articles_date_filters[f'{key}'] = value
			if articles_date_filters:
				articles_qs = articles_qs.filter(**articles_date_filters)
			article_ids = articles_qs.values_list('article_id', flat=True)
		
		# Get authors with article counts using a simpler subquery approach
		# This avoids the complex GROUP BY queries that hang the database
		from django.db.models import Subquery
		from gregory.models import Authors
		
		top_authors = Authors.objects.filter(
			articles__article_id__in=Subquery(article_ids)
		).annotate(
			category_articles_count=Count('articles', filter=Q(articles__article_id__in=Subquery(article_ids)), distinct=True)
		).order_by('-category_articles_count')[:max_authors]
		
		return CategoryTopAuthorSerializer(top_authors, many=True).data
	
	def get_monthly_counts(self, obj):
		"""Get monthly counts of articles and trials when requested"""
		# Get monthly counts parameters from serializer context
		context = self.context
		monthly_params = context.get('monthly_counts_params', {})
		
		# Check if monthly counts should be included
		if not monthly_params.get('include_monthly_counts', False):
			return None
		
		# Get ML threshold
		ml_threshold = monthly_params.get('ml_threshold', 0.5)
		
		# Import here to avoid circular imports
		from gregory.models import MLPredictions
		from django.db.models import F, Max
		from django.db.models.functions import TruncMonth
		
		# Monthly article counts
		articles = obj.articles.all()
		articles = articles.annotate(month=TruncMonth('published_date'))
		article_counts = articles.values('month').annotate(count=Count('article_id')).order_by('month')
		article_counts = list(article_counts.values('month', 'count'))

		# Get available ML models for this category by getting distinct algorithms from latest predictions
		# Get the latest prediction date for each article-algorithm combination
		latest_predictions_subquery = MLPredictions.objects.filter(
			article__team_categories=obj,
			algorithm__isnull=False
		).values('article_id', 'algorithm').annotate(
			latest_date=Max('created_date')
		)
		
		# Get the actual latest predictions
		latest_prediction_ids = []
		for pred_info in latest_predictions_subquery:
			latest_pred = MLPredictions.objects.filter(
				article_id=pred_info['article_id'],
				algorithm=pred_info['algorithm'],
				created_date=pred_info['latest_date']
			).first()
			if latest_pred:
				latest_prediction_ids.append(latest_pred.id)
		
		# Get available models from these latest predictions
		available_models = MLPredictions.objects.filter(
			id__in=latest_prediction_ids
		).values_list('algorithm', flat=True).distinct()
		available_models = list(available_models)
		
		# Monthly articles with ML predictions above threshold for each model (latest predictions only)
		ml_counts_by_model = {}
		for model in available_models:
			# Get latest prediction IDs for this specific model
			latest_model_predictions_subquery = MLPredictions.objects.filter(
				article__team_categories=obj,
				algorithm=model
			).values('article_id').annotate(
				latest_date=Max('created_date')
			)
			
			latest_model_prediction_ids = []
			for pred_info in latest_model_predictions_subquery:
				latest_pred = MLPredictions.objects.filter(
					article_id=pred_info['article_id'],
					algorithm=model,
					created_date=pred_info['latest_date'],
					probability_score__gte=ml_threshold
				).first()
				if latest_pred:
					latest_model_prediction_ids.append(pred_info['article_id'])
			
			# Get articles with latest predictions above threshold for this model
			articles_with_ml = obj.articles.filter(article_id__in=latest_model_prediction_ids)
			articles_with_ml = articles_with_ml.annotate(month=TruncMonth('published_date'))
			ml_article_counts = articles_with_ml.values('month').annotate(count=Count('article_id', distinct=True)).order_by('month')
			ml_counts_by_model[model] = list(ml_article_counts.values('month', 'count'))

		# Deduplicated monthly counts of relevant articles: an article is relevant when
		# the latest prediction of at least one model clears the threshold, and it is
		# counted once per month no matter how many models flagged it. This is the
		# per-month size of the union of the per-model series above, so it can never
		# exceed monthly_article_counts.
		latest_prediction_ids = MLPredictions.objects.filter(
			article__team_categories=obj,
			algorithm__isnull=False
		).order_by(
			'article_id', 'algorithm', '-created_date', F('probability_score').desc(nulls_last=True)
		).distinct('article_id', 'algorithm').values('id')

		relevant_article_ids = MLPredictions.objects.filter(
			id__in=latest_prediction_ids,
			probability_score__gte=ml_threshold
		).values_list('article_id', flat=True)

		relevant_articles = obj.articles.filter(
			article_id__in=relevant_article_ids,
			published_date__isnull=False
		).annotate(month=TruncMonth('published_date'))
		relevant_counts = relevant_articles.values('month').annotate(
			count=Count('article_id', distinct=True)
		).order_by('month')
		relevant_counts = list(relevant_counts.values('month', 'count'))

		# Monthly trial counts
		trials = obj.trials.all()
		trials = trials.annotate(month=TruncMonth('published_date'))
		trial_counts = trials.values('month').annotate(count=Count('trial_id')).order_by('month')
		trial_counts = list(trial_counts.values('month', 'count'))

		return {
			'ml_threshold': ml_threshold,
			'available_models': available_models,
			'monthly_article_counts': article_counts,
			'monthly_ml_article_counts_by_model': ml_counts_by_model,
			'monthly_relevant_article_counts': relevant_counts,
			'monthly_trial_counts': trial_counts,
		}

class ArticleAuthorSerializer(serializers.ModelSerializer):
	country = serializers.SerializerMethodField()
	full_name = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = ['author_id', 'given_name', 'family_name', 'full_name', 'ORCID', 'country']

	def get_country(self, obj):
		# Return the country code or name
		return obj.country.code if obj.country else None
		
	def get_full_name(self, obj):
		return obj.full_name

class ArticleSerializer(OrgScopedSerializerMixin, serializers.HyperlinkedModelSerializer):
	sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')
	team_categories = TeamCategorySerializer(many=True, read_only=True)
	authors = ArticleAuthorSerializer(many=True, read_only=True)
	teams = TeamSerializer(many=True, read_only=True)
	subjects = SubjectsSerializer(many=True, read_only=True)
	ml_predictions = MLPredictionsSerializer(many=True, read_only=True, source='ml_predictions_detail')
	article_subject_relevances = ArticleSubjectRelevanceSerializer(many=True, read_only=True)
	clinical_trials = serializers.SerializerMethodField()
	takeaways = serializers.SerializerMethodField()
	summary_plain_english = serializers.SerializerMethodField()

	# Omit these fields from the response when there is no organisation context
	_per_org_fields = ['takeaways', 'summary_plain_english']

	class Meta:
		model = Articles
		depth = 1
		fields = [
			'article_id', 'title', 'summary', 'summary_plain_english', 'link', 'links', 'published_date', 'sources', 'teams',
			'subjects', 'publisher', 'container_title', 'authors',
			'discovery_date', 'article_subject_relevances',
			'doi', 'access', 'takeaways', 'team_categories', 'ml_predictions', 'clinical_trials',
		]
		read_only_fields = ('discovery_date', 'ml_predictions', 'clinical_trials')

	def get_clinical_trials(self, obj):
		"""Get trials referenced in the article"""
		references = ArticleTrialReference.objects.filter(article=obj)
		trials = [ref.trial for ref in references]
		return TrialReferenceSerializer(trials, many=True).data

	def _get_org_content(self, obj, org):
		"""Return the caller-org's ArticleOrgContent for *obj*, or None.

		Uses the per-org prefetch attached by ``ArticleViewSet.get_queryset``
		when present (zero extra queries per row); falls back to a single
		``.get()`` lookup for detail responses and other callers that didn't
		prefetch.  Result is cached per serializer instance so calling this
		from both ``get_takeaways`` and ``get_summary_plain_english`` only
		costs one lookup.
		"""
		if not hasattr(self, '_org_content_cache'):
			self._org_content_cache = {}
		key = (obj.pk, org.pk)
		if key in self._org_content_cache:
			return self._org_content_cache[key]

		prefetched = getattr(obj, '_prefetched_org_contents', None)
		if prefetched is not None:
			match = next((c for c in prefetched if c.organization_id == org.pk), None)
			self._org_content_cache[key] = match
			return match

		try:
			content = obj.org_contents.get(organization=org)
		except ArticleOrgContent.DoesNotExist:
			content = None
		self._org_content_cache[key] = content
		return content

	def get_takeaways(self, obj):
		"""Return takeaways from ArticleOrgContent for the caller's org, or None."""
		org = _resolve_per_org_fields_org(self.context.get('request'))
		if org is None:
			return None  # field will be popped in to_representation
		content = self._get_org_content(obj, org)
		return content.takeaways if content else None

	def get_summary_plain_english(self, obj):
		"""Return plain-English summary from ArticleOrgContent for the caller's org, or None."""
		org = _resolve_per_org_fields_org(self.context.get('request'))
		if org is None:
			return None  # field will be popped in to_representation
		content = self._get_org_content(obj, org)
		return content.summary_plain_english if content else None

class TrialSerializer(OrgScopedSerializerMixin, serializers.HyperlinkedModelSerializer):
	sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')
	team_categories = TeamCategorySerializer(many=True, read_only=True)
	articles = serializers.SerializerMethodField()
	takeaways = serializers.SerializerMethodField()
	summary_plain_english = serializers.SerializerMethodField()

	# Omit these fields from the response when there is no organisation context
	_per_org_fields = ['takeaways', 'summary_plain_english']

	class Meta:
		model = Trials
		fields = [
			'trial_id', 'title', 'summary', 'summary_plain_english', 'ctg_detailed_description',
			'published_date', 'discovery_date', 'last_updated', 'link', 'links', 'sources',
			'identifiers', 'team_categories', 'export_date', 'internal_number', 'last_refreshed_on',
			'acronym', 'scientific_title', 'primary_sponsor', 'secondary_sponsor', 'sponsor_type',
			'prospective_registration', 'date_registration',
			'source_register', 'recruitment_status', 'other_records', 'inclusion_agemin',
			'inclusion_agemax', 'inclusion_gender', 'date_enrollement', 'target_size',
			'study_type', 'study_design', 'phase', 'countries', 'contact_firstname',
			'contact_lastname', 'contact_address', 'contact_email', 'contact_tel',
			'contact_affiliation', 'inclusion_criteria', 'exclusion_criteria', 'condition',
			'intervention', 'primary_outcome', 'secondary_outcome', 'secondary_id',
			'source_support', 'ethics_review_status', 'ethics_review_approval_date',
			'ethics_review_contact_name', 'ethics_review_contact_address', 'ethics_review_contact_phone',
			'ethics_review_contact_email', 'results_date_completed', 'results_url_link',
			'results_yes_no', 'results_ipd_plan', 'results_ipd_description',
			# EU Clinical Trials (CTIS) fields
			'therapeutic_areas', 'country_status', 'trial_region', 'results_posted',
			'overall_decision_date', 'countries_decision_date',
			'takeaways', 'articles'
		]
		read_only_fields = ('discovery_date', 'articles')

	def get_articles(self, obj):
		"""Get articles that reference this trial.

		Uses the prefetched ``article_references`` cache when available (populated
		by TrialViewSet/AllTrialViewSet via prefetch_related('article_references__article'))
		to avoid one query per trial on list responses.
		"""
		references = obj.article_references.all()
		articles = [ref.article for ref in references]
		return ArticleReferenceSerializer(articles, many=True).data

	def _get_org_content(self, obj, org):
		"""Return the caller-org's TrialOrgContent for *obj*, or None.

		Uses the per-org prefetch attached by ``TrialViewSet.get_queryset``
		when present (zero extra queries per row); falls back to a single
		``.get()`` lookup for detail responses and other callers that didn't
		prefetch.  Result is cached per serializer instance.
		"""
		if not hasattr(self, '_org_content_cache'):
			self._org_content_cache = {}
		key = (obj.pk, org.pk)
		if key in self._org_content_cache:
			return self._org_content_cache[key]

		prefetched = getattr(obj, '_prefetched_org_contents', None)
		if prefetched is not None:
			match = next((c for c in prefetched if c.organization_id == org.pk), None)
			self._org_content_cache[key] = match
			return match

		try:
			content = obj.org_contents.get(organization=org)
		except TrialOrgContent.DoesNotExist:
			content = None
		self._org_content_cache[key] = content
		return content

	def get_takeaways(self, obj):
		"""Return takeaways from TrialOrgContent for the caller's org, or None."""
		org = _resolve_per_org_fields_org(self.context.get('request'))
		if org is None:
			return None  # field will be popped in to_representation
		content = self._get_org_content(obj, org)
		return content.takeaways if content else None

	def get_summary_plain_english(self, obj):
		"""Return plain-English summary from TrialOrgContent for the caller's org, or None."""
		org = _resolve_per_org_fields_org(self.context.get('request'))
		if org is None:
			return None  # field will be popped in to_representation
		content = self._get_org_content(obj, org)
		return content.summary_plain_english if content else None

class SourceSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = Sources
		fields = ['source_id', 'source_for', 'name', 'description', 'link', 'subject_id', 'team_id']

class AuthorSerializer(serializers.ModelSerializer):
	articles_count = serializers.SerializerMethodField()
	country = serializers.SerializerMethodField()
	articles_list = serializers.SerializerMethodField()

	class Meta:
		model = Authors
		fields = ['author_id', 'given_name', 'family_name', 'full_name', 'ORCID', 'country', 'articles_count', 'articles_list']

	def get_articles_count(self, obj):
		request = self.context.get('request')
		# When org-visibility is active, always compute the scoped count so the
		# value is correct even if an un-scoped article_count was annotated by
		# an earlier queryset stage (e.g. a cached queryset without visibility).
		if request is not None and hasattr(request, 'visible_org_ids'):
			return obj.articles_set.filter(
				teams__organization_id__in=request.visible_org_ids
			).distinct().count()
		# No visibility active — use the pre-annotated count for efficiency.
		if hasattr(obj, 'article_count'):
			return obj.article_count
		return obj.articles_set.count()

	def get_country(self, obj):
		# Return the country code or name
		return obj.country.code if obj.country else None

	def get_articles_list(self, obj):
		site = get_site()
		if not site:
			return ""
		
		# If the domain already has 'api.' subdomain, use it as-is
		# Otherwise, add 'api.' prefix
		domain = site.domain
		if domain.startswith('api.'):
			base_url = f"https://{domain}/articles/?author_id="
		else:
			base_url = f"https://api.{domain}/articles/?author_id="
		
		return base_url + str(obj.author_id)

# Simple Trial serializer for use in Article references
class TrialReferenceSerializer(serializers.ModelSerializer):
	class Meta:
		model = Trials
		fields = ['trial_id', 'title', 'summary', 'link']

# Simple Article serializer for use in Trial references
class ArticleReferenceSerializer(serializers.ModelSerializer):
	class Meta:
		model = Articles
		fields = ['article_id', 'title', 'summary', 'link']


class ArticlesByCategoryAndTeamSerializer(serializers.ModelSerializer):
	articles = ArticleSerializer(many=True, read_only=True)
	team = TeamSerializer(read_only=True)
	category = TeamCategorySerializer(read_only=True, source='self')

	class Meta:
		model = TeamCategory
		fields = ['id', 'team', 'category', 'articles']


class OrganizationSerializer(serializers.ModelSerializer):
	"""Minimal serializer for the Organizations list/detail endpoints."""
	class Meta:
		model = Organization
		fields = ['id', 'name', 'slug', 'is_active']