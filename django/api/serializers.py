from django.contrib.auth.models import User, Group
from django.db.models.fields import SlugField
from rest_framework import serializers
from gregory.models import Articles, Trials, Sources, Authors, Categories

class CategorySerializer(serializers.ModelSerializer):
	count_of_articles = serializers.SerializerMethodField()

	class Meta:
		model = Categories
		fields = ['category_id', 'category_description', 'category_name', 'category_slug', 'category_terms','count_of_articles']

	def get_count_of_articles(self, obj):
		return obj.article_count()

class ArticleSerializer(serializers.HyperlinkedModelSerializer):
	source = serializers.SlugRelatedField(many=False, read_only=True, slug_field='name')
	categories = CategorySerializer(many=True, read_only=True)
	class Meta:
		model = Articles
		depth = 1
		fields = ['article_id','title','summary','link','published_date','source','publisher','container_title','authors','relevant','ml_prediction_gnb','ml_prediction_lr','discovery_date','noun_phrases','doi','access','takeaways','categories']
		read_only_fields = ('discovery_date','ml_prediction_gnb','ml_prediction_lr','noun_phrases','takeaways')

class TrialSerializer(serializers.HyperlinkedModelSerializer):
	source = serializers.SlugRelatedField(many=False, read_only=True, slug_field='name')
	categories = CategorySerializer(many=True, read_only=True)

	class Meta:
		model = Trials
		fields = ['trial_id','title','summary','published_date','discovery_date','link','source','relevant','identifiers','categories']
		read_only_fields = ('discovery_date',)
		
class SourceSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = Sources
		fields = ['name','description','source_id','source_for','link']

class AuthorSerializer(serializers.HyperlinkedModelSerializer):
	articles_count = serializers.SerializerMethodField()
	class Meta:
		model = Authors
		fields = ['author_id','given_name','family_name','ORCID', 'articles_count']
	def get_articles_count(self, obj):
		return obj.articles_set.count()
	
class CountArticlesSerializer(serializers.ModelSerializer):
	articles_count = serializers.SerializerMethodField()

	class Meta:
		model = Articles
		fields = ( 'articles_count',)   

	def get_articles_count(self, obj):
		return Articles.objects.all().count()