from django.db import migrations


class Migration(migrations.Migration):
	"""
	Fix the subscriptions_lists.team_id FK constraint which incorrectly points
	to organizations_organization(id) instead of gregory_team(id).
	This happened because the DB was restored from a dump that predates the
	Team model refactor.
	"""

	dependencies = [
		('subscriptions', '0011_finalize_unsubscribe_token_unique'),
		('gregory', '__first__'),
	]

	operations = [
		migrations.RunSQL(
			sql="""
				ALTER TABLE subscriptions_lists
				DROP CONSTRAINT IF EXISTS subscriptions_lists_team_id_a69bde12_fk_organizat;

				ALTER TABLE subscriptions_lists
				ADD CONSTRAINT subscriptions_lists_team_id_fk_gregory_team
				FOREIGN KEY (team_id)
				REFERENCES gregory_team(id)
				DEFERRABLE INITIALLY DEFERRED;
			""",
			reverse_sql="""
				ALTER TABLE subscriptions_lists
				DROP CONSTRAINT IF EXISTS subscriptions_lists_team_id_fk_gregory_team;

				ALTER TABLE subscriptions_lists
				ADD CONSTRAINT subscriptions_lists_team_id_a69bde12_fk_organizat
				FOREIGN KEY (team_id)
				REFERENCES organizations_organization(id)
				DEFERRABLE INITIALLY DEFERRED;
			""",
		),
	]
