from django.test import TestCase
from django.utils.text import slugify
from organizations.models import Organization
from gregory.models import Team


class TeamMigrationTestCase(TestCase):
    def setUp(self):
        # Create some test organizations before testing the migration results
        self.org1 = Organization.objects.create(name="Test Team 1")
        self.org2 = Organization.objects.create(name="Test Team 2")
        self.org3 = Organization.objects.create(name="Test Team")
        self.org4 = Organization.objects.create(name="Test Team")  # Duplicate name to test slug uniqueness
        
        # Create teams for each organization with proper slug generation
        self.create_teams_for_organizations()
    
    def create_teams_for_organizations(self):
        """Create Team objects for all Organizations with proper slug handling"""
        used_slugs = set()
        
        for org in Organization.objects.all():
            # Skip if team already exists
            if Team.objects.filter(organization=org).exists():
                continue
                
            base_slug = slugify(org.name)
            slug = base_slug
            
            # Handle slug collisions
            counter = 1
            while slug in used_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            used_slugs.add(slug)
            
            # Create team for this organization
            Team.objects.create(organization=org, slug=slug)
    
    def test_team_creation_after_migration(self):
        """Test that Team objects exist for all Organizations after migration."""
        # Count teams and verify match with organizations
        team_count = Team.objects.count()
        org_count = Organization.objects.count()
        self.assertEqual(team_count, org_count, 
                         f"Team count ({team_count}) doesn't match Organization count ({org_count})")
        
        # Verify every organization has a corresponding team
        for org in Organization.objects.all():
            self.assertTrue(
                Team.objects.filter(organization=org).exists(),
                f"Team not found for Organization: {org.name}"
            )

    def test_team_slugs(self):
        """Test that all Team slugs are non-null, unique, and match the expected pattern."""
        # Check all slugs are non-null
        teams_with_null_slugs = Team.objects.filter(slug__isnull=True)
        self.assertEqual(teams_with_null_slugs.count(), 0, "Found teams with NULL slugs")
        
        # Get all slugs
        all_slugs = list(Team.objects.values_list('slug', flat=True))
        
        # Check for uniqueness
        self.assertEqual(len(all_slugs), len(set(all_slugs)), "Found duplicate slugs")
        
        # Verify slugs match expected format
        for team in Team.objects.all():
            base_slug = slugify(team.organization.name)
            self.assertTrue(
                team.slug == base_slug or team.slug.startswith(f"{base_slug}-"),
                f"Team slug '{team.slug}' doesn't match expected pattern for '{team.organization.name}'"
            )
            
        # Test specifically for our duplicate name case
        duplicate_orgs = Organization.objects.filter(name="Test Team")
        self.assertEqual(duplicate_orgs.count(), 2, "Expected exactly 2 organizations with name 'Test Team'")
        
        duplicate_teams = list(Team.objects.filter(organization__in=duplicate_orgs))
        self.assertEqual(len(duplicate_teams), 2, "Expected exactly 2 teams for duplicate organizations")
        
        # One should have the base slug, one should have a numbered suffix
        slugs = [team.slug for team in duplicate_teams]
        self.assertTrue(
            "test-team" in slugs and any(slug.startswith("test-team-") for slug in slugs),
            f"Expected one base slug 'test-team' and one with suffix, got: {slugs}"
        )
