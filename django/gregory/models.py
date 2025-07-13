from cryptography.fernet import Fernet
from django_countries.fields import CountryField
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import GeneratedField
from django.db.models.functions import Upper
from django.utils.text import slugify
from django.utils import timezone
from organizations.models import Organization, OrganizationUser
from simple_history.models import HistoricalRecords
import base64
from django.db.models.functions import Lower

class Authors(models.Model):
	author_id = models.AutoField(primary_key=True)
	family_name = models.CharField(blank=False,null=False, max_length=150)
	given_name = models.CharField(blank=False,null=False, max_length=150)
	full_name = models.CharField(max_length=301, blank=True, null=True, db_index=True, help_text="Auto-generated from given_name and family_name")
	ORCID = models.CharField(blank=True, null=True, max_length=150, unique=True)
	country = CountryField(blank=True, null=True)  # New field
	orcid_check = models.DateTimeField(blank=True, null=True)
	history = HistoricalRecords()

	def save(self, *args, **kwargs):
		# Auto-populate full_name from given_name and family_name
		self.full_name = f"{self.given_name} {self.family_name}".strip()
		super().save(*args, **kwargs)

	def __str__(self):
		return self.full_name or f"{self.given_name} {self.family_name}"
	
	class Meta:
		verbose_name_plural = 'authors'
		db_table = 'authors'

class TeamCategory(models.Model):
	team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='team_categories', null=False, blank=False)
	subjects = models.ManyToManyField('Subject', related_name='team_subjects', blank=False)
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
	
	def trials_count(self):
		return self.trials.count()

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['team', 'category_slug'],
				name='unique_team_category_slug')
		]
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
	subject_slug = models.SlugField(editable=True)
	auto_predict = models.BooleanField(default=False, help_text='Enable automatic ML prediction for new articles')
	team = models.ForeignKey(
			'Team', 
			on_delete=models.CASCADE,  # Not sure which would be the best option here
			null=True,
			blank=False,  
			related_name='subjects'  # Helps in querying from the Team model, e.g., team.subjects.all()
	)

	def __str__(self):
		# More readable subject representation
		if self.team:
			# Keep it short - just show the subject name and team name (no organization)
			return f"{self.subject_name} [{self.team.name}]"
		else:
			return self.subject_name

	class Meta:
		managed = True
		verbose_name_plural = 'subjects'
		db_table = 'subjects'
		constraints = [
			models.UniqueConstraint(
				fields=['team', 'subject_slug'],
				name='unique_team_subject_slug')
		]

	def get_full_name(self):
		"""Return a full representation of the subject including team and organization"""
		if self.team and self.team.organization:
			return f"{self.subject_name} - {self.team.name} ({self.team.organization.name})"
		elif self.team:
			return f"{self.subject_name} - {self.team.name}"
		else:
			return self.subject_name

class Sources(models.Model):
	TABLES = [('science paper', 'Science Paper'),('trials','Trials'),('news article','News Article')]
	METHODS = [('rss', 'RSS'), ('scrape', 'Scrape'), ('manual', 'Manual submission')]
	active = models.BooleanField(default=True)
	source_id = models.AutoField(primary_key=True)
	source_for = models.CharField(choices=TABLES, max_length=50, default='science paper')
	name = models.TextField(blank=True, null=True)
	link = models.TextField(blank=True, null=True)
	subject = models.ForeignKey(Subject,on_delete=models.PROTECT,null=True,blank=True,unique=False)
	method = models.CharField(choices=METHODS, max_length=10, default='rss')
	ignore_ssl = models.BooleanField(default=False)
	description = models.TextField(blank=True, null=True)
	keyword_filter = models.TextField(
		blank=True, 
		null=True,
		help_text='Keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson). Applies to supported feed sources like bioRxiv and PNAS.'
	)
	team = models.ForeignKey(
		'Team', 
		on_delete=models.CASCADE,  # Not sure which would be the best option here
		null=True,
		blank=False,  
		related_name='sources'  # Helps in querying from the Team model, e.g., team.sources.all()
	)
	
	def get_latest_article_date(self):
		"""
		Returns the date of the most recent article from this source.
		"""
		latest_article = self.articles_set.order_by('-published_date').first()
		if latest_article:
			return latest_article.published_date
		return None
	
	def get_article_count(self):
		"""
		Returns the count of articles from this source.
		"""
		return self.articles_set.count()
	
	def get_latest_trial_date(self):
		"""
		Returns the date of the most recent trial from this source.
		"""
		latest_trial = self.trials_set.order_by('-last_updated').first()
		if latest_trial:
			return latest_trial.last_updated
		return None

	def get_trial_count(self):
		"""
		Returns the count of trials from this source.
		"""
		return self.trials_set.count()
		
	def get_health_status(self):
		"""
		Returns the health status of the source based on the latest article/trial date.
		Uses the same status logic for both article and trial sources.
		"""
		if not self.active:
			return "inactive"
			
		# Get the latest article or trial date depending on source type
		if self.source_for == 'trials':
			# For trial sources, check the Trials model
			latest_date = self.get_latest_trial_date()
		else:
			# For article sources, check the Articles model
			latest_date = self.get_latest_article_date()
			
		if not latest_date:
			return "no_content"
		
		# Same status logic for both types of sources
		now = timezone.now()
		days_since_last_update = (now - latest_date).days
		
		if days_since_last_update > 60:
			return "error"
		elif days_since_last_update > 30:
			return "warning"
		else:
			return "healthy"

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
	summary_plain_english = models.TextField(blank=True, null=True) # Used for plain English version
	
	# Persisted uppercase columns for performant case-insensitive search
	utitle = GeneratedField(
		expression=Upper('title'),
		output_field=models.TextField(),
		db_persist=True
	)
	usummary = GeneratedField(
		expression=Upper('summary'),
		output_field=models.TextField(),
		db_persist=True
	)
	
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
		constraints = [
			models.UniqueConstraint(
				fields=['title', 'link'],
				name='unique_article_title_link')
		]
		indexes = [
			# GIN indexes for fast text search on uppercase columns
			GinIndex(
				fields=['utitle'],
				name='articles_utitle_gin_idx',
				opclasses=['gin_trgm_ops']
			),
			GinIndex(
				fields=['usummary'],
				name='articles_usummary_gin_idx',
				opclasses=['gin_trgm_ops']
			),
		]
		verbose_name_plural = 'articles'
		db_table = 'articles'
		ordering = ['-discovery_date']

class Trials(models.Model):
	trial_id = models.AutoField(primary_key=True)
	discovery_date = models.DateTimeField(blank=True, null=True)
	last_updated = models.DateTimeField(auto_now=True, null=True)
	title = models.TextField(blank=False,null=False, unique=True)
	summary = models.TextField(blank=True, null=True)
	summary_plain_english = models.TextField(blank=True, null=True) # Used for plain English summary
	
	# Persisted uppercase columns for performant case-insensitive search
	utitle = GeneratedField(
		expression=Upper('title'),
		output_field=models.TextField(),
		db_persist=True
	)
	usummary = GeneratedField(
		expression=Upper('summary'),
		output_field=models.TextField(),
		db_persist=True
	)
	
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
	contact_email = models.EmailField(max_length=2000, null=True, blank=True)
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
		indexes = [
			# GIN indexes for fast text search on uppercase columns
			GinIndex(
				fields=['utitle'],
				name='trials_utitle_gin_idx',
				opclasses=['gin_trgm_ops']
			),
			GinIndex(
				fields=['usummary'],
				name='trials_usummary_gin_idx',
				opclasses=['gin_trgm_ops']
			),
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

class Team(models.Model):
	organization = models.ForeignKey(
		Organization, 
		on_delete=models.CASCADE, 
		related_name='teams'
	)
	name = models.CharField(max_length=200, help_text="Team name within the organization")
	slug = models.SlugField(unique=True, editable=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['organization', 'name'],
				name='unique_organization_team_name')
		]  # Ensure unique team names within each organization

	def __str__(self):
		if self.organization:
			# Only show organization name if it's different from team name
			if self.name.lower() != self.organization.name.lower():
				return f"{self.name} ({self.organization.name})"
		return self.name
	
	@property
	def members(self):
		# Assuming TeamMember links back to Organization via an 'organization' field
		# and each TeamMember instance has a related 'user' object
		return [member.user for member in TeamMember.objects.filter(organization=self.organization)]

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
	postmark_api_url= models.URLField(max_length=200, blank=True, null=True, help_text="Postmark API URL for this team.")
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
	ALGORITHM_CHOICES = [
		('pubmed_bert', 'PubMed BERT'),
		('lgbm_tfidf', 'LGBM TF-IDF'),
		('lstm', 'LSTM'),
		('unknown', 'Unknown')
	]
	
	created_date = models.DateTimeField(auto_now_add=True)
	subject = models.ForeignKey('Subject', on_delete=models.CASCADE, related_name='ml_subject_predictions')
	article = models.ForeignKey('Articles', on_delete=models.CASCADE, null=True, related_name='ml_predictions_detail')
	model_version = models.CharField(max_length=100, null=True, blank=True, help_text='Version identifier of the ML model used')
	algorithm = models.CharField(max_length=20, choices=ALGORITHM_CHOICES, default='unknown', help_text='ML algorithm used for prediction')
	probability_score = models.FloatField(null=True, blank=True, help_text='Probability score from the ML model prediction')
	predicted_relevant = models.BooleanField(null=True, blank=True, help_text='Whether the ML model predicted this article as relevant')
	
	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['article', 'subject', 'model_version', 'algorithm'],
				name='unique_article_subject_prediction')
		]
	
	@classmethod
	def get_latest_prediction(cls, article, subject, model_version=None):
		"""
		Get the latest prediction for a given article and subject, optionally filtered by model version.
		
		Args:
			article: Articles instance or ID
			subject: Subject instance or ID
			model_version: Optional model version string to filter by
			
		Returns:
			Latest MLPredictions instance or None if no predictions exist
		"""
		query = cls.objects.filter(article=article, subject=subject)
		
		if model_version:
			query = query.filter(model_version=model_version)
			
		return query.order_by('-created_date').first()

class ArticleSubjectRelevance(models.Model):
	article = models.ForeignKey(Articles, related_name='article_subject_relevances', on_delete=models.CASCADE)
	subject = models.ForeignKey('Subject', on_delete=models.CASCADE)
	is_relevant = models.BooleanField(null=True, blank=True, default=None, help_text="Indicates if the article is relevant for the subject. NULL means not reviewed.")

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=['article', 'subject'],
				name='unique_article_subject_relevance')
		]
		verbose_name_plural = 'article subject relevances'

	def __str__(self):
		if self.is_relevant is True:
			relevance_status = "Relevant"
		elif self.is_relevant is False:
			relevance_status = "Not Relevant"
		else:
			relevance_status = "Not Reviewed"
		return f"{self.article.title} - {self.subject.subject_name}: {relevance_status}"

class ArticleTrialReference(models.Model):
	"""
	Represents a relationship between an Article and a Trial, where the Article's summary
	contains an identifier from the Trial's identifiers field.
	"""
	article = models.ForeignKey('Articles', on_delete=models.CASCADE, related_name='trial_references')
	trial = models.ForeignKey('Trials', on_delete=models.CASCADE, related_name='article_references')
	identifier_type = models.CharField(max_length=50, help_text="Which identifier was found (e.g., 'nct_id', 'isrctn')")
	identifier_value = models.CharField(max_length=100, help_text="The actual identifier value")
	discovered_date = models.DateTimeField(auto_now_add=True)
	
	class Meta:
			constraints = [
				models.UniqueConstraint(
					fields=['article', 'trial', 'identifier_type'],
					name='unique_article_trial_identifier')
			]
			verbose_name_plural = 'article trial references'
			db_table = 'article_trial_references'
			indexes = [
				models.Index(fields=['identifier_type', 'identifier_value']),
			]
	
	def __str__(self):
			return f"Article {self.article.article_id} references Trial {self.trial.trial_id} via {self.identifier_type}"


class PredictionRunLog(models.Model):
	"""
	Logs both training and prediction runs for machine learning models.
	"""
	RUN_TYPE_CHOICES = [
		('train', 'Training'),
		('predict', 'Prediction')
	]
	
	ALGORITHM_CHOICES = [
		('pubmed_bert', 'PubMed BERT'),
		('lgbm_tfidf', 'LGBM TF-IDF'),
		('lstm', 'LSTM'),
		('unknown', 'Unknown')
	]
	
	team = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='prediction_run_logs')
	subject = models.ForeignKey('Subject', on_delete=models.CASCADE, related_name='prediction_run_logs')
	model_version = models.CharField(max_length=100, help_text="Version identifier for the model used")
	algorithm = models.CharField(max_length=20, choices=ALGORITHM_CHOICES, default='unknown', help_text="ML algorithm used for the run")
	run_type = models.CharField(max_length=10, choices=RUN_TYPE_CHOICES, help_text="Type of run: training or prediction")
	run_started = models.DateTimeField(auto_now_add=True, help_text="When the run was started")
	run_finished = models.DateTimeField(null=True, blank=True, help_text="When the run was completed")
	success = models.BooleanField(null=True, blank=True, help_text="Whether the run was successful")
	triggered_by = models.CharField(max_length=100, null=True, blank=True, help_text="User or system that triggered the run")
	error_message = models.TextField(null=True, blank=True, help_text="Error message if the run failed")

	class Meta:
		verbose_name = "Prediction Run Log"
		verbose_name_plural = "Prediction Run Logs"
		indexes = [
			models.Index(fields=['team', 'subject', 'run_finished']),
			models.Index(fields=['run_type', 'success']),
		]
		
	def __str__(self):
		status = "Successful" if self.success else "Failed" if self.success is False else "Running"
		return f"{self.get_run_type_display()} run for {self.team} - {self.subject} ({status})"
		
	@classmethod
	def get_latest_run(cls, team, subject, run_type=None):
		"""
		Get the latest completed run for a team/subject combination.
		
		Args:
			team: Team instance
			subject: Subject instance
			run_type: Optional filter by run type ('train' or 'predict')
			
		Returns:
			Latest PredictionRunLog instance or None if no completed runs
		"""
		query = cls.objects.filter(
			team=team,
			subject=subject,
			run_finished__isnull=False
		)
		
		if run_type:
			query = query.filter(run_type=run_type)
			
		return query.order_by('-run_finished').first()


