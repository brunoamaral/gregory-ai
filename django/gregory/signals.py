from simple_history.signals import (
	post_create_historical_record,
	pre_create_historical_record,
)
from django.db.models.signals import post_save
from django.dispatch import receiver
from organizations.models import Organization

MAX_AUTHOR_HISTORY = 5


@receiver(pre_create_historical_record)
def stamp_api_access_scheme_on_history(sender, history_instance, **kwargs):
	"""Populate api_access_scheme and api_access_scheme_label on any historical
	model that carries ApiKeyHistoryMixin fields.

	Reads request.api_access_scheme set by ApiKeyMiddleware via simple-history's
	HistoryRequestMiddleware context. No-ops silently for admin/shell
	saves where the request or the field is absent.
	"""
	if not hasattr(history_instance, "api_access_scheme_id"):
		return
	try:
		from simple_history.models import HistoricalRecords

		request = getattr(HistoricalRecords.context, "request", None)
		if request is None:
			return
		# api_access_scheme is a SimpleLazyObject; resolving it here is safe
		# because ApiKeyMiddleware has already run.
		scheme = getattr(request, "api_access_scheme", None)
		if scheme is None:
			return
		history_instance.api_access_scheme = scheme
		history_instance.api_access_scheme_label = (scheme.client_name or "")[:200]
	except Exception:  # noqa: S110
		# Never let a signal failure break a save.
		pass


@receiver(post_create_historical_record)
def trim_author_history(sender, instance, history_instance, **kwargs):
	from gregory.models import Authors

	if not isinstance(instance, Authors):
		return
	keep_ids = list(
		instance.history.order_by("-history_date").values_list("pk", flat=True)[
			:MAX_AUTHOR_HISTORY
		]
	)
	instance.history.exclude(pk__in=keep_ids).delete()


@receiver(post_save, sender=Organization)
def create_organization_api_settings(sender, instance, created, **kwargs):
	"""Create an OrganizationApiSettings row for every newly created Organisation."""
	if created:
		from gregory.models import OrganizationApiSettings

		OrganizationApiSettings.objects.get_or_create(organization=instance)
