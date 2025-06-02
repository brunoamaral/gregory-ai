# Generated manually to fix articles_teams foreign key constraint

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0008_update_existing_team_names'),
    ]

    operations = [
        migrations.RunSQL(
            # Find and drop any foreign key constraint on articles_teams.team_id that references organizations_organization
            sql="""
                DO $$ 
                DECLARE 
                    constraint_name TEXT;
                BEGIN
                    -- Look for any foreign key constraint from articles_teams.team_id to organizations_organization
                    SELECT conname INTO constraint_name
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    JOIN pg_class r ON c.confrelid = r.oid
                    WHERE t.relname = 'articles_teams' 
                    AND r.relname = 'organizations_organization'
                    AND c.contype = 'f'
                    AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = t.oid AND attname = 'team_id')];
                    
                    IF constraint_name IS NOT NULL THEN
                        EXECUTE 'ALTER TABLE articles_teams DROP CONSTRAINT ' || quote_ident(constraint_name);
                        RAISE NOTICE 'Dropped constraint: %', constraint_name;
                    ELSE
                        RAISE NOTICE 'No incorrect constraint found to drop';
                    END IF;
                END $$;
            """,
            reverse_sql="-- Cannot reverse this operation automatically"
        ),
        migrations.RunSQL(
            # Ensure the correct foreign key constraint exists pointing to gregory_team
            sql="""
                DO $$
                BEGIN
                    -- Check if the correct constraint already exists
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN pg_class t ON c.conrelid = t.oid
                        JOIN pg_class r ON c.confrelid = r.oid
                        WHERE t.relname = 'articles_teams' 
                        AND r.relname = 'gregory_team'
                        AND c.contype = 'f'
                        AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = t.oid AND attname = 'team_id')]
                    ) THEN
                        ALTER TABLE articles_teams ADD CONSTRAINT articles_teams_team_id_fk_gregory_team 
                        FOREIGN KEY (team_id) REFERENCES gregory_team(id) DEFERRABLE INITIALLY DEFERRED;
                        RAISE NOTICE 'Added correct constraint: articles_teams_team_id_fk_gregory_team';
                    ELSE
                        RAISE NOTICE 'Correct constraint already exists';
                    END IF;
                END $$;
            """,
            reverse_sql="ALTER TABLE articles_teams DROP CONSTRAINT IF EXISTS articles_teams_team_id_fk_gregory_team;"
        ),
    ]
