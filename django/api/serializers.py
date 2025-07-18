from rest_framework import serializers
from gregory.models import Articles, Trials, Sources, Authors, Subject, Team, MLPredictions, ArticleSubjectRelevance, TeamCategory, ArticleTrialReference
from organizations.models import Organization
from sitesettings.models import CustomSetting
from django.contrib.sites.models import Site
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

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
		fields = ['id', 'category_name', 'category_description', 'category_slug', 'category_terms']

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

class CategorySerializer(serializers.ModelSerializer):
	article_count_total = serializers.SerializerMethodField()
	trials_count_total = serializers.SerializerMethodField()
	
	class Meta:
		model = TeamCategory
		fields = ['id', 'category_description', 'category_name', 'category_slug', 'category_terms', 'article_count_total', 'trials_count_total']
	
	def get_article_count_total(self, obj):
		# If the queryset has annotated article_count, use it for efficiency
		if hasattr(obj, 'article_count_annotated'):
			return obj.article_count_annotated
		# Fallback to the model method
		return obj.article_count()
	
	def get_trials_count_total(self, obj):
		# If the queryset has annotated trials_count, use it for efficiency
		if hasattr(obj, 'trials_count_annotated'):
			return obj.trials_count_annotated
		# Fallback to the model method
		return obj.trials_count()

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

class ArticleSerializer(serializers.HyperlinkedModelSerializer):
	sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')
	team_categories = TeamCategorySerializer(many=True, read_only=True)
	authors = ArticleAuthorSerializer(many=True, read_only=True)
	teams = TeamSerializer(many=True, read_only=True)
	subjects = SubjectsSerializer(many=True, read_only=True)
	ml_predictions = MLPredictionsSerializer(many=True, read_only=True, source='ml_predictions_detail')
	article_subject_relevances = ArticleSubjectRelevanceSerializer(many=True, read_only=True)
	clinical_trials = serializers.SerializerMethodField()

	class Meta:
		model = Articles
		depth = 1
		fields = [
			'article_id', 'title', 'summary', 'link', 'published_date', 'sources', 'teams',
			'subjects', 'publisher', 'container_title', 'authors',
			'discovery_date', 'article_subject_relevances',
			'doi', 'access', 'takeaways', 'team_categories', 'ml_predictions', 'clinical_trials',
		]
		read_only_fields = ('discovery_date', 'ml_predictions', 'takeaways', 'clinical_trials')
	
	def get_clinical_trials(self, obj):
		"""Get trials referenced in the article"""
		references = ArticleTrialReference.objects.filter(article=obj)
		trials = [ref.trial for ref in references]
		return TrialReferenceSerializer(trials, many=True).data

class TrialSerializer(serializers.HyperlinkedModelSerializer):
	source = serializers.SlugRelatedField(read_only=True, slug_field='name')
	team_categories = TeamCategorySerializer(many=True, read_only=True)
	articles = serializers.SerializerMethodField()

	class Meta:
		model = Trials
		fields = [
			'trial_id', 'title', 'summary', 'published_date', 'discovery_date', 'link', 'source',
			'identifiers', 'team_categories', 'export_date', 'internal_number', 'last_refreshed_on',
			'scientific_title', 'primary_sponsor', 'retrospective_flag', 'date_registration',
			'source_register', 'recruitment_status', 'other_records', 'inclusion_agemin',
			'inclusion_agemax', 'inclusion_gender', 'date_enrollement', 'target_size',
			'study_type', 'study_design', 'phase', 'countries', 'contact_firstname',
			'contact_lastname', 'contact_address', 'contact_email', 'contact_tel',
			'contact_affiliation', 'inclusion_criteria', 'exclusion_criteria', 'condition',
			'intervention', 'primary_outcome', 'secondary_outcome', 'secondary_id',
			'source_support', 'ethics_review_status', 'ethics_review_approval_date',
			'ethics_review_contact_name', 'ethics_review_contact_address', 'ethics_review_contact_phone',
			'ethics_review_contact_email', 'results_date_completed', 'results_url_link', 'articles'
		]
		read_only_fields = ('discovery_date', 'articles')
		
	def get_articles(self, obj):
		"""Get articles that reference this trial"""
		references = ArticleTrialReference.objects.filter(trial=obj)
		articles = [ref.article for ref in references]
		return ArticleReferenceSerializer(articles, many=True).data

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
		# If the queryset has annotated article_count, use it for efficiency
		if hasattr(obj, 'article_count'):
			return obj.article_count
		# Fallback to counting all articles
		return obj.articles_set.count()

	def get_country(self, obj):
		# Return the country code or name
		return obj.country.code if obj.country else None

	def get_articles_list(self, obj):
		site = get_site()
		base_url = f"https://api.{site.domain}/articles/?author_id=" if site else ""
		return base_url + str(obj.author_id) if site else ""

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

class CountArticlesSerializer(serializers.ModelSerializer):
	class Meta:
		model = Articles
		fields = ('articles_count',)

	def get_articles_count(self, obj):
		return Articles.objects.count()

class ArticlesByCategoryAndTeamSerializer(serializers.ModelSerializer):
	articles = ArticleSerializer(many=True, read_only=True)
	team = TeamSerializer(read_only=True)
	category = TeamCategorySerializer(read_only=True, source='self')

	class Meta:
		model = TeamCategory
		fields = ['id', 'team', 'category', 'articles']