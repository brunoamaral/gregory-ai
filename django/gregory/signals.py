from simple_history.signals import post_create_historical_record
from django.db.models.signals import post_save
from django.dispatch import receiver
from organizations.models import Organization

MAX_AUTHOR_HISTORY = 5

@receiver(post_create_historical_record)
def trim_author_history(sender, instance, history_instance, **kwargs):
	from gregory.models import Authors
	if not isinstance(instance, Authors):
		return
	keep_ids = list(
		instance.history.order_by('-history_date').values_list('pk', flat=True)[:MAX_AUTHOR_HISTORY]
	)
	instance.history.exclude(pk__in=keep_ids).delete()


@receiver(post_save, sender=Organization)
def create_organization_api_settings(sender, instance, created, **kwargs):
	"""Create an OrganizationApiSettings row for every newly created Organisation."""
	if created:
		from gregory.models import OrganizationApiSettings
		OrganizationApiSettings.objects.get_or_create(organization=instance)
