from django.db import models
from django.contrib.sites.models import Site

# Create your models here.
class CustomSettings(models.Model):
	setting_id = models.AutoField(primary_key=True)
	site = models.ForeignKey(Site, on_delete=models.PROTECT)
	title = models.TextField(blank=False, null=False, unique=True)
