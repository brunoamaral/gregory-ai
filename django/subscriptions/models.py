from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator, MaxValueValidator
from gregory.models import Subject, Articles, Trials, Team, TeamCategory
from simple_history.models import HistoricalRecords
class Lists(models.Model):
	list_id = models.AutoField(primary_key=True)
	list_name = models.CharField(max_length=150, null=False, blank=False)
	list_description = models.CharField(max_length=150, null=True, blank=True)
	list_email_subject = models.CharField(max_length=150,null=True,blank=True)
	subjects = models.ManyToManyField('gregory.Subject', blank=True)
	# Set purposes of the list
	admin_summary = models.BooleanField(default=False) 
	weekly_digest = models.BooleanField(default=False) 
	clinical_trials_notifications = models.BooleanField(default=False) 
	# Article limit for weekly digest emails
	article_limit = models.PositiveIntegerField(
		default=15,
		null=True,
		blank=True,
		help_text="Maximum number of articles to include in weekly digest emails (default: 15)",
		verbose_name="Article Limit for Weekly Digest Emails"
	)
	
	# ML threshold for relevance filtering
	ml_threshold = models.FloatField(
		default=0.8,
		validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
		help_text="ML prediction confidence threshold (0.0-1.0). Only articles with ML predictions above this threshold will be considered relevant.",
		verbose_name="ML Threshold for Relevance"
	)
	# Latest research categories
	latest_research_categories = models.ManyToManyField(
		'gregory.TeamCategory', 
		blank=True, 
		related_name="latest_research_lists", 
		verbose_name="Latest Research Categories",
		help_text="Select team categories to include in the 'Latest Research' section of weekly digest emails."
	)
	team = models.ForeignKey(
		Team, 
		on_delete=models.CASCADE, 
		related_name="lists",
		null=False,
		blank=False,
		help_text="The team this list belongs to."
	)

	class Meta:
		managed = True
		verbose_name_plural = 'lists'

	def __str__(self):
		return f"{self.list_name} (Team: {self.team.name})"


class Subscribers(models.Model):
	PROFILEOPTIONS = [
		('patient', 'Patient'),
		('doctor', 'Doctor'),
		('clinical centre', 'Clinical Centre'),
		('researcher', 'Researcher')
	]

	subscriber_id = models.AutoField(primary_key=True)
	first_name = models.CharField(max_length=150, null=False, blank=False)
	last_name = models.CharField(max_length=150, null=True, blank=True)
	email = models.EmailField(max_length=254, unique=True, null=False, blank=False)
	profile = models.CharField(choices=PROFILEOPTIONS, max_length=50, default='')
	active = models.BooleanField(default=True)
	is_admin = models.BooleanField(default=False)
	subscriptions = models.ManyToManyField(Lists, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	history = HistoricalRecords(m2m_fields=['subscriptions'])

	class Meta:
		managed = True
		verbose_name_plural = 'subscribers'
		db_table = 'subscribers'
		constraints = [
			UniqueConstraint(Lower('email'), name='unique_lower_email')
		]

	def __str__(self):
		return f"{self.first_name} {self.last_name} ({self.email})"
	def save(self, *args, **kwargs):
		self.email = self.email.lower()
		super(Subscribers, self).save(*args, **kwargs)


class SentArticleNotification(models.Model):
	article = models.ForeignKey(Articles, on_delete=models.CASCADE)
	list = models.ForeignKey(Lists, on_delete=models.CASCADE)
	subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
	sent_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ('article', 'list', 'subscriber')
		verbose_name_plural = 'sent article notifications'

	def __str__(self):
		return f"Article {self.article_id} sent to {self.list_id}, subscriber {self.subscriber_id}"

class SentTrialNotification(models.Model):
	trial = models.ForeignKey(Trials, on_delete=models.CASCADE)
	list = models.ForeignKey(Lists, on_delete=models.CASCADE)
	subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
	sent_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ('trial', 'list', 'subscriber')
		verbose_name_plural = 'sent trial notifications'

	def __str__(self):
		return f"Trial {self.trial_id} sent to {self.list_id}, subscriber {self.subscriber_id}"

class FailedNotification(models.Model):
    subscriber = models.ForeignKey(Subscribers, on_delete=models.CASCADE)
    list = models.ForeignKey(Lists, on_delete=models.CASCADE)
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Failed Notifications"

    def __str__(self):
        return f"Failed notification to {self.subscriber.email} for list {self.list.list_name}"