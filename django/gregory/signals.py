from simple_history.signals import post_create_historical_record
from django.dispatch import receiver

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
