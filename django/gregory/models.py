
from cryptography.fernet import Fernet
from django_countries.fields import CountryField
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.text import slugify
from organizations.models import Organization, OrganizationUser
from simple_history.models import HistoricalRecords
import base64
from django.conf import settings 
from django.db.models.functions import Lower

class Authors(models.Model):
	author_id = models.AutoField(primary_key=True)
	family_name = models.CharField(blank=False,null=False, max_length=150)
	given_name = models.CharField(blank=False,null=False, max_length=150)
	ORCID = models.CharField(blank=True, null=True, max_length=150, unique=True)
	country = CountryField(blank=True, null=True)  # New field
	orcid_check = models.DateTimeField(blank=True, null=True)
	history = HistoricalRecords()

	def __str__(self):
		full_name = (self.given_name,self.family_name)
		object_name = ' '.join(full_name)
		return str(object_name)
	class Meta:
		verbose_name_plural = 'authors'
		db_table = 'authors'
	@property
	def full_name(self):
		return self.given_name+" "+self.family_name

class TeamCategory(models.Model):
	team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='team_categories')
	subjects = models.ManyToManyField('Subject', related_name='team_subjects', blank=True)
	category_name = models.CharField(max_length=200)
	category_description = models.TextField(blank=True, null=True)
	category_slug = models.SlugField(blank=True, null=True, unique=True)
	category_terms = ArrayField(models.CharField(max_length=100), default=list, verbose_name='Terms to include in category (comma separated)', help_text="Add terms separated by commas.")

	def save(self, *args, **kwargs):
		if not self.category_slug:
				self.category_slug = slugify(self.category_name)
		super().save(*args, **kwargs)

	def __str__(self):
		return f"{self.team.name} - {self.category_name}"

	def article_count(self):
		return self.articles.count()

	class Meta:
		unique_together = (('team', 'category_slug'),)
		verbose_name_plural = 'team categories'
		db_table = 'team_categories'

class Entities(models.Model):
	entity = models.TextField()
	label = models.TextField()

	class Meta:
		managed = True
		verbose_name_plural = 'entities'
		db_table = 'entities'

class Subject(models.Model):
	subject_name = models.CharField(blank=False, null=False, max_length=50)
	description = models.TextField(blank=True, null=True)
	team = models.ForeignKey(
			'Team', 
			on_delete=models.CASCADE,  # Not sure which would be the best option here
			null=True,
			blank=False,  
			related_name='subjects'  # Helps in querying from the Team model, e.g., team.subjects.all()
	)

	def __str__(self):
		return str(self.team) + " - " + self.subject_name

	class Meta:
		managed = True
		verbose_name_plural = 'subjects'
		db_table = 'subjects'


class Sources(models.Model):
	TABLES = [('science paper', 'Science Paper'),('trials','Trials'),('news article','News Article')]
	METHODS = [('rss', 'RSS'), ('scrape', 'Scrape'), ('manual', 'Manual submission')]
	active = models.BooleanField(default=True)
	source_id = models.AutoField(primary_key=True)
	source_for = models.CharField(choices=TABLES, max_length=50, default='science paper')
	name = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	language = models.TextField(blank=True, null=True)
	subject = models.ForeignKey(Subject,on_delete=models.PROTECT,null=True,blank=True,unique=False)
	method = models.CharField(choices=METHODS, max_length=10, default='rss')
	ignore_ssl = models.BooleanField(default=False)
	description = models.TextField(blank=True, null=True)
	team = models.ForeignKey(
		'Team', 
		on_delete=models.CASCADE,  # Not sure which would be the best option here
		null=True,
		blank=False,  
		related_name='sources'  # Helps in querying from the Team model, e.g., team.sources.all()
	)

	def __str__(self):
		return self.name or ""

	class Meta:
		managed = True
		verbose_name_plural = 'sources'
		db_table = 'sources'

class Articles(models.Model):
	KINDS = [('science paper', 'Science Paper'),('news article','News Article')]
	ACCESS_OPTIONS = [('unknown','Unknown'),('open','Open'),('restricted','Restricted')]
	article_id = models.AutoField(primary_key=True)
	title = models.TextField(blank=False, null=False, unique=True)
	link = models.URLField(blank=False, null=False, max_length=2000)
	doi = models.CharField(max_length=280, blank=True, null=True)
	summary = models.TextField(blank=True, null=True)
	sources = models.ManyToManyField(Sources, blank=True)
	published_date = models.DateTimeField(blank=True, null=True)
	discovery_date = models.DateTimeField(auto_now_add=True)
	authors = models.ManyToManyField(Authors, blank=True)
	team_categories = models.ManyToManyField('TeamCategory', related_name='articles', blank=True)
	entities = models.ManyToManyField('Entities')
	relevant = models.BooleanField(blank=True, null=True)
	ml_predictions = models.ManyToManyField('MLPredictions', blank=True)
	noun_phrases = models.JSONField(blank=True, null=True)
	sent_to_subscribers = models.BooleanField(blank=True, null=True)
	kind = models.CharField(choices=KINDS, max_length=50,default='science paper')
	access = models.CharField(choices=ACCESS_OPTIONS, max_length=50, default=None, null=True)
	publisher = models.CharField(max_length=150, blank=True, null=True, default=None)
	container_title = models.CharField(max_length=150, blank=True, null=True, default=None)
	crossref_check = models.DateTimeField(blank=True, null=True)
	takeaways = models.TextField(blank=True, null=True)
	history = HistoricalRecords()
	subjects = models.ManyToManyField('Subject', related_name='articles')  # Ensuring that article has one or more subjects 
	teams = models.ManyToManyField('Team', related_name='articles')  # Allows an article to belong to one or more teams
	retracted = models.BooleanField(default=False)
	def __str__(self):
		return str(self.article_id)

	class Meta:
		managed = True
		unique_together = (('title', 'link'),)
		verbose_name_plural = 'articles'
		db_table = 'articles'
		ordering = ['-discovery_date']

class Trials(models.Model):
	trial_id = models.AutoField(primary_key=True)
	discovery_date = models.DateTimeField(blank=True, null=True)
	last_updated = models.DateTimeField(auto_now=True, null=True)
	title = models.TextField(blank=False,null=False, unique=True)
	summary = models.TextField(blank=True, null=True)
	link = models.URLField(blank=False, null=False, max_length=2000)
	published_date = models.DateTimeField(blank=True, null=True)
	sources = models.ManyToManyField('Sources', blank=True)
	relevant = models.BooleanField(blank=True, null=True)
	team_categories = models.ManyToManyField('TeamCategory', related_name='trials')
	identifiers = models.JSONField(blank=True, null=True)
	teams = models.ManyToManyField('Team', related_name='trials')
	subjects = models.ManyToManyField('Subject', related_name='trials')
	history = HistoricalRecords()

	# WHO Fields
	export_date = models.DateTimeField(null=True, blank=True)
	internal_number = models.CharField(max_length=100, null=True, blank=True)
	last_refreshed_on = models.DateField(null=True, blank=True)
	scientific_title = models.TextField(null=True, blank=True)
	primary_sponsor = models.TextField(null=True, blank=True)
	retrospective_flag = models.CharField(max_length=10, null=True, blank=True)
	date_registration = models.DateField(null=True, blank=True)
	source_register = models.CharField(max_length=200, null=True, blank=True)
	recruitment_status = models.CharField(max_length=200, null=True, blank=True)
	inclusion_agemin = models.CharField(max_length=100, null=True, blank=True)
	inclusion_agemax = models.CharField(max_length=100, null=True, blank=True)
	inclusion_gender = models.CharField(max_length=500, null=True, blank=True)
	date_enrollement = models.DateField(null=True, blank=True)
	target_size = models.TextField(null=True, blank=True)
	study_type = models.TextField(null=True, blank=True)
	study_design = models.TextField(null=True, blank=True)  # Changed to TextField
	phase = models.TextField(null=True, blank=True)  # Changed to TextField
	countries = models.TextField(null=True, blank=True)
	contact_firstname = models.TextField(null=True, blank=True)
	contact_lastname = models.TextField(null=True, blank=True)
	contact_address = models.TextField(null=True, blank=True)
	contact_email = models.EmailField(max_length=1000, null=True, blank=True)
	contact_tel = models.TextField(null=True, blank=True)
	contact_affiliation = models.TextField(null=True, blank=True)
	inclusion_criteria = models.TextField(null=True, blank=True)  # Changed to TextField
	exclusion_criteria = models.TextField(null=True, blank=True)  # Changed to TextField
	condition = models.TextField(null=True, blank=True)  # Changed to TextField
	intervention = models.TextField(null=True, blank=True)
	primary_outcome = models.TextField(null=True, blank=True)
	secondary_outcome = models.TextField(null=True, blank=True)
	secondary_id = models.TextField(null=True, blank=True)
	source_support = models.TextField(null=True, blank=True)
	ethics_review_status = models.TextField(null=True, blank=True)
	ethics_review_approval_date = models.DateField(null=True, blank=True)
	ethics_review_contact_name = models.EmailField(max_length=1000, null=True, blank=True)
	ethics_review_contact_address = models.TextField(null=True, blank=True)
	ethics_review_contact_phone = models.TextField(null=True, blank=True)
	ethics_review_contact_email = models.EmailField(max_length=1000, null=True, blank=True)
	results_date_completed = models.DateField(null=True, blank=True)
	results_url_link = models.URLField(null=True, blank=True, max_length=2000)
	ml_predictions = models.ManyToManyField('MLPredictions', blank=True)

	# Fields for euclinicaltrials.eu data
	therapeutic_areas = models.TextField(null=True, blank=True)
	country_status = models.TextField(null=True, blank=True)
	trial_region = models.CharField(max_length=500, null=True, blank=True)
	results_posted = models.BooleanField(default=False)
	overall_decision_date = models.DateField(null=True, blank=True)
	countries_decision_date = models.JSONField(null=True, blank=True)
	sponsor_type = models.CharField(max_length=500, null=True, blank=True)

	# New field added
	other_records = models.CharField(max_length=200, null=True, blank=True)

	def __str__(self):
		return str(self.trial_id)

	class Meta:
		managed = True
		verbose_name_plural = 'trials'
		db_table = 'trials'
		constraints = [
			models.UniqueConstraint(
				Lower('title'),
				name='unique_title_case_insensitive'
			)
		]
def get_fernet():
	try:
		secret_key = settings.FERNET_SECRET_KEY
		return Fernet(secret_key)
	except Exception as e:
		raise ValueError("FERNET_SECRET_KEY is not properly configured in settings.") from e

class EncryptedTextField(models.TextField):
	"""
	Custom TextField that encrypts data before saving to the database
	and decrypts it when reading from the database.
	"""
	def from_db_value(self, value, expression, connection):
		if value is None:
			return value
		fernet = get_fernet()
		return fernet.decrypt(base64.b64decode(value)).decode()

	def get_prep_value(self, value):
		if value is None:
			return value
		if not isinstance(value, str):
			raise ValueError("Only strings can be encrypted.")
		fernet = get_fernet()
		return base64.b64encode(fernet.encrypt(value.encode())).decode()

class Team(Organization):
	class Meta:
		proxy = True

	@property
	def members(self):
		# Assuming TeamMember links back to Organization via an 'organization' field
		# and each TeamMember instance has a related 'user' object
		return [member.user for member in TeamMember.objects.filter(organization=self)]

class TeamCredentials(models.Model):
	team = models.OneToOneField(
		'Team',
		on_delete=models.CASCADE,
		related_name="credentials",
		help_text="The team associated with these credentials."
	)
	# ORCID for teams is not necessary at the moment.
	# orcid_client_id = EncryptedTextField(
	# 	blank=True,
	# 	null=True,
	# 	help_text="ORCID Client ID for this team."
	# )
	# orcid_client_secret = EncryptedTextField(
	# 	blank=True,
	# 	null=True,
	# 	help_text="ORCID Client Secret for this team."
	# )
	postmark_api_token = EncryptedTextField(
		blank=True,
		null=True,
		help_text="Postmark API Token for this team."
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"Credentials for Team: {self.team.name}"

	class Meta:
		verbose_name = "Team Credential"
		verbose_name_plural = "Team Credentials"

class TeamMember(OrganizationUser):
	class Meta:
		proxy = True

class MLPredictions(models.Model):
	created_date = models.DateTimeField(auto_now_add=True)
	subject = models.ForeignKey('Subject', on_delete=models.CASCADE, related_name='ml_subject_predictions')
	gnb = models.BooleanField(blank=True, null=True,
		verbose_name="Gaussian Naive Bayes Prediction",
		help_text="Indicates the Machine Learning prediction made using Gaussian Naive Bayes."
	)
	lr = models.BooleanField(blank=True, null=True,
		verbose_name="Logistic Regression Prediction",
		help_text="Indicates the Machine Learning prediction made using Logistic Regression."
	)
	lsvc = models.BooleanField(blank=True,null=True,
		verbose_name="Linear Support Vector Classification Prediction",
		help_text="Indicates the Machine Learning prediction made using Linear Support Vector Classification."
	)
	mnb = models.BooleanField(blank=True, null=True,
		verbose_name = 'Multinomial Naive Bayes',
		help_text='indicates the Machine Learning prediction using Multinomial Naive Bayes.'
	)

class ArticleSubjectRelevance(models.Model):
	article = models.ForeignKey(Articles, related_name='article_subject_relevances', on_delete=models.CASCADE)
	subject = models.ForeignKey('Subject', on_delete=models.CASCADE)
	is_relevant = models.BooleanField(default=False, help_text="Indicates if the article is relevant for the subject.")

	class Meta:
		unique_together = ('article', 'subject')
		verbose_name_plural = 'article subject relevances'

	def __str__(self):
		relevance_status = "Relevant" if self.is_relevant else "Not Relevant"
		return f"{self.article.title} - {self.subject.subject_name}: {relevance_status}"


