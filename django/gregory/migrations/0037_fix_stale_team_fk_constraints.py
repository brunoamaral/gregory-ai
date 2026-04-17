from django.db import migrations


class Migration(migrations.Migration):
	"""
	Fix stale FK constraints on team_id columns that incorrectly reference
	organizations_organization(id) instead of gregory_team(id).
	Affects: gregory_teamcredentials, team_categories, trials_teams.
	Root cause: DB was restored from a dump predating the Team model refactor.
	"""

	dependencies = [
		('gregory', '0036_organizationcredentials_orcid_client_id_and_more'),
	]

	operations = [
		migrations.RunSQL(
			sql="""
				-- Fix gregory_teamcredentials.team_id
				ALTER TABLE gregory_teamcredentials
				DROP CONSTRAINT IF EXISTS gregory_teamcredenti_team_id_b5f6094b_fk_organizat;
				ALTER TABLE gregory_teamcredentials
				ADD CONSTRAINT gregory_teamcredentials_team_id_fk_gregory_team
				FOREIGN KEY (team_id)
				REFERENCES gregory_team(id)
				DEFERRABLE INITIALLY DEFERRED;

				-- Fix team_categories.team_id
				ALTER TABLE team_categories
				DROP CONSTRAINT IF EXISTS team_categories_team_id_7233dcac_fk_organizat;
				ALTER TABLE team_categories
				ADD CONSTRAINT team_categories_team_id_fk_gregory_team
				FOREIGN KEY (team_id)
				REFERENCES gregory_team(id)
				DEFERRABLE INITIALLY DEFERRED;

				-- Fix trials_teams.team_id
				ALTER TABLE trials_teams
				DROP CONSTRAINT IF EXISTS trials_teams_team_id_c0f6c544_fk_organizations_organization_id;
				ALTER TABLE trials_teams
				ADD CONSTRAINT trials_teams_team_id_fk_gregory_team
				FOREIGN KEY (team_id)
				REFERENCES gregory_team(id)
				DEFERRABLE INITIALLY DEFERRED;
			""",
			reverse_sql="""
				ALTER TABLE gregory_teamcredentials
				DROP CONSTRAINT IF EXISTS gregory_teamcredentials_team_id_fk_gregory_team;
				ALTER TABLE gregory_teamcredentials
				ADD CONSTRAINT gregory_teamcredenti_team_id_b5f6094b_fk_organizat
				FOREIGN KEY (team_id)
				REFERENCES organizations_organization(id)
				DEFERRABLE INITIALLY DEFERRED;

				ALTER TABLE team_categories
				DROP CONSTRAINT IF EXISTS team_categories_team_id_fk_gregory_team;
				ALTER TABLE team_categories
				ADD CONSTRAINT team_categories_team_id_7233dcac_fk_organizat
				FOREIGN KEY (team_id)
				REFERENCES organizations_organization(id)
				DEFERRABLE INITIALLY DEFERRED;

				ALTER TABLE trials_teams
				DROP CONSTRAINT IF EXISTS trials_teams_team_id_fk_gregory_team;
				ALTER TABLE trials_teams
				ADD CONSTRAINT trials_teams_team_id_c0f6c544_fk_organizations_organization_id
				FOREIGN KEY (team_id)
				REFERENCES organizations_organization(id)
				DEFERRABLE INITIALLY DEFERRED;
			""",
		),
	]
