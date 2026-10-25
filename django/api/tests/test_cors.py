"""
CORS_ALLOWED_ORIGIN_REGEXES: origins whose host changes per deploy.

Cloudflare Pages gives every branch and every deploy of the Brain
Regeneration frontend its own hostname under brain-regeneration.pages.dev,
which an exact CORS_ALLOWED_ORIGINS list can't cover. admin/settings.py reads
whitespace-separated regexes from the CORS_ALLOWED_ORIGIN_REGEXES env var.

These tests set the production CORS settings with override_settings rather
than through the env var, because settings.py reads the env once at import
time.

Run with:
    docker exec gregory python manage.py test api.tests.test_cors
"""

from django.test import TestCase, override_settings
from organizations.models import Organization
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import Articles, Subject, Team

PAGES_REGEX = r"^https://[a-z0-9-]+\.brain-regeneration\.pages\.dev$"

# What the non-DEBUG branch of admin/settings.py builds for production, with
# the regex env var set.
PRODUCTION_CORS = dict(
	DEBUG=False,
	CORS_ALLOW_ALL_ORIGINS=False,
	CORS_ALLOWED_ORIGINS=["https://brain-regeneration.com"],
	CORS_ALLOWED_ORIGIN_REGEXES=[PAGES_REGEX],
)


@override_settings(**PRODUCTION_CORS)
class CorsOriginRegexTest(TestCase):
	def setUp(self):
		self.client = APIClient()
		org = Organization.objects.create(name="CORS Org", slug="cors-org")
		team = Team.objects.create(organization=org, name="CORS Team", slug="cors-team")
		subject = Subject.objects.create(
			subject_name="CORS Subject", subject_slug="cors-subject", team=team
		)
		# A single api_public site, so an anonymous request whose Origin
		# matches no site still resolves (to that site) and returns 200.
		publish_subjects(subject, name="CORS Site")
		article = Articles.objects.create(
			title="CORS Article", link="https://cors.example/a"
		)
		article.subjects.add(subject)
		article.teams.add(team)

	def _get(self, origin):
		return self.client.get("/articles/", HTTP_ORIGIN=origin)

	def test_preview_branch_origin_is_allowed(self):
		origin = "https://feature-x.brain-regeneration.pages.dev"
		resp = self._get(origin)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp["Access-Control-Allow-Origin"], origin)

	def test_preview_deploy_hash_origin_is_allowed(self):
		origin = "https://bcd228f0.brain-regeneration.pages.dev"
		resp = self._get(origin)
		self.assertEqual(resp["Access-Control-Allow-Origin"], origin)

	def test_preflight_from_preview_origin_is_allowed(self):
		origin = "https://feature-x.brain-regeneration.pages.dev"
		resp = self.client.options(
			"/articles/",
			HTTP_ORIGIN=origin,
			HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp["Access-Control-Allow-Origin"], origin)

	def test_suffix_after_pages_dev_is_rejected(self):
		# The regex's trailing $ is what stops this one: corsheaders matches
		# with re.match, which anchors only the start.
		resp = self._get("https://feature-x.brain-regeneration.pages.dev.evil.com")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("Access-Control-Allow-Origin", resp)

	def test_other_pages_project_is_rejected(self):
		resp = self._get("https://feature-x.other-project.pages.dev")
		self.assertNotIn("Access-Control-Allow-Origin", resp)

	def test_unrelated_origin_is_rejected(self):
		resp = self._get("https://evil.example.com")
		self.assertNotIn("Access-Control-Allow-Origin", resp)

	def test_http_scheme_is_rejected(self):
		resp = self._get("http://feature-x.brain-regeneration.pages.dev")
		self.assertNotIn("Access-Control-Allow-Origin", resp)

	def test_exact_origin_list_still_works(self):
		resp = self._get("https://brain-regeneration.com")
		self.assertEqual(
			resp["Access-Control-Allow-Origin"], "https://brain-regeneration.com"
		)

	@override_settings(CORS_ALLOWED_ORIGIN_REGEXES=[])
	def test_no_regexes_means_preview_origin_is_rejected(self):
		# The env var unset or empty: behaviour as before the setting existed.
		resp = self._get("https://feature-x.brain-regeneration.pages.dev")
		self.assertNotIn("Access-Control-Allow-Origin", resp)
