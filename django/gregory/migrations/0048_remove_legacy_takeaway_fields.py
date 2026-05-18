from django.db import migrations


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0047_drop_authtoken_tables'),
	]

	operations = [
		migrations.RemoveField(model_name='articles', name='takeaways'),
		migrations.RemoveField(model_name='articles', name='summary_plain_english'),
		migrations.RemoveField(model_name='trials', name='summary_plain_english'),
		migrations.RemoveField(model_name='historicalarticles', name='takeaways'),
		migrations.RemoveField(model_name='historicalarticles', name='summary_plain_english'),
		migrations.RemoveField(model_name='historicaltrials', name='summary_plain_english'),
	]
