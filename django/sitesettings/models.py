from django.db import models
from django.contrib.sites.models import Site

class CustomSetting(models.Model):
	setting_id = models.AutoField(primary_key=True)
	site = models.ForeignKey(Site, on_delete=models.PROTECT)
	title = models.CharField(max_length=280,blank=False, null=False, unique=True)
	email_footer = models.TextField(blank=True,null=True)
	admin_email = models.EmailField(max_length=254, unique=False, null=True,blank=True)
	sender_email_prefix = models.CharField(
		max_length=64,
		default='gregory',
		blank=True,
		help_text="Local part of the sender email address (e.g. 'gregory' → gregory@site.domain)."
	)
