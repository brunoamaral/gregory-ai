"""The PubMed source type in the Sources admin: validation, warnings and the saved link."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory import source_types
from gregory.models import OrganizationCredentials, PublicationType, Sources, Subject, Team
from gregory.utils.ncbi import ESearchResult, NcbiError, NcbiQueryError

User = get_user_model()
QUERY = '"multiple sclerosis"[MeSH Terms] AND remyelination'


class PubmedSourceAdminTests(TestCase):
	def setUp(self):
		self.root = User.objects.create_superuser("pm-src-root", "r@example.com", "x")
		self.client.force_login(self.root)
		self.org = Organization.objects.create(name="PM Org", slug="pm-org")
		self.team = Team.objects.create(organization=self.org, name="PM Team", slug="pm-team")
		self.subject = Subject.objects.create(team=self.team, subject_name="MS", subject_slug="ms")
		self.url = reverse("admin:gregory_sources_add")

	def post(self, search_result=None, search_error=None, url=None, **overrides):
		data = {
			"source_type": "pubmed",
			"team": self.team.pk,
			"subject": self.subject.pk,
			"pubmed_query": QUERY,
			"name": "PubMed | MS",
			"active": "on",
		}
		data.update(overrides)
		data = {k: v for k, v in data.items() if v is not None}
		fake = patch("gregory.admin.NcbiClient")
		with fake as client_class:
			self.esearch = client_class.for_site.return_value.esearch
			if search_error is not None:
				self.esearch.side_effect = search_error
			else:
				self.esearch.return_value = search_result or ESearchResult(
					count=42, querytranslation='"multiple sclerosis"[MeSH Terms]'
				)
			response = self.client.post(url or self.url, data, follow=True)
		self.last_client = client_class
		return response

	def messages(self, response):
		return [(m.level_tag, str(m)) for m in response.context["messages"]]

	# -- the source type ----------------------------------------------------

	def test_the_type_is_offered_and_fetched(self):
		pubmed = source_types.BY_KEY["pubmed"]
		self.assertEqual((pubmed.source_for, pubmed.method, pubmed.fetched), ("science paper", "pubmed_api", True))
		self.assertEqual(pubmed.required, ("pubmed_query",))
		self.assertIs(source_types.type_for("science paper", "pubmed_api"), pubmed)

	def test_method_value_fits_the_column(self):
		self.assertLessEqual(len("pubmed_api"), Sources._meta.get_field("method").max_length)

	# -- validation ---------------------------------------------------------

	def test_empty_query_is_rejected(self):
		response = self.post(pubmed_query="")
		self.assertContains(response, "Enter the PubMed search")
		self.assertFalse(Sources.objects.exists())

	def test_date_clause_is_rejected(self):
		response = self.post(pubmed_query="ms AND 2024[dp]")
		self.assertContains(response, "Remove the date clause")
		self.assertFalse(Sources.objects.exists())
		self.esearch.assert_not_called()

	def test_pubmed_error_becomes_a_field_error_quoting_its_message(self):
		response = self.post(search_error=NcbiQueryError("fieldsnotfound: bogus"))
		self.assertContains(response, "PubMed rejected this search: fieldsnotfound: bogus")
		self.assertFalse(Sources.objects.exists())

	def test_the_check_counts_the_thirty_day_window_keyless(self):
		self.post()
		args, kwargs = self.esearch.call_args
		self.assertTrue(args[0].startswith(f"({QUERY}) AND "))
		self.assertTrue(args[0].endswith("[crdt]"))
		self.assertEqual(kwargs, {"rettype": "count"})
		self.last_client.for_site.assert_called_once_with(timeout=10, max_retries=1)

	def test_the_check_applies_the_excluded_types(self):
		review = PublicationType.objects.create(ui="D016454", name="Review")
		self.post(excluded_publication_types=[review.pk])
		term = self.esearch.call_args.args[0]
		self.assertIn('NOT ("Review"[pt])', term)

	def test_unreachable_ncbi_saves_with_a_warning(self):
		response = self.post(search_error=NcbiError("timeout"))
		source = Sources.objects.get()
		self.assertEqual(source.method, "pubmed_api")
		warnings = [text for level, text in self.messages(response) if level == "warning"]
		self.assertTrue(any("could not be reached" in text for text in warnings))

	# -- saving -------------------------------------------------------------

	def test_saving_reports_the_count_and_pubmeds_reading(self):
		response = self.post()
		infos = [text for level, text in self.messages(response) if level == "info"]
		self.assertTrue(any("42 papers" in t and "PubMed read it as" in t for t in infos))

	def test_no_volume_warning_at_the_threshold(self):
		response = self.post(search_result=ESearchResult(count=500, querytranslation="x"))
		warnings = " ".join(t for level, t in self.messages(response) if level == "warning")
		self.assertNotIn("more than 500", warnings)

	def test_volume_warning_above_the_threshold(self):
		response = self.post(search_result=ESearchResult(count=501, querytranslation="x"))
		warnings = " ".join(t for level, t in self.messages(response) if level == "warning")
		self.assertIn("more than 500 new papers in 30 days", warnings)

	def test_a_blank_link_becomes_the_pubmed_search_url(self):
		self.post()
		link = Sources.objects.get().link
		self.assertTrue(link.startswith("https://pubmed.ncbi.nlm.nih.gov/?term="))
		self.assertIn("multiple+sclerosis", link)

	def test_an_entered_link_is_kept(self):
		self.post(link="https://example.org/my-search")
		self.assertEqual(Sources.objects.get().link, "https://example.org/my-search")

	def test_excluded_publication_types_are_saved(self):
		review = PublicationType.objects.create(ui="D016454", name="Review")
		letter = PublicationType.objects.create(ui="D016422", name="Letter")
		self.post(excluded_publication_types=[review.pk, letter.pk])
		self.assertEqual(
			set(Sources.objects.get().excluded_publication_types.values_list("name", flat=True)),
			{"Review", "Letter"},
		)

	def test_warns_when_the_organization_has_no_ncbi_key(self):
		response = self.post()
		warnings = " ".join(t for level, t in self.messages(response) if level == "warning")
		self.assertIn("no NCBI API key", warnings)

	def test_no_key_warning_when_the_organization_has_one(self):
		OrganizationCredentials.objects.create(organization=self.org, ncbi_api_key="K")
		response = self.post()
		warnings = " ".join(t for level, t in self.messages(response) if level == "warning")
		self.assertNotIn("no NCBI API key", warnings)

	# -- the empty picker ---------------------------------------------------

	def test_empty_publication_type_list_says_how_to_fill_it(self):
		response = self.client.get(self.url)
		self.assertContains(response, "sync_publication_types")

	def test_the_note_is_gone_once_the_table_is_filled(self):
		PublicationType.objects.create(ui="D016454", name="Review")
		response = self.client.get(self.url)
		self.assertNotContains(response, "sync_publication_types")

	# -- review follow-ups --------------------------------------------------

	def test_unbalanced_brackets_or_quotes_are_rejected_without_calling_pubmed(self):
		for query in ("a OR (b", 'a AND "b'):
			response = self.post(pubmed_query=query)
			self.assertContains(response, "Check the brackets and quotes")
			self.esearch.assert_not_called()
		self.assertFalse(Sources.objects.exists())

	def test_a_quoted_relative_date_phrase_is_allowed(self):
		self.post(pubmed_query='"last 6 months of life"[tiab] AND palliative')
		self.assertTrue(Sources.objects.exists())

	def test_pubmed_is_not_called_when_another_field_is_invalid(self):
		response = self.post(name="")
		self.assertContains(response, "required")
		self.esearch.assert_not_called()

	def test_renaming_a_saved_source_does_not_check_the_search_again(self):
		self.post()
		source = Sources.objects.get()
		change = reverse("admin:gregory_sources_change", args=[source.pk])
		self.post(url=change, name="Renamed", link=source.link)
		self.esearch.assert_not_called()
		self.assertEqual(Sources.objects.get().name, "Renamed")

	def test_changing_the_search_checks_it_again(self):
		self.post()
		source = Sources.objects.get()
		change = reverse("admin:gregory_sources_change", args=[source.pk])
		self.post(url=change, pubmed_query=QUERY + " AND myelin", link=source.link)
		self.esearch.assert_called_once()

	def test_pubmeds_warnings_are_shown(self):
		result = ESearchResult(count=3, querytranslation="x", warnings=["phrasesignored: foo bar"])
		response = self.post(search_result=result)
		warnings = " ".join(t for level, t in self.messages(response) if level == "warning")
		self.assertIn("PubMed warned about this search: phrasesignored: foo bar", warnings)

	def test_a_search_too_long_for_the_link_column_saves_without_a_link(self):
		long_query = " OR ".join(f"term{n}" for n in range(400))
		self.post(pubmed_query=long_query)
		source = Sources.objects.get()
		self.assertFalse(source.link)
		self.assertEqual(source.pubmed_query, long_query)
