from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower

class Lists(models.Model):
	list_id = models.AutoField(primary_key=True)
	list_name = models.CharField(max_length=150, null=False, blank=False)
	list_description = models.CharField(max_length=150, null=True, blank=True)
	subjects = models.ManyToManyField('gregory.Subject', blank=True)

	class Meta:
		managed = True
		verbose_name_plural = 'lists'

	def __str__(self):
		return str(self.list_name)

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

	class Meta:
		managed = True
		verbose_name_plural = 'subscribers'
		db_table = 'subscribers'
		constraints = [
			UniqueConstraint(Lower('email'), name='unique_lower_email')
		]

	def __str__(self):
		return str(self.email)

	def save(self, *args, **kwargs):
		self.email = self.email.lower()
		super(Subscribers, self).save(*args, **kwargs)

class SentTrialNotification(models.Model):
	trial = models.ForeignKey('gregory.Trials', on_delete=models.CASCADE)
	list = models.ForeignKey('subscriptions.Lists', on_delete=models.CASCADE)
	sent_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ('trial', 'list')
		verbose_name_plural = 'sent trial notifications'

	def __str__(self):
		return f"Trial {self.trial_id} sent to {self.list_id} at {self.sent_at}"