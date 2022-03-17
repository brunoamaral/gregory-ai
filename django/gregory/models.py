from django.db import models
class Categories(models.Model):
	category_id = models.AutoField(primary_key=True)
	category_name = models.CharField(blank=True, null=True,max_length=200)
	category_description = models.TextField(blank=True, null=True)

	def __str__(self):
		return self.category_name

	class Meta:
		managed = True
		verbose_name_plural = 'categories'
		db_table = 'categories'

class Articles(models.Model):
	article_id = models.AutoField(primary_key=True)
	title = models.TextField(blank=False, null=False, unique=True)
	summary = models.TextField(blank=True, null=True)
	link = models.URLField(blank=False, null=False, max_length=2000)
	published_date = models.DateTimeField(blank=True, null=True)
	discovery_date = models.DateTimeField()
	source = models.ForeignKey('Sources', models.DO_NOTHING, db_column='source', blank=True, null=True)
	relevant = models.BooleanField(blank=True, null=True)
	ml_prediction_gnb = models.BooleanField(blank=True, null=True)
	ml_prediction_lr = models.BooleanField(blank=True, null=True)
	noun_phrases = models.JSONField(blank=True, null=True)
	categories = models.ManyToManyField(Categories)
	entities = models.ManyToManyField('Entities')
	sent_to_admin = models.BooleanField(blank=True, null=True)
	sent_to_subscribers = models.BooleanField(blank=True, null=True)
	sent_to_twitter = models.BooleanField(blank=True, null=True)
	doi = models.CharField(max_length=280, blank=True, null=True)

	def __str__(self):
		return str(self.article_id)

	class Meta:
		managed = True
		# unique_together = (('title', 'link'),)
		verbose_name_plural = 'articles'
		db_table = 'articles'


class Entities(models.Model):
	entity = models.TextField()
	label = models.TextField()


	class Meta:
		managed = True
		verbose_name_plural = 'entities'
		db_table = 'entities'
		unique_together = (('entity', 'label'),)



class Sources(models.Model):
	TABLES = [('articles', 'Articles'),('trials','Trials')]


	source_id = models.AutoField(primary_key=True)
	source_for = models.CharField(choices=TABLES, max_length=50, default='articles')
	name = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	language = models.TextField()
	subject = models.TextField()
	method = models.TextField()
	

	def __str__(self):
		return self.name

	class Meta:
		managed = True
		verbose_name_plural = 'sources'
		db_table = 'sources'


class Trials(models.Model):
	trial_id = models.AutoField(primary_key=True)
	discovery_date = models.DateTimeField(blank=True, null=True)
	title = models.TextField(blank=False,null=False, unique=True)
	summary = models.TextField(blank=True, null=True)
	link = models.URLField(blank=False, null=False, max_length=2000)
	published_date = models.DateTimeField(blank=True, null=True)
	source = models.ForeignKey('Sources', models.DO_NOTHING, db_column='source', blank=True, null=True)
	relevant = models.BooleanField(blank=True, null=True)
	sent = models.BooleanField(blank=True, null=True)
	sent_to_twitter = models.BooleanField(blank=True, null=True)
	sent_to_subscribers = models.BooleanField(blank=True, null=True)

	def __str__(self):
		return str(self.trial_id) 

	class Meta:
		managed = True
		verbose_name_plural = 'trials'
		db_table = 'trials'

