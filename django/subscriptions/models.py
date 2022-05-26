from django.db import models

# Create your models here.
class Lists(models.Model):
	list_id = models.AutoField(primary_key=True)
	list_name = models.CharField(max_length=150, null=False, blank=False)
	list_description = models.CharField(max_length=150, null=True, blank=True)
	class Meta:
		managed = True
		verbose_name_plural = 'lists'
		# db_table = 'lists'
	def __str__(self):
		return str(self.list_name)
			
class Subscribers(models.Model):
	PROFILEOPTIONS = [('patient', 'Patient'),('doctor','Doctor'),('clinical centre','Clinical Centre')]

	subscriber_id = models.AutoField(primary_key=True)
	first_name = models.CharField(max_length=150, null=False, blank=False)
	last_name = models.CharField(max_length=150,null=True,blank=True)
	email = models.EmailField(max_length=254, unique=True, null=False,blank=False)
	profile = models.CharField(choices=PROFILEOPTIONS, max_length=50, default='')
	active = models.BooleanField(default=True)
	is_admin = models.BooleanField(default=False)
	subscriptions = models.ManyToManyField(Lists, null=True, blank=True)


	def __str__(self):
		return str(self.email) 

	class Meta:
		managed = True
		verbose_name_plural = 'subscribers'
		db_table = 'subscribers'




