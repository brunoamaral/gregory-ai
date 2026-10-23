"""
/patents/ (api/views.py PatentViewSet): who sees which patent families, the
patent-trial links in both directions, and the filters.

Patents are visible only through sites with ``CustomSetting.patents_enabled``
on, even when the subject sits in a site's scope. Fixture:

  - site A: public, patents ON,  scope {S_on}
  - site B: public, patents OFF, scope {S_off}
  - site P: private, patents ON, scope {S_priv}
  - all three belong to one organisation

  patents: p_on {S_on}, p_off {S_off}, p_priv {S_priv}, p_both {S_on, S_off}
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.text import slugify
from django.utils.timezone import now
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	PatentApplicant,
	PatentCategoryAssignment,
	PatentPublication,
	PatentTrialLink,
	Patents,
	Sponsor,
	Subject,
	Team,
	TeamCategory,
	Trials,
)
from mcpauth.tests.helpers import SERVICE_KEY, grant, make_org, make_user
from sitesettings.models import CustomSetting


def _subject(org, name):
	team = Team.objects.create(
		organization=org, name=f"{name} team", slug=slugify(f"{name} team")
	)
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _patent(title, *subjects, **fields):
	patent = Patents.objects.create(title=title, **fields)
	patent.subjects.add(*subjects)
	return patent


def _trial(title, *subjects):
	trial = Trials.objects.create(title=title, link=f"https://t.test/{slugify(title)}")
	trial.subjects.add(*subjects)
	return trial


def _link(patent, trial, sponsor, basis="category", suppressed=False):
	return PatentTrialLink.objects.create(
		patent=patent,
		trial=trial,
		sponsor=sponsor,
		timing=PatentTrialLink.TIMING_DURING,
		basis=basis,
		suppressed=suppressed,
	)


def _titles(response):
	return sorted(r["title"] for r in response.data["results"])


class PatentFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Patent Org")
		cls.s_on = _subject(cls.org, "Subject On")
		cls.s_off = _subject(cls.org, "Subject Off")
		cls.s_priv = _subject(cls.org, "Subject Priv")
		cls.site_a = publish_subjects(
			cls.s_on, organization=cls.org, domain="pat-a.example.com", patents_enabled=True
		)
		cls.site_b = publish_subjects(
			cls.s_off, organization=cls.org, domain="pat-b.example.com", patents_enabled=False
		)
		cls.site_p = private_site_publishing(
			cls.s_priv, organization=cls.org, domain="pat-p.example.com", patents_enabled=True
		)

		cls.p_on = _patent("Patent on", cls.s_on)
		cls.p_off = _patent("Patent off", cls.s_off)
		cls.p_priv = _patent("Patent priv", cls.s_priv)
		cls.p_both = _patent("Patent both", cls.s_on, cls.s_off)

		cls.sponsor = Sponsor.objects.create(name="Acme Pharma", slug="acme-pharma")

	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def key_for(self, site):
		return APIAccessScheme.objects.create(
			client_name=f"key-{site.pk}",
			client_contacts="k@example.com",
			organization=self.org,
			site=site,
			ip_addresses="",
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)


class PatentVisibilityTest(PatentFixture):
	def test_anonymous_on_a_site_with_patents_sees_its_patents(self):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(_titles(response), ["Patent both", "Patent on"])

	def test_anonymous_on_a_site_without_patents_sees_none(self):
		response = self.client.get(f"/patents/?site_id={self.site_b.pk}")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["results"], [])
		self.assertEqual(response.data["count"], 0)

	def test_detail_through_a_site_without_patents_is_404(self):
		response = self.client.get(f"/patents/{self.p_off.pk}/?site_id={self.site_b.pk}")
		self.assertEqual(response.status_code, 404)

	def test_a_patent_with_one_visible_subject_is_visible_but_not_its_other_subject(self):
		response = self.client.get(f"/patents/{self.p_both.pk}/?site_id={self.site_a.pk}")
		self.assertEqual(response.status_code, 200)
		self.assertEqual([s["id"] for s in response.data["subjects"]], [self.s_on.pk])

	def test_site_bound_key_on_a_private_site_with_patents(self):
		key = self.key_for(self.site_p)
		response = self.client.get("/patents/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(_titles(response), ["Patent priv"])

	def test_site_bound_key_on_a_site_without_patents_sees_none(self):
		key = self.key_for(self.site_b)
		response = self.client.get("/patents/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(response.data["results"], [])

	def test_include_public_adds_only_public_sites_with_patents(self):
		key = self.key_for(self.site_p)
		response = self.client.get(
			"/patents/?include_public=true", HTTP_AUTHORIZATION=key.api_key
		)
		self.assertEqual(_titles(response), ["Patent both", "Patent on", "Patent priv"])

	def test_include_public_does_not_apply_to_an_anonymous_caller(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_b.pk}&include_public=true"
		)
		self.assertEqual(response.data["results"], [])

	def test_authenticated_member_sees_the_union_of_their_sites_with_patents(self):
		user = make_user("pat-member", self.org)
		self.client.force_login(user)
		response = self.client.get("/patents/")
		self.assertEqual(_titles(response), ["Patent both", "Patent on", "Patent priv"])

	def test_authenticated_user_outside_the_organisation_sees_none(self):
		self.client.force_login(make_user("pat-outsider"))
		response = self.client.get("/patents/")
		self.assertEqual(response.data["results"], [])

	def test_ambiguous_anonymous_site_fails_closed_like_other_endpoints(self):
		# Two public sites and no indicator: the same 400 /trials/ gives.
		self.assertEqual(self.client.get("/trials/").status_code, 400)
		self.assertEqual(self.client.get("/patents/").status_code, 400)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class EditorPatentReadTest(PatentFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		CustomSetting.objects.filter(site__in=[cls.site_a, cls.site_b, cls.site_p]).update(
			mcp_enabled=True
		)

	def headers(self, user, site):
		return {
			"HTTP_X_GREGORY_EDITOR_USER": str(user.pk),
			"HTTP_X_GREGORY_EDITOR_SITE": str(site.pk),
			"HTTP_AUTHORIZATION": f"Bearer {SERVICE_KEY}",
		}

	def test_editor_of_a_site_with_patents_reads_them(self):
		ana = make_user("pat-ed-ana", self.org)
		grant(ana, self.site_p)
		response = self.client.get("/editor/patents/", **self.headers(ana, self.site_p))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(_titles(response), ["Patent priv"])

	def test_editor_of_a_site_without_patents_sees_none(self):
		ben = make_user("pat-ed-ben", self.org)
		grant(ben, self.site_b)
		response = self.client.get("/editor/patents/", **self.headers(ben, self.site_b))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["results"], [])
		detail = self.client.get(
			f"/editor/patents/{self.p_off.pk}/", **self.headers(ben, self.site_b)
		)
		self.assertEqual(detail.status_code, 404)

	def test_editor_route_refuses_ordinary_callers(self):
		self.assertEqual(self.client.get("/editor/patents/").status_code, 401)


class PatentTrialLinkVisibilityTest(PatentFixture):
	"""Links in both directions: suppressed never, subject-basis on request,
	and a patent or trial the caller cannot see discloses nothing."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.t_on = _trial("Trial on", cls.s_on)
		cls.t_on_2 = _trial("Trial on two", cls.s_on)
		cls.t_off = _trial("Trial off", cls.s_off)
		# Visible through site B (S_off) and through site A (S_on).
		cls.t_both = _trial("Trial both", cls.s_on, cls.s_off)

		cls.l_category = _link(cls.p_on, cls.t_on, cls.sponsor)
		cls.l_subject = _link(cls.p_on, cls.t_on_2, cls.sponsor, basis="subject")
		cls.l_suppressed = _link(cls.p_both, cls.t_on, cls.sponsor, suppressed=True)
		cls.l_hidden_trial = _link(cls.p_on, cls.t_off, cls.sponsor)
		cls.l_both = _link(cls.p_on, cls.t_both, cls.sponsor)
		cls.l_off_patent = _link(cls.p_off, cls.t_both, cls.sponsor)

	def a(self, path, **params):
		sep = "&" if "?" in path else "?"
		return self.client.get(f"{path}{sep}site_id={self.site_a.pk}", **params)

	def b(self, path):
		sep = "&" if "?" in path else "?"
		return self.client.get(f"{path}{sep}site_id={self.site_b.pk}")

	# --- patent -> trials ---------------------------------------------------

	def test_patent_detail_shows_category_links_to_visible_trials(self):
		response = self.a(f"/patents/{self.p_on.pk}/")
		links = {link["trial_id"] for link in response.data["trial_links"]}
		self.assertEqual(links, {self.t_on.pk, self.t_both.pk})
		self.assertEqual(response.data["trial_links_count"], 2)

	def test_patent_link_fields(self):
		response = self.a(f"/patents/{self.p_on.pk}/")
		link = next(l for l in response.data["trial_links"] if l["trial_id"] == self.t_on.pk)
		self.assertEqual(
			set(link),
			{"trial_id", "trial_title", "sponsor_id", "timing", "days_after_completion", "basis"},
		)
		self.assertEqual(link["trial_title"], "Trial on")
		self.assertEqual(link["sponsor_id"], self.sponsor.pk)
		self.assertEqual(link["basis"], "category")

	def test_subject_links_only_on_request(self):
		response = self.a(f"/patents/{self.p_on.pk}/?include_subject_links=true")
		links = {link["trial_id"] for link in response.data["trial_links"]}
		self.assertEqual(links, {self.t_on.pk, self.t_on_2.pk, self.t_both.pk})
		self.assertEqual(response.data["trial_links_count"], 3)

	def test_suppressed_links_never_show(self):
		response = self.a(
			f"/patents/{self.p_both.pk}/?include_subject_links=true"
		)
		self.assertEqual(response.data["trial_links"], [])
		self.assertEqual(response.data["trial_links_count"], 0)

	def test_link_to_a_trial_the_caller_cannot_see_is_not_shown(self):
		# t_off is only in site B's scope; site A must not learn it exists.
		response = self.a(f"/patents/{self.p_on.pk}/")
		self.assertNotIn(self.t_off.pk, {l["trial_id"] for l in response.data["trial_links"]})
		self.assertNotIn("Trial off", str(response.data))

	def test_a_member_who_can_see_both_sites_sees_the_link(self):
		self.client.force_login(make_user("pat-link-member", self.org))
		response = self.client.get(f"/patents/{self.p_on.pk}/")
		self.assertIn(self.t_off.pk, {l["trial_id"] for l in response.data["trial_links"]})

	def test_list_trial_links_count_matches_detail(self):
		response = self.a("/patents/")
		row = next(r for r in response.data["results"] if r["title"] == "Patent on")
		self.assertEqual(row["trial_links_count"], 2)

	def test_has_trial_link_filter(self):
		linked = self.a("/patents/?has_trial_link=true")
		self.assertEqual(_titles(linked), ["Patent on"])
		unlinked = self.a("/patents/?has_trial_link=false")
		self.assertEqual(_titles(unlinked), ["Patent both"])

	def test_has_trial_link_with_subject_links(self):
		# p_on has category links already; nothing new here, but p_both is
		# only suppressed, so it stays unlinked either way.
		linked = self.a("/patents/?has_trial_link=true&include_subject_links=true")
		self.assertEqual(_titles(linked), ["Patent on"])

	# --- trial -> patents ---------------------------------------------------

	def test_trial_detail_shows_patent_links(self):
		response = self.a(f"/trials/{self.t_on.pk}/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			[l["patent_id"] for l in response.data["patent_links"]], [self.p_on.pk]
		)
		link = response.data["patent_links"][0]
		self.assertEqual(
			set(link),
			{
				"patent_id",
				"title",
				"representative_publication",
				"timing",
				"days_after_completion",
				"basis",
			},
		)
		self.assertEqual(link["title"], "Patent on")

	def test_trial_detail_omits_suppressed_and_subject_links(self):
		# t_on has a category link (p_on) and a suppressed one (p_both).
		response = self.a(f"/trials/{self.t_on.pk}/?include_subject_links=true")
		self.assertEqual(
			[l["patent_id"] for l in response.data["patent_links"]], [self.p_on.pk]
		)
		response = self.a(f"/trials/{self.t_on_2.pk}/")
		self.assertEqual(response.data["patent_links"], [])
		response = self.a(f"/trials/{self.t_on_2.pk}/?include_subject_links=true")
		self.assertEqual(
			[l["patent_id"] for l in response.data["patent_links"]], [self.p_on.pk]
		)

	def test_trial_on_a_site_without_the_switch_shows_no_patent_links(self):
		# t_both is visible through site B, where patents are off. It is linked
		# to p_on and p_off, and neither may show.
		response = self.b(f"/trials/{self.t_both.pk}/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["patent_links"], [])
		self.assertNotIn("Patent on", str(response.data))
		self.assertNotIn("Patent off", str(response.data))

	def test_same_trial_through_a_site_with_the_switch_shows_its_visible_patent(self):
		response = self.a(f"/trials/{self.t_both.pk}/")
		self.assertEqual(
			[l["patent_id"] for l in response.data["patent_links"]], [self.p_on.pk]
		)

	def test_has_patent_link_filter(self):
		linked = self.a("/trials/?has_patent_link=true")
		self.assertEqual(_titles(linked), ["Trial both", "Trial on"])
		unlinked = self.a("/trials/?has_patent_link=false")
		self.assertEqual(_titles(unlinked), ["Trial on two"])

	def test_has_patent_link_with_subject_links(self):
		linked = self.a("/trials/?has_patent_link=true&include_subject_links=true")
		self.assertEqual(_titles(linked), ["Trial both", "Trial on", "Trial on two"])

	def test_has_patent_link_matches_nothing_on_a_site_without_the_switch(self):
		linked = self.b("/trials/?has_patent_link=true")
		self.assertEqual(linked.data["results"], [])
		unlinked = self.b("/trials/?has_patent_link=false")
		self.assertEqual(_titles(unlinked), ["Trial both", "Trial off"])

	def test_has_patent_link_count_is_not_shared_across_patent_scopes(self):
		# Same subjects, same query string, different patent scope: the cached
		# paginator count of one must not answer the other.
		both = publish_subjects(
			self.s_on,
			organization=self.org,
			domain="pat-a2.example.com",
			patents_enabled=False,
		)
		with_patents = self.a("/trials/?has_patent_link=true")
		without = self.client.get(f"/trials/?has_patent_link=true&site_id={both.pk}")
		self.assertEqual(with_patents.data["count"], 2)
		self.assertEqual(without.data["count"], 0)


class PatentStatsAndScopeCacheTest(PatentFixture):
	def test_stats_are_scoped_and_not_shared_between_patent_scopes(self):
		both = publish_subjects(
			self.s_on,
			organization=self.org,
			domain="pat-a3.example.com",
			patents_enabled=False,
		)
		on = self.client.get(f"/patents/stats/?site_id={self.site_a.pk}")
		off = self.client.get(f"/patents/stats/?site_id={both.pk}")
		self.assertEqual(on.data["total"], 2)
		self.assertEqual(off.data["total"], 0)

	def test_list_count_is_not_shared_between_patent_scopes(self):
		both = publish_subjects(
			self.s_on,
			organization=self.org,
			domain="pat-a4.example.com",
			patents_enabled=False,
		)
		on = self.client.get(f"/patents/?site_id={self.site_a.pk}")
		off = self.client.get(f"/patents/?site_id={both.pk}")
		self.assertEqual(on.data["count"], 2)
		self.assertEqual(off.data["count"], 0)

	def test_stats_payload(self):
		p = _patent(
			"Granted",
			self.s_on,
			has_grant=True,
			earliest_priority_date=date(2015, 5, 1),
		)
		_patent("Undated", self.s_on)
		trial = _trial("Stats trial", self.s_on)
		_link(p, trial, self.sponsor)
		_link(self.p_on, trial, self.sponsor, basis="subject")
		self.p_on.earliest_priority_date = date(2020, 1, 1)
		self.p_on.save()
		response = self.client.get(f"/patents/stats/?site_id={self.site_a.pk}")
		self.assertEqual(response.status_code, 200)
		data = response.data
		self.assertEqual(data["total"], 4)
		self.assertEqual(data["with_grant"], 1)
		self.assertEqual(data["with_trial_link"], 1)
		self.assertEqual(
			data["by_priority_year"],
			[
				{"year": 2015, "count": 1},
				{"year": 2020, "count": 1},
				{"year": None, "count": 2},
			],
		)

	def test_stats_follow_the_filters(self):
		_patent("Granted", self.s_on, has_grant=True)
		response = self.client.get(
			f"/patents/stats/?site_id={self.site_a.pk}&has_grant=true"
		)
		self.assertEqual(response.data["total"], 1)


class PatentFormatTest(PatentFixture):
	def test_csv_is_not_offered(self):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&format=csv")
		self.assertEqual(response.status_code, 404)
		self.assertNotIn("csv", response.get("Content-Type", ""))

	def test_csv_accept_header_is_not_offered(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}", HTTP_ACCEPT="text/csv"
		)
		self.assertNotIn("csv", response.get("Content-Type", ""))

	def test_all_results_is_ignored(self):
		for n in range(5):
			_patent(f"Bulk {n}", self.s_on)
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&all_results=true&page_size=2"
		)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 2)
		self.assertEqual(response.data["count"], 7)
		self.assertIsNotNone(response.data["next"])

	def test_page_size_is_capped(self):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&page_size=5000")
		self.assertEqual(response.status_code, 200)

	def test_write_methods_are_refused(self):
		self.client.force_login(make_user("pat-writer", self.org))
		url = f"/patents/{self.p_on.pk}/"
		for method in ("put", "patch", "delete"):
			with self.subTest(method=method):
				self.assertEqual(getattr(self.client, method)(url).status_code, 405)
		self.assertEqual(self.client.post("/patents/").status_code, 405)


class PatentIncludeClaimsTest(PatentFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.p_on.claims = "1. A method of treating multiple sclerosis."
		cls.p_on.save()

	def url(self, extra=""):
		return f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}{extra}"

	def test_claims_key_is_absent_by_default(self):
		response = self.client.get(self.url())
		self.assertNotIn("claims", response.data)

	def test_include_claims_adds_the_key(self):
		response = self.client.get(self.url("&include=claims"))
		self.assertEqual(response.data["claims"], "1. A method of treating multiple sclerosis.")

	def test_include_claims_with_no_claims_is_null(self):
		response = self.client.get(
			f"/patents/{self.p_both.pk}/?site_id={self.site_a.pk}&include=claims"
		)
		self.assertIn("claims", response.data)
		self.assertIsNone(response.data["claims"])

	def test_list_never_carries_claims(self):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&include=claims")
		self.assertEqual(response.status_code, 200)
		for row in response.data["results"]:
			self.assertNotIn("claims", row)

	def test_unknown_include_is_a_400(self):
		self.assertEqual(self.client.get(self.url("&include=bogus")).status_code, 400)


class PatentFilterTest(PatentFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat = TeamCategory.objects.create(
			team=cls.s_on.team,
			category_name="Ocrelizumab",
			category_slug="ocrelizumab",
			category_terms=["ocrelizumab"],
			modality="biologic_antibody",
		)
		cls.cat.subjects.add(cls.s_on)
		cls.other_cat = TeamCategory.objects.create(
			team=cls.s_on.team,
			category_name="Other",
			category_slug="other",
			category_terms=["other"],
			modality="small_molecule",
		)
		cls.other_cat.subjects.add(cls.s_on)

		cls.alpha = _patent(
			"Anti-CD20 antibody formulation",
			cls.s_on,
			summary="A stable formulation of an anti-CD20 antibody",
			family_id="1001",
			earliest_priority_date=date(2018, 3, 1),
			earliest_publication_date=date(2019, 9, 1),
			has_grant=True,
			cpc_classes=["A61K39/395", "A61P25/28"],
		)
		cls.beta = _patent(
			"Remyelination small molecule",
			cls.s_on,
			summary="Compounds that promote remyelination",
			family_id="1002",
			earliest_priority_date=date(2021, 6, 1),
			earliest_publication_date=date(2022, 12, 1),
			has_grant=False,
			cpc_classes=["A61K31/435"],
		)
		cls.gamma = _patent(
			"Diagnostic imaging marker",
			cls.s_on,
			family_id="1003",
			earliest_priority_date=date(2015, 1, 1),
			cpc_classes=[],
		)
		PatentCategoryAssignment.objects.create(
			patents=cls.alpha, teamcategory=cls.cat, source="automatic"
		)
		PatentCategoryAssignment.objects.create(
			patents=cls.beta, teamcategory=cls.other_cat, source="manual"
		)

		for patent, numbers in (
			(cls.alpha, [("EP", "4123456", "A1"), ("EP", "4123456", "B1"), ("WO", "2018123456", "A1")]),
			(cls.beta, [("EP", "412345", "A1"), ("US", "2022123456", "A1")]),
			(cls.gamma, [("WO", "0185797", "A1")]),
		):
			for country, doc, kind in numbers:
				PatentPublication.objects.create(
					patent=patent,
					publication_number=f"{country}{doc}{kind}",
					country=country,
					doc_number=doc,
					kind=kind,
				)
		# Letters inside the number itself, as on 4.6% of stored numbers.
		PatentPublication.objects.create(
			patent=cls.gamma,
			publication_number="MXPA02011163A",
			country="MX",
			doc_number="PA02011163",
			kind="A",
		)

		cls.acme = cls.sponsor
		cls.globex = Sponsor.objects.create(name="Globex", slug="globex")
		PatentApplicant.objects.create(
			patent=cls.alpha, sponsor=cls.acme, raw_name="ACME PHARMA LTD", sequence=0
		)
		PatentApplicant.objects.create(
			patent=cls.alpha, sponsor=None, raw_name="Jane Q. Researcher", sequence=1, is_individual=True
		)
		PatentApplicant.objects.create(
			patent=cls.beta, sponsor=cls.globex, raw_name="Globex Inc.", sequence=0
		)

	def titles(self, query):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return [r["title"] for r in response.data["results"]]

	def assertMatches(self, query, *patents):
		self.assertEqual(sorted(self.titles(query)), sorted(p.title for p in patents), query)

	def test_search_title_and_summary(self):
		self.assertMatches("search=remyelination", self.beta)
		self.assertMatches("search=formulation", self.alpha)
		self.assertMatches("search=antibody OR remyelination", self.alpha, self.beta)
		self.assertMatches('search="anti-CD20 antibody"', self.alpha)
		self.assertMatches("search=antibody NOT stable", )

	def test_search_does_not_read_claims(self):
		self.gamma.claims = "claims mention wolverine"
		self.gamma.save()
		self.assertMatches("search=wolverine")

	def test_title_and_summary(self):
		self.assertMatches("title=imaging", self.gamma)
		self.assertMatches("summary=STABLE", self.alpha)

	def test_subject_id(self):
		self.assertMatches(f"subject_id={self.s_on.pk}", self.alpha, self.beta, self.gamma, self.p_on, self.p_both)

	def test_site_id_with_and_without_the_switch(self):
		self.assertMatches(f"site_id={self.site_a.pk}", self.alpha, self.beta, self.gamma, self.p_on, self.p_both)
		# Naming a site without the switch returns nothing, not another site's.
		self.assertMatches(f"site_id={self.site_b.pk}")

	def test_category_id_slug_and_modality(self):
		self.assertMatches(f"category_id={self.cat.pk}", self.alpha)
		self.assertMatches("category_slug=ocrelizumab", self.alpha)
		self.assertMatches("category_slug=nope")
		self.assertMatches("category_modality=biologic_antibody", self.alpha)
		self.assertMatches("category_modality=small_molecule", self.beta)

	def test_applicant_filters(self):
		self.assertMatches(f"applicant_sponsor_id={self.acme.pk}", self.alpha)
		self.assertMatches("applicant_sponsor_slug=globex", self.beta)
		self.assertMatches("applicant=researcher", self.alpha)
		self.assertMatches("applicant=acme pharma", self.alpha)
		self.assertMatches("applicant=nobody")

	def test_date_ranges(self):
		self.assertMatches("priority_date_after=2018-03-01&priority_date_before=2021-06-01", self.alpha, self.beta)
		self.assertMatches("priority_date_before=2016-01-01", self.gamma)
		self.assertMatches("publication_date_after=2020-01-01", self.beta)
		self.assertMatches("publication_date_before=2019-12-31", self.alpha)

	def test_invalid_date_is_a_400(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&priority_date_after=not-a-date"
		)
		self.assertEqual(response.status_code, 400)

	def test_has_grant(self):
		self.assertMatches("has_grant=true", self.alpha)
		self.assertMatches("has_grant=false", self.beta, self.gamma, self.p_on, self.p_both)

	def test_cpc_is_a_prefix_match_on_any_class(self):
		self.assertMatches("cpc=A61K31", self.beta)
		self.assertMatches("cpc=A61K", self.alpha, self.beta)
		self.assertMatches("cpc=A61P25/28", self.alpha)
		self.assertMatches("cpc=A61P25", self.alpha)
		self.assertMatches("cpc=A61K31/435", self.beta)
		self.assertMatches("cpc=A61K3/", )

	def test_cpc_list_ignores_spaces_and_case(self):
		self.assertMatches("cpc=a61k 39,A61K31", self.alpha, self.beta)

	def test_cpc_wildcards_are_literal(self):
		self.assertMatches("cpc=A61%25")
		self.assertMatches("cpc=A61_31")

	def test_cpc_value_is_never_sql(self):
		self.assertMatches("cpc=A61K'; DROP TABLE patents; --")
		self.assertTrue(Patents.objects.exists())

	def test_publication_number_formats(self):
		for raw in ("EP4123456A1", "EP 4123456 A1", "ep4123456a1", "EP-4123456-A1", "EP4123456.A1"):
			with self.subTest(raw=raw):
				self.assertMatches(f"publication_number={raw}", self.alpha)

	def test_publication_number_matches_any_member(self):
		self.assertMatches("publication_number=WO2018123456A1", self.alpha)
		self.assertMatches("publication_number=EP4123456B1", self.alpha)

	def test_publication_number_without_kind_matches_every_kind(self):
		self.assertMatches("publication_number=EP4123456", self.alpha)

	def test_publication_number_with_kind_matches_only_that_kind(self):
		self.assertMatches("publication_number=EP4123456A2")

	def test_publication_number_is_not_a_prefix_match(self):
		# EP412345 is beta's; EP4123456 is alpha's. Neither is a prefix hit.
		self.assertMatches("publication_number=EP412345", self.beta)
		self.assertMatches("publication_number=EP4123", )
		self.assertMatches("publication_number=EP41234", )

	def test_publication_number_list_is_or(self):
		self.assertMatches("publication_number=EP4123456A1,US2022123456A1", self.alpha, self.beta)

	def test_publication_number_with_letters_inside_the_number(self):
		self.assertMatches("publication_number=MXPA02011163A", self.gamma)
		self.assertMatches("publication_number=MX PA02011163 A", self.gamma)

	def test_publication_number_with_leading_zeros_as_stored(self):
		self.assertMatches("publication_number=WO0185797A1", self.gamma)

	def test_publication_number_garbage_matches_nothing(self):
		self.assertMatches("publication_number=%21%21%21")
		self.assertMatches("publication_number=123")

	def test_family_id(self):
		self.assertMatches("family_id=1002", self.beta)

	def test_a_family_never_appears_twice(self):
		# alpha has three member publications and two applicants.
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&publication_number=EP4123456,WO2018123456"
		)
		self.assertEqual(response.data["count"], 1)
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&applicant=a")
		self.assertEqual(len({r["patent_id"] for r in response.data["results"]}), len(response.data["results"]))

	def test_ordering(self):
		def order(value):
			response = self.client.get(
				f"/patents/?site_id={self.site_a.pk}&ordering={value}&search=A"
			)
			return [r["family_id"] for r in response.data["results"] if r["family_id"]]

		self.assertEqual(order("earliest_priority_date"), ["1003", "1001", "1002"])
		self.assertEqual(order("-earliest_priority_date"), ["1002", "1001", "1003"])

	def test_ordering_puts_missing_dates_last_in_both_directions(self):
		def ids(value):
			response = self.client.get(
				f"/patents/?site_id={self.site_a.pk}&ordering={value}"
			)
			return [r["family_id"] for r in response.data["results"]]

		asc, desc = ids("earliest_publication_date"), ids("-earliest_publication_date")
		# gamma (no publication date) and the dateless fixture rows come last.
		self.assertEqual(asc[:2], ["1001", "1002"])
		self.assertEqual(desc[:2], ["1002", "1001"])


class PatentSerializerTest(PatentFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat = TeamCategory.objects.create(
			team=cls.s_on.team,
			category_name="Cat",
			category_slug="cat",
			category_terms=["cat"],
		)
		cls.cat.subjects.add(cls.s_on)
		cls.patent = _patent(
			"Serialised",
			cls.s_on,
			summary="Summary",
			family_id="2001",
			representative_publication="EP1A1",
			link="https://worldwide.espacenet.com/patent/search?q=pn%3DEP1A1",
			links={"espacenet": "https://worldwide.espacenet.com/patent/search?q=pn%3DEP1A1"},
			earliest_priority_date=date(2019, 1, 2),
			cpc_classes=["A61K31/00"],
			ipc_classes=["A61K31/00"],
			inventors=["DOE, Jane"],
		)
		PatentCategoryAssignment.objects.create(
			patents=cls.patent, teamcategory=cls.cat, source="automatic"
		)
		PatentPublication.objects.create(
			patent=cls.patent,
			publication_number="EP1A1",
			country="EP",
			doc_number="1",
			kind="A1",
			publication_date=date(2020, 1, 1),
			application_number="EP2019000001",
			application_date=date(2019, 1, 2),
		)
		PatentPublication.objects.create(
			patent=cls.patent, publication_number="WO2A1", country="WO", doc_number="2", kind="A1"
		)
		PatentApplicant.objects.create(
			patent=cls.patent, sponsor=cls.sponsor, raw_name="ACME", sequence=0
		)
		PatentApplicant.objects.create(
			patent=cls.patent, sponsor=None, raw_name="Jane Doe", sequence=1, is_individual=True
		)

	def row(self):
		response = self.client.get(f"/patents/?site_id={self.site_a.pk}&search=Serialised")
		return response.data["results"][0]

	def test_list_fields(self):
		row = self.row()
		self.assertEqual(
			set(row),
			{
				"patent_id",
				"family_id",
				"title",
				"summary",
				"representative_publication",
				"link",
				"earliest_priority_date",
				"earliest_publication_date",
				"has_grant",
				"cpc_classes",
				"applicants",
				"categories",
				"subjects",
				"publications_count",
				"trial_links_count",
			},
		)
		self.assertEqual(row["publications_count"], 2)
		self.assertEqual(row["link"], self.patent.link)

	def test_applicants_in_sequence_with_sponsor(self):
		row = self.row()
		self.assertEqual(
			row["applicants"],
			[
				{
					"raw_name": "ACME",
					"sponsor_id": self.sponsor.pk,
					"sponsor_name": "Acme Pharma",
					"sponsor_slug": "acme-pharma",
					"is_individual": False,
				},
				{
					"raw_name": "Jane Doe",
					"sponsor_id": None,
					"sponsor_name": None,
					"sponsor_slug": None,
					"is_individual": True,
				},
			],
		)

	def test_categories_and_subjects(self):
		row = self.row()
		self.assertEqual([c["category_slug"] for c in row["categories"]], ["cat"])
		self.assertEqual([s["id"] for s in row["subjects"]], [self.s_on.pk])

	def test_detail_adds_publications_inventors_and_classes(self):
		response = self.client.get(f"/patents/{self.patent.pk}/?site_id={self.site_a.pk}")
		data = response.data
		self.assertEqual(
			{"publications", "inventors", "ipc_classes", "links", "trial_links"} - set(data), set()
		)
		self.assertEqual([p["publication_number"] for p in data["publications"]], ["EP1A1", "WO2A1"])
		self.assertEqual(
			set(data["publications"][0]),
			{
				"publication_number",
				"country",
				"kind",
				"publication_date",
				"application_number",
				"application_date",
			},
		)
		self.assertEqual(data["inventors"], ["DOE, Jane"])
		self.assertEqual(data["ipc_classes"], ["A61K31/00"])
		self.assertEqual(data["links"], {"espacenet": self.patent.link})
		self.assertNotIn("claims", data)

	def test_the_list_does_not_carry_detail_fields(self):
		row = self.row()
		for key in ("publications", "inventors", "ipc_classes", "links", "trial_links", "claims"):
			self.assertNotIn(key, row)


class PatentQueryEfficiencyTest(PatentFixture):
	def _populate(self, n, start=0):
		cat = TeamCategory.objects.get_or_create(
			team=self.s_on.team,
			category_slug="eff",
			defaults={"category_name": "Eff", "category_terms": ["eff"]},
		)[0]
		cat.subjects.add(self.s_on)
		trial = Trials.objects.filter(title="Eff trial").first() or _trial("Eff trial", self.s_on)
		for i in range(start, start + n):
			patent = _patent(f"Eff {i}", self.s_on, family_id=f"9{i:04d}")
			PatentPublication.objects.create(
				patent=patent,
				publication_number=f"EP9{i:05d}A1",
				country="EP",
				doc_number=f"9{i:05d}",
				kind="A1",
			)
			PatentPublication.objects.create(
				patent=patent,
				publication_number=f"WO9{i:05d}A1",
				country="WO",
				doc_number=f"9{i:05d}",
				kind="A1",
			)
			PatentApplicant.objects.create(
				patent=patent, sponsor=self.sponsor, raw_name="ACME", sequence=0
			)
			PatentApplicant.objects.create(
				patent=patent, sponsor=None, raw_name=f"P {i}", sequence=1, is_individual=True
			)
			PatentCategoryAssignment.objects.create(
				patents=patent, teamcategory=cat, source="automatic"
			)
			other_trial = _trial(f"Eff trial {i}", self.s_on)
			_link(patent, other_trial, self.sponsor)

	def _count(self, path):
		# The first request in a process pays for one-off caches (Site,
		# ContentType); measure the second so only per-row queries show.
		self.client.get(path)
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(path)
		self.assertEqual(response.status_code, 200)
		return len(ctx)

	def test_list_cost_does_not_grow_with_rows(self):
		self._populate(3)
		path = f"/patents/?site_id={self.site_a.pk}&page_size=50"
		small = self._count(path)
		self._populate(20, start=3)
		large = self._count(path)
		self.assertEqual(small, large)

	def test_detail_cost_does_not_grow_with_links(self):
		self._populate(1)
		patent = Patents.objects.get(title="Eff 0")
		path = f"/patents/{patent.pk}/?site_id={self.site_a.pk}"
		small = self._count(path)
		for i in range(8):
			_link(patent, _trial(f"More {i}", self.s_on), self.sponsor)
		large = self._count(path)
		self.assertEqual(small, large)

	def test_trial_detail_cost_does_not_grow_with_patent_links(self):
		trial = _trial("Many patents", self.s_on)
		_link(_patent("One", self.s_on), trial, self.sponsor)
		path = f"/trials/{trial.pk}/?site_id={self.site_a.pk}"
		small = self._count(path)
		for i in range(8):
			_link(_patent(f"More {i}", self.s_on), trial, self.sponsor)
		large = self._count(path)
		self.assertEqual(small, large)


class SponsorPatentApplicantsTest(PatentFixture):
	"""Without the flag /sponsors/ is exactly what it was; with it, sponsors
	that only applied for patents in scope are added, and only those."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		# acme: trial in S_on AND an applicant on p_on.
		# applicant_only: applicant on p_on, no trial.
		# off_applicant: applicant on p_off only (site B has patents off).
		# nobody: nothing at all.
		cls.applicant_only = Sponsor.objects.create(name="Applicant Only", slug="applicant-only")
		cls.off_applicant = Sponsor.objects.create(name="Off Applicant", slug="off-applicant")
		cls.nobody = Sponsor.objects.create(name="Nobody", slug="nobody")
		# update(), not save(): Trials.save() re-derives the normalised sponsor.
		trial = _trial("Acme trial", cls.s_on)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=cls.sponsor)
		off_trial = _trial("Off trial", cls.s_off)
		Trials.objects.filter(pk=off_trial.pk).update(primary_sponsor_normalized=cls.off_applicant)
		PatentApplicant.objects.create(patent=cls.p_on, sponsor=cls.sponsor, raw_name="Acme", sequence=0)
		PatentApplicant.objects.create(patent=cls.p_on, sponsor=cls.applicant_only, raw_name="AO", sequence=1)
		PatentApplicant.objects.create(patent=cls.p_off, sponsor=cls.off_applicant, raw_name="OA", sequence=0)

	def names(self, query, site):
		response = self.client.get(f"/sponsors/?site_id={site.pk}&{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return {r["name"]: r for r in response.data["results"]}

	def test_without_the_flag_rows_are_the_trial_sponsors_only(self):
		rows = self.names("", self.site_a)
		self.assertEqual(set(rows), {"Acme Pharma"})

	def test_without_the_flag_on_a_site_without_patents(self):
		rows = self.names("", self.site_b)
		self.assertEqual(set(rows), {"Off Applicant"})

	def test_flag_adds_patent_only_applicants_in_scope(self):
		rows = self.names("include_patent_applicants=true", self.site_a)
		self.assertEqual(set(rows), {"Acme Pharma", "Applicant Only"})
		self.assertEqual(rows["Applicant Only"]["trials_count"], 0)
		self.assertEqual(rows["Applicant Only"]["patents_count"], 1)

	def test_flag_does_nothing_on_a_site_without_patents(self):
		rows = self.names("include_patent_applicants=true", self.site_b)
		self.assertEqual(set(rows), {"Off Applicant"})

	def test_patents_count_is_scoped(self):
		rows = self.names("", self.site_a)
		self.assertEqual(rows["Acme Pharma"]["patents_count"], 1)
		self.assertEqual(rows["Acme Pharma"]["trials_count"], 1)
		rows = self.names("", self.site_b)
		self.assertEqual(rows["Off Applicant"]["patents_count"], 0)
		self.assertEqual(rows["Off Applicant"]["trials_count"], 1)

	def test_count_cache_is_not_shared_between_patent_scopes(self):
		on = self.client.get(f"/sponsors/?site_id={self.site_a.pk}&include_patent_applicants=true")
		other = publish_subjects(
			self.s_on, organization=self.org, domain="pat-s.example.com", patents_enabled=False
		)
		off = self.client.get(f"/sponsors/?site_id={other.pk}&include_patent_applicants=true")
		self.assertEqual(on.data["count"], 2)
		self.assertEqual(off.data["count"], 1)


class CategoryPatentCountTest(PatentFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat = TeamCategory.objects.create(
			team=cls.s_on.team, category_name="Shared", category_slug="shared", category_terms=["x"]
		)
		cls.cat.subjects.add(cls.s_on, cls.s_off)
		for patent in (cls.p_on, cls.p_off, cls.p_both):
			PatentCategoryAssignment.objects.create(
				patents=patent, teamcategory=cls.cat, source="automatic"
			)

	def count(self, site):
		response = self.client.get(f"/categories/?site_id={site.pk}&category_id={self.cat.pk}")
		self.assertEqual(response.status_code, 200, response.content)
		return response.data["results"][0]["patents_count_total"]

	def test_counts_only_patents_visible_through_sites_with_the_switch(self):
		# p_on and p_both carry S_on; p_off carries only S_off (site B, off).
		self.assertEqual(self.count(self.site_a), 2)

	def test_zero_through_a_site_without_the_switch(self):
		self.assertEqual(self.count(self.site_b), 0)


class TenantDirectoryTest(PatentFixture):
	def test_tenants_carry_patents_enabled(self):
		CustomSetting.objects.filter(site__in=[self.site_a, self.site_b]).update(mcp_enabled=True)
		response = self.client.get("/tenants/")
		self.assertEqual(response.status_code, 200)
		by_site = {t["site_id"]: t for t in response.data}
		self.assertIs(by_site[self.site_a.pk]["patents_enabled"], True)
		self.assertIs(by_site[self.site_b.pk]["patents_enabled"], False)

	def test_private_tenant_for_its_own_key(self):
		CustomSetting.objects.filter(site=self.site_p).update(mcp_enabled=True)
		key = self.key_for(self.site_p)
		response = self.client.get("/tenants/", HTTP_AUTHORIZATION=key.api_key)
		by_site = {t["site_id"]: t for t in response.data}
		self.assertIs(by_site[self.site_p.pk]["patents_enabled"], True)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class EditorTenantDirectoryTest(PatentFixture):
	def test_editor_tenants_carry_patents_enabled(self):
		CustomSetting.objects.filter(site__in=[self.site_a, self.site_b, self.site_p]).update(
			mcp_enabled=True
		)
		ana = make_user("pat-tenant-ana", self.org)
		grant(ana, self.site_p)
		response = self.client.get(
			"/editor/tenants/",
			HTTP_X_GREGORY_EDITOR_USER=str(ana.pk),
			HTTP_X_GREGORY_EDITOR_SITE=str(self.site_p.pk),
			HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}",
		)
		self.assertEqual(response.status_code, 200)
		by_site = {t["site_id"]: t for t in response.data}
		self.assertIs(by_site[self.site_a.pk]["patents_enabled"], True)
		self.assertIs(by_site[self.site_b.pk]["patents_enabled"], False)
		self.assertIs(by_site[self.site_p.pk]["patents_enabled"], True)


class ExistingVisibilityUnchangedTest(PatentFixture):
	"""visible_subject_ids must return what it always did, whatever the
	patents switch says."""

	def test_trials_do_not_depend_on_the_switch(self):
		_trial("Trial in B", self.s_off)
		response = self.client.get(f"/trials/?site_id={self.site_b.pk}")
		self.assertEqual(_titles(response), ["Trial in B"])

	def test_visible_subject_ids_ignores_the_switch(self):
		from django.test import RequestFactory
		from gregory.visibility import visible_patent_subject_ids, visible_subject_ids

		request = RequestFactory().get(f"/x/?site_id={self.site_b.pk}")
		request.user = type("Anon", (), {"is_authenticated": False})()
		self.assertEqual(visible_subject_ids(request), {self.s_off.pk})
		self.assertEqual(visible_patent_subject_ids(request), set())


class ReviewFixesTest(PatentFixture):
	"""Regression tests for the PR #919 review notes."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat = TeamCategory.objects.create(
			team=cls.s_on.team, category_name="Fix", category_slug="fix", category_terms=["fix"]
		)
		cls.cat.subjects.add(cls.s_on)
		PatentCategoryAssignment.objects.create(
			patents=cls.p_on, teamcategory=cls.cat, source="automatic"
		)
		cls.p_on.claims = "1. A claim."
		cls.p_on.inventors = ["DOE, J"]
		cls.p_on.cpc_classes = ["A61K31/435"]
		cls.p_on.save()
		PatentPublication.objects.create(
			patent=cls.p_on,
			publication_number="EP4123456B1",
			country="EP",
			doc_number="4123456",
			kind="B1",
		)
		PatentPublication.objects.create(
			patent=cls.p_both,
			publication_number="EP7777777B2",
			country="EP",
			doc_number="7777777",
			kind="B2",
		)
		PatentPublication.objects.create(
			patent=cls.p_off,
			publication_number="EP5555555A1",
			country="EP",
			doc_number="5555555",
			kind="A1",
		)

	def captured(self, path):
		cache.clear()
		self.client.get(path)  # warm one-off caches
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(path)
		return response, [q["sql"] for q in ctx.captured_queries]

	# 1. the category fallback fails closed
	def test_category_count_without_the_annotation_is_zero(self):
		from api.serializers import CategorySerializer

		self.assertEqual(CategorySerializer().get_patents_count_total(self.cat), 0)

	# 2. no request, no links
	def test_link_queryset_without_a_request_is_empty(self):
		from api.patent_links import visible_patent_trial_links_queryset

		trial = _trial("Orphan trial", self.s_on)
		_link(self.p_on, trial, self.sponsor)
		self.assertEqual(list(visible_patent_trial_links_queryset(None)), [])
		self.assertEqual(list(visible_patent_trial_links_queryset(None, True)), [])

	def test_detail_serializers_without_a_request_show_no_links(self):
		from api.serializers import PatentDetailSerializer, TrialDetailSerializer

		trial = _trial("Orphan trial two", self.s_on)
		_link(self.p_on, trial, self.sponsor)
		self.assertEqual(PatentDetailSerializer().get_trial_links(self.p_on), [])
		self.assertEqual(TrialDetailSerializer().get_patent_links(trial), [])

	# 3. a letter-only kind code
	def test_kind_letter_alone_matches_the_stored_kind_with_digit(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&publication_number=EP4123456B"
		)
		self.assertEqual(_titles(response), ["Patent on"])
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&publication_number=EP7777777B"
		)
		self.assertEqual(_titles(response), ["Patent both"])

	def test_kind_letter_does_not_match_a_different_letter(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&publication_number=EP4123456A"
		)
		self.assertEqual(response.data["results"], [])

	def test_full_kind_stays_exact(self):
		response = self.client.get(
			f"/patents/?site_id={self.site_a.pk}&publication_number=EP4123456B2"
		)
		self.assertEqual(response.data["results"], [])

	# 4. heavy columns are not loaded unless used
	def test_list_does_not_select_claims_or_other_detail_columns(self):
		response, queries = self.captured(f"/patents/?site_id={self.site_a.pk}")
		self.assertEqual(response.status_code, 200)
		listing = next(q for q in queries if 'FROM "patents"' in q and "LIMIT" in q)
		for column in ('"patents"."claims"', '"patents"."inventors"', '"patents"."ipc_classes"'):
			self.assertNotIn(column, listing)

	def test_detail_without_include_claims_does_not_select_claims(self):
		response, queries = self.captured(
			f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}"
		)
		self.assertNotIn("claims", response.data)
		row = next(q for q in queries if 'FROM "patents"' in q and "LIMIT" in q)
		self.assertNotIn('"patents"."claims"', row)
		self.assertIn('"patents"."inventors"', row)
		# Not read, so not re-fetched either: no query touches claims.
		self.assertFalse([q for q in queries if '"claims"' in q])

	def test_detail_with_include_claims_selects_and_returns_them(self):
		response, queries = self.captured(
			f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}&include=claims"
		)
		self.assertEqual(response.data["claims"], "1. A claim.")
		self.assertTrue([q for q in queries if '"patents"."claims"' in q])

	def test_detail_cost_is_the_same_with_and_without_claims(self):
		_, without = self.captured(f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}")
		_, with_claims = self.captured(
			f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}&include=claims"
		)
		self.assertEqual(len(without), len(with_claims))

	# 5. sponsors skip the patent work when nothing shows patents
	def test_sponsors_on_a_site_without_patents_run_no_patent_query(self):
		trial = _trial("Off sponsor trial", self.s_off)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=self.sponsor)
		response, queries = self.captured(f"/sponsors/?site_id={self.site_b.pk}")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["results"][0]["patents_count"], 0)
		self.assertFalse([q for q in queries if "patent_applicants" in q])

	def test_sponsors_on_a_site_without_patents_ignore_the_applicant_flag(self):
		response, queries = self.captured(
			f"/sponsors/?site_id={self.site_b.pk}&include_patent_applicants=true"
		)
		self.assertEqual(response.status_code, 200)
		self.assertFalse([q for q in queries if "patent_applicants" in q])

	def test_sponsors_on_a_site_with_patents_still_count_them(self):
		trial = _trial("On sponsor trial", self.s_on)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=self.sponsor)
		PatentApplicant.objects.create(
			patent=self.p_on, sponsor=self.sponsor, raw_name="Acme", sequence=0
		)
		response, queries = self.captured(f"/sponsors/?site_id={self.site_a.pk}")
		self.assertEqual(response.data["results"][0]["patents_count"], 1)
		self.assertTrue([q for q in queries if "patent_applicants" in q])

	# 6. one parser for the boolean flags
	def test_yes_is_not_a_true_value_for_the_flags(self):
		trial = _trial("Flag trial", self.s_on)
		_link(self.p_on, trial, self.sponsor, basis="subject")
		for value, expected in (("true", 1), ("TRUE", 1), ("1", 1), ("yes", 0), ("0", 0)):
			with self.subTest(value=value):
				response = self.client.get(
					f"/patents/{self.p_on.pk}/?site_id={self.site_a.pk}"
					f"&include_subject_links={value}"
				)
				self.assertEqual(len(response.data["trial_links"]), expected)

	def test_applicant_flag_uses_the_same_values(self):
		applicant = Sponsor.objects.create(name="Only Applicant", slug="only-applicant")
		PatentApplicant.objects.create(
			patent=self.p_on, sponsor=applicant, raw_name="OA", sequence=0
		)
		for value, expected in (("true", 1), ("1", 1), ("yes", 0)):
			with self.subTest(value=value):
				response = self.client.get(
					f"/sponsors/?site_id={self.site_a.pk}&include_patent_applicants={value}"
				)
				self.assertEqual(response.data["count"], expected)

	# 7. the CPC filter survives being a subquery
	def test_cpc_filter_inside_a_subquery_binds_to_the_inner_table(self):
		from api.filters import PatentFilter

		filtered = PatentFilter(
			data={"cpc": "A61K31"}, queryset=Patents.objects.all()
		).qs
		self.assertEqual([p.pk for p in filtered], [self.p_on.pk])
		via_in = Patents.objects.filter(pk__in=filtered.values("pk"))
		self.assertEqual([p.pk for p in via_in], [self.p_on.pk])
		# Uncorrelated Exists() over the filter, evaluated for an outer row that
		# has no matching CPC class. True because p_on matches somewhere; a
		# filter bound to the OUTER table (a hard-coded table name once the
		# inner one is re-aliased) would look at p_both's own empty classes
		# and wrongly say no.
		from django.db.models import Exists

		via_exists = Patents.objects.filter(Exists(filtered), pk=self.p_both.pk)
		self.assertEqual([p.pk for p in via_exists], [self.p_both.pk])
