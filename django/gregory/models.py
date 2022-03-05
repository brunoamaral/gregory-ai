# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class Articles(models.Model):
	article_id = models.AutoField(primary_key=True)
	title = models.TextField(blank=True, null=True)
	summary = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	published_date = models.DateTimeField(blank=True, null=True)
	source = models.ForeignKey('Sources', models.DO_NOTHING, db_column='source', blank=True, null=True)
	relevant = models.BooleanField(blank=True, null=True)
	sent_to_admin = models.BooleanField(blank=True, null=True)
	ml_prediction_gnb = models.BooleanField(blank=True, null=True)
	ml_prediction_lr = models.BooleanField(blank=True, null=True)
	sent_to_subscribers = models.BooleanField(blank=True, null=True)
	discovery_date = models.DateTimeField()
	sent_to_twitter = models.BooleanField(blank=True, null=True)
	noun_phrases = models.TextField(blank=True, null=True)  # This field type is a guess.

	def __str__(self):
		return self.article_id

	class Meta:
		managed = False
		db_table = 'articles'
		verbose_name_plural = 'articles'


class Categories(models.Model):
	category_id = models.AutoField(primary_key=True)
	category_name = models.TextField(blank=True, null=True)

	class Meta:
		managed = False
		db_table = 'categories'
		verbose_name_plural = 'categories'


class Entities(models.Model):
	entity = models.TextField()
	label = models.TextField()

	class Meta:
		managed = False
		db_table = 'entities'
		verbose_name_plural = 'entities'


class RelArticlesCategories(models.Model):
	article = models.ForeignKey(Articles, models.DO_NOTHING)
	category = models.ForeignKey(Categories, models.DO_NOTHING)

	class Meta:
		managed = False
		db_table = 'rel_articles_categories'


class RelArticlesEntities(models.Model):
	entity = models.ForeignKey(Entities, models.DO_NOTHING, blank=True, null=True)
	article = models.ForeignKey(Articles, models.DO_NOTHING, blank=True, null=True)

	class Meta:
		managed = False
		db_table = 'rel_articles_entities'


class RelArticlesSources(models.Model):
	article = models.ForeignKey(Articles, models.DO_NOTHING, blank=True, null=True)
	source = models.ForeignKey('Sources', models.DO_NOTHING, blank=True, null=True)

	class Meta:
		managed = False
		db_table = 'rel_articles_sources'


class Sources(models.Model):
	source_id = models.AutoField(primary_key=True)
	name = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	language = models.TextField()
	subject = models.TextField()
	method = models.TextField()

	def __str__(self):
		return self.name

	class Meta:
		managed = False
		db_table = 'sources'
		verbose_name_plural = 'sources'

class Trials(models.Model):
	trial_id = models.AutoField(primary_key=True)
	discovery_date = models.DateTimeField(blank=True, null=True)
	title = models.TextField()
	summary = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	published_date = models.DateTimeField(blank=True, null=True)
	source = models.TextField(blank=True, null=True)
	relevant = models.BooleanField(blank=True, null=True)
	sent = models.BooleanField(blank=True, null=True)
	sent_to_twitter = models.BooleanField(blank=True, null=True)
	sent_to_subscribers = models.BooleanField(blank=True, null=True)

	def __str__(self):
		return str(self.trial_id) + ': ' + self.title

	class Meta:
		managed = False
		db_table = 'trials'
		verbose_name_plural = 'trials'