from django.contrib.auth.models import User, Group
from django.db.models.fields import SlugField
from rest_framework import serializers
from gregory.models import Articles, Trials, Sources, Authors

class ArticleSerializer(serializers.HyperlinkedModelSerializer):
	source = serializers.SlugRelatedField(many=False, read_only=True, slug_field='name')
	authors = serializers.SlugRelatedField(many=True, read_only=True, slug_field='full_name')

	class Meta:
		model = Articles
		fields = ['article_id','title','summary','link','published_date','source','authors','relevant','ml_prediction_gnb','ml_prediction_lr','discovery_date','noun_phrases','doi']
		read_only_fields = ('discovery_date',)
		
class TrialSerializer(serializers.HyperlinkedModelSerializer):
	source = serializers.SlugRelatedField(many=False, read_only=True, slug_field='name')

	class Meta:
		model = Trials
		fields = ['trial_id','title','summary','published_date','discovery_date','link','source','relevant']
		read_only_fields = ('discovery_date',)
		
class SourceSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = Sources
		fields = ['name','source_id','source_for','link']

class AuthorSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = Authors
		fields = ['given_name','family_name','ORCID']

class CountArticlesSerializer(serializers.ModelSerializer):
	articles_count = serializers.SerializerMethodField()

	class Meta:
		model = Articles
		fields = ( 'articles_count',)   

	def get_articles_count(self, obj):
		return Articles.objects.all().count()