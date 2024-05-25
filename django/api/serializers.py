from rest_framework import serializers
from gregory.models import Articles, Trials, Sources, Authors, Categories, Subject, Team, MLPredictions, ArticleSubjectRelevance,TeamCategory
from organizations.models import Organization
from sitesettings.models import CustomSetting
from django.contrib.sites.models import Site
from django.conf import settings

customsettings = CustomSetting.objects.get(site=settings.SITE_ID)
site = Site.objects.get(pk=settings.SITE_ID)

class TeamCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamCategory
        fields = ['id', 'category_name', 'category_description', 'category_slug', 'category_terms']
class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ['id', 'subject_name', 'description']

class ArticleSubjectRelevanceSerializer(serializers.ModelSerializer):
    subject = SubjectSerializer(read_only=True)
    subject_id = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all(), source='subject', write_only=True)

    class Meta:
        model = ArticleSubjectRelevance
        fields = ['subject', 'subject_id', 'is_relevant']

class MLPredictionsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MLPredictions
        fields = ['gnb', 'lr', 'lsvc', 'mnb', 'created_date', 'subject']

class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ['id', 'name']

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Categories
        fields = ['category_id', 'category_description', 'category_name', 'category_slug', 'category_terms', 'article_count']

class ArticleAuthorSerializer(serializers.ModelSerializer):
    country = serializers.SerializerMethodField()

    class Meta:
        model = Authors
        fields = ['author_id', 'given_name', 'family_name', 'ORCID', 'country']

    def get_country(self, obj):
        # Return the country code or name
        return obj.country.code if obj.country else None

class ArticleSerializer(serializers.HyperlinkedModelSerializer):
    sources = serializers.SlugRelatedField(many=True, read_only=True, slug_field='name')
    categories = CategorySerializer(many=True, read_only=True)
    team_categories = TeamCategorySerializer(many=True, read_only=True)
    authors = ArticleAuthorSerializer(many=True, read_only=True)
    teams = TeamSerializer(many=True, read_only=True)
    subjects = SubjectSerializer(many=True, read_only=True,)
    ml_predictions = MLPredictionsSerializer(many=True, read_only=True, source='ml_predictions.all')
    article_subject_relevances = ArticleSubjectRelevanceSerializer(many=True, read_only=True)

    class Meta:
        model = Articles
        depth = 1
        fields = [
            'article_id', 'title', 'summary', 'link', 'published_date', 'sources', 'teams', 
            'subjects', 'publisher', 'container_title', 'authors', 'relevant', 
            'ml_prediction_gnb', 'ml_prediction_lr', 'ml_prediction_lsvc', 'discovery_date',
            'article_subject_relevances', 
            'noun_phrases', 'doi', 'access', 'takeaways', 'categories', 'team_categories', 'ml_predictions',
        ]
        read_only_fields = ('discovery_date', 'ml_predictions', 'ml_prediction_gnb', 'ml_prediction_lr', 'ml_prediction_lsvc', 'noun_phrases', 'takeaways')

class TrialSerializer(serializers.HyperlinkedModelSerializer):
    source = serializers.SlugRelatedField(read_only=True, slug_field='name')
    categories = CategorySerializer(many=True, read_only=True)
    team_categories = TeamCategorySerializer(many=True, read_only=True)

    class Meta:
        model = Trials
        fields = [
            'trial_id', 'title', 'summary', 'published_date', 'discovery_date', 'link', 'source', 'relevant', 
            'identifiers', 'categories', 'export_date', 'internal_number', 'last_refreshed_on', 
            'scientific_title', 'primary_sponsor', 'retrospective_flag', 'date_registration', 
            'source_register', 'recruitment_status', 'other_records', 'inclusion_agemin', 
            'inclusion_agemax', 'inclusion_gender', 'date_enrollement', 'target_size', 
            'study_type', 'study_design', 'phase', 'countries', 'contact_firstname', 
            'contact_lastname', 'contact_address', 'contact_email', 'contact_tel', 
            'contact_affiliation', 'inclusion_criteria', 'exclusion_criteria', 'condition', 
            'intervention', 'primary_outcome', 'secondary_outcome', 'secondary_id', 
            'source_support', 'ethics_review_status', 'ethics_review_approval_date', 
            'ethics_review_contact_name', 'ethics_review_contact_address', 'ethics_review_contact_phone', 
            'ethics_review_contact_email', 'results_date_completed', 'results_url_link'
        ]
        read_only_fields = ('discovery_date',)

class SourceSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Sources
        fields = ['source_id', 'source_for', 'name', 'description', 'link', 'language']

class AuthorSerializer(serializers.ModelSerializer):
    articles_count = serializers.SerializerMethodField()
    country = serializers.SerializerMethodField()
    articles_list = serializers.SerializerMethodField()

    class Meta:
        model = Authors
        fields = ['author_id', 'given_name', 'family_name', 'ORCID', 'country', 'articles_count', 'articles_list']

    def get_articles_count(self, obj):
        return obj.articles_set.count()
    def get_country(self, obj):
        # Return the country code or name
        return obj.country.code if obj.country else None
    def get_articles_list(self, obj):
        base_url = f"https://api.{site.domain}/articles/author/"
        return base_url + str(obj.author_id)

class CountArticlesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Articles
        fields = ('articles_count',)

    def get_articles_count(self, obj):
        return Articles.objects.count()


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = '__all__'