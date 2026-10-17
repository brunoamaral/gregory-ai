"""
The Sources add/change form: one "Source type" instead of source_for + method.

Every type stores the source_for/method pair its importer fetches; pairs no importer
fetches can't be chosen for a new source, and an existing one keeps its pair until
someone picks a real type. Each type's required settings are checked server-side,
and the subject must belong to the chosen team.

Run:
  docker exec gregory pytest gregory/tests/test_source_admin_form.py
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from organizations.models import Organization

from unittest.mock import patch

from gregory import source_types
from gregory.admin import SourceAdminForm
from gregory.models import Sources, Subject, Team
from gregory.utils.ncbi import ESearchResult

User = get_user_model()

# The smallest valid settings for each type.
SETTINGS = {
	"rss_papers": {"link": "https://example.org/feed"},
	"rss_trials": {"link": "https://example.org/trials.rss"},
	"ctgov": {"ctgov_search_condition": "multiple sclerosis"},
	"ctis": {"ctis_search_criteria": '{"medicalCondition": "Multiple Sclerosis"}'},
	"epo_ops": {"ops_cql_query": 'ta="multiple sclerosis" and cpc=/low A61P25/00'},
	"pubmed": {"pubmed_query": '"multiple sclerosis"[MeSH Terms] AND remyelination'},
}


class SourceAdminFormTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="org-source-form")
		self.team = Team.objects.create(organization=self.org, name="Team A", slug="team-a-source-form")
		self.other_team = Team.objects.create(
			organization=self.org, name="Team B", slug="team-b-source-form"
		)
		self.subject = Subject.objects.create(
			subject_name="Multiple Sclerosis", subject_slug="ms", team=self.team
		)
		self.other_subject = Subject.objects.create(
			subject_name="Parkinson's", subject_slug="pd", team=self.other_team
		)
		self.user = User.objects.create_superuser("admin-source-form", "a@example.org", "x")
		self.request = RequestFactory().get("/admin/gregory/sources/add/")
		self.request.user = self.user
		# The PubMed type checks its search on PubMed; tests never reach NCBI.
		patcher = patch("gregory.admin.NcbiClient")
		patcher.start().return_value.esearch.return_value = ESearchResult(
			count=1, querytranslation="translated"
		)
		self.addCleanup(patcher.stop)

	def form(self, source_type, instance=None, **data):
		payload = {
			"source_type": source_type,
			"team": self.team.pk,
			"subject": self.subject.pk,
			"name": "Test source",
			"active": "on",
			**SETTINGS.get(source_type, {}),
			**data,
		}
		return SourceAdminForm(data=payload, instance=instance, request=self.request)

	def test_every_type_saves_the_pair_its_importer_fetches(self):
		for source_type in source_types.SOURCE_TYPES:
			with self.subTest(source_type.key):
				form = self.form(source_type.key)
				self.assertTrue(form.is_valid(), form.errors)
				source = form.save()
				self.assertEqual(
					(source.source_for, source.method), (source_type.source_for, source_type.method)
				)

	def test_name_subject_and_type_are_required(self):
		form = self.form("", name="", subject="")
		self.assertFalse(form.is_valid())
		self.assertIn("name", form.errors)
		self.assertIn("subject", form.errors)
		self.assertIn("source_type", form.errors)

	def test_subject_must_belong_to_the_team(self):
		form = self.form("rss_papers", subject=self.other_subject.pk)
		self.assertFalse(form.is_valid())
		self.assertIn("belongs to", form.errors["subject"][0])

	def test_rss_needs_a_link(self):
		form = self.form("rss_trials", link="")
		self.assertFalse(form.is_valid())
		self.assertIn("link", form.errors)

	def test_ctgov_needs_at_least_one_search_field(self):
		empty = self.form("ctgov", ctgov_search_condition="  ")
		self.assertFalse(empty.is_valid())
		self.assertIn("every trial", empty.errors["ctgov_search_condition"][0])

		intervention_only = self.form(
			"ctgov", ctgov_search_condition="", ctgov_search_intervention="dihydroartemisinin"
		)
		self.assertTrue(intervention_only.is_valid(), intervention_only.errors)

	def test_ctis_criteria_must_be_an_object_with_known_keys(self):
		cases = {
			"": "Enter the search criteria",
			"{}": "Enter the search criteria",
			'["Multiple Sclerosis"]': "JSON object",
			'{"condition": "MS"}': "Unknown key(s): condition",
		}
		for criteria, message in cases.items():
			with self.subTest(criteria=criteria):
				form = self.form("ctis", ctis_search_criteria=criteria)
				self.assertFalse(form.is_valid())
				self.assertIn(message, form.errors["ctis_search_criteria"][0])

	def test_epo_query_is_required_and_must_not_carry_a_date_clause(self):
		self.assertIn("ops_cql_query", self.form("epo_ops", ops_cql_query="").errors)
		for query in ('ta=alzheimer and pd within "20240101 20241231"', "ta=alzheimer and pd>2020"):
			with self.subTest(query=query):
				form = self.form("epo_ops", ops_cql_query=query)
				self.assertFalse(form.is_valid())
				self.assertIn("publication-date", form.errors["ops_cql_query"][0])
		# "pd" inside a word or term is not a date clause.
		self.assertTrue(self.form("epo_ops", ops_cql_query='ta="pd-1 inhibitor"').is_valid())

	def test_changing_the_type_of_an_existing_source_updates_its_pair(self):
		source = self.form("rss_trials").save()
		form = self.form("ctgov", instance=source)
		self.assertTrue(form.is_valid(), form.errors)
		source = form.save()
		self.assertEqual((source.source_for, source.method), ("trials", "ctgov_api"))

	def test_an_unfetched_pair_is_kept_until_a_real_type_is_chosen(self):
		legacy = Sources.objects.create(
			name="News feed", source_for="news article", method="rss",
			team=self.team, subject=self.subject,
		)
		form = SourceAdminForm(instance=legacy, request=self.request)
		self.assertEqual(form.fields["source_type"].initial, source_types.LEGACY_KEY)

		form = self.form(source_types.LEGACY_KEY, instance=legacy)
		self.assertTrue(form.is_valid(), form.errors)
		saved = form.save()
		self.assertEqual((saved.source_for, saved.method), ("news article", "rss"))

	def test_a_new_source_cannot_choose_the_legacy_option(self):
		form = self.form(source_types.LEGACY_KEY)
		self.assertFalse(form.is_valid())
		self.assertIn("source_type", form.errors)

	def test_subject_select_carries_each_subjects_team(self):
		form = SourceAdminForm(request=self.request)
		attrs = form.fields["subject"].widget.attrs
		self.assertIn(f'"{self.subject.pk}": {self.team.pk}', attrs["data-subject-teams"])


class SourceAdminPageTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="org-source-page")
		self.team = Team.objects.create(organization=self.org, name="Team", slug="team-source-page")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-page", team=self.team)
		self.user = User.objects.create_superuser("admin-source-page", "p@example.org", "x")
		self.model_admin = admin.site._registry[Sources]

	def request(self, path="/admin/gregory/sources/add/"):
		request = RequestFactory().get(path)
		request.user = self.user
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_add_page_offers_source_type_instead_of_the_raw_fields(self):
		response = self.model_admin.add_view(self.request())
		response.render()
		html = response.content.decode()
		self.assertIn('id="id_source_type"', html)
		self.assertIn("data-subject-teams", html)
		self.assertIn("source_type_toggle.js", html)
		self.assertNotIn('id="id_source_for"', html)
		self.assertNotIn('id="id_method"', html)

	def test_list_flags_a_pair_no_importer_fetches(self):
		legacy = Sources(name="News feed", source_for="news article", method="rss")
		self.assertIn("never fetched", self.model_admin.source_type_label(legacy))
		rss = Sources(name="Feed", source_for="science paper", method="rss")
		self.assertEqual(self.model_admin.source_type_label(rss), "RSS feed of science papers")

	def test_saving_an_epo_source_without_credentials_warns(self):
		source = Sources(
			name="EPO | MS", source_for="patents", method="epo_ops", team=self.team,
			subject=self.subject, ops_cql_query='ta="multiple sclerosis"',
		)
		request = self.request()
		self.model_admin.save_model(request, source, form=None, change=False)
		messages = [str(m) for m in get_messages(request)]
		self.assertTrue(any("no EPO credentials" in m for m in messages), messages)

	def test_saving_an_api_source_tells_you_its_id(self):
		source = Sources(
			name="Scraper | MS", source_for="science paper", method="scrape",
			team=self.team, subject=self.subject,
		)
		request = self.request()
		self.model_admin.save_model(request, source, form=None, change=False)
		messages = [str(m) for m in get_messages(request)]
		self.assertTrue(any(f"source ID {source.pk}" in m for m in messages), messages)


class SourceTypesTests(TestCase):
	def test_every_pair_is_unique(self):
		pairs = [(t.source_for, t.method) for t in source_types.SOURCE_TYPES]
		self.assertEqual(len(pairs), len(set(pairs)))

	def test_fetched_types_match_the_importers_filters(self):
		"""Each importer picks up exactly one pair; the fetched types must be those."""
		fetched = {(t.source_for, t.method) for t in source_types.SOURCE_TYPES if t.fetched}
		self.assertEqual(fetched, {
			("science paper", "rss"),  # feedreader_articles
			("science paper", "pubmed_api"),  # feedreader_articles_pubmed
			("trials", "rss"),  # feedreader_trials
			("trials", "ctgov_api"),  # feedreader_trials_ctgov
			("trials", "ctis_api"),  # feedreader_trials_ctis
			("patents", "epo_ops"),  # feedreader_patents_ops
		})
