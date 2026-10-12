"""Tests for the patent models, their configuration hooks (Sources, credentials,
TeamCategory) and the admin."""

import datetime
from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from organizations.models import Organization, OrganizationUser

from gregory.admin import PatentAdmin
from gregory.models import (
	OrganizationCredentials,
	PatentApplicant,
	PatentCategoryAssignment,
	PatentPublication,
	Patents,
	Sources,
	Sponsor,
	SponsorAlias,
	Subject,
	Team,
	TeamCategory,
	default_match_weights,
)
from gregory.utils.sponsor_merge import merge_sponsors
from subscriptions.management.commands.utils.get_credentials import (
	get_epo_ops_credentials,
)

User = get_user_model()


def make_org_team(slug):
	org = Organization.objects.create(name=f"Org {slug}", slug=f"org-{slug}")
	team = Team.objects.create(organization=org, name=f"Team {slug}", slug=f"team-{slug}")
	return org, team


class PatentModelTests(TestCase):
	def setUp(self):
		self.patent = Patents.objects.create(
			family_id="12345", title="Anti-CD20 antibody for multiple sclerosis"
		)

	def test_generated_uppercase_columns(self):
		self.patent.refresh_from_db()
		self.assertEqual(self.patent.utitle, "ANTI-CD20 ANTIBODY FOR MULTIPLE SCLEROSIS")
		self.assertIsNone(self.patent.usummary)

	def test_family_id_is_unique(self):
		with self.assertRaises(IntegrityError), transaction.atomic():
			Patents.objects.create(family_id="12345", title="Duplicate")

	def test_publication_number_is_unique_across_families(self):
		other = Patents.objects.create(family_id="999", title="Other")
		PatentPublication.objects.create(patent=self.patent, publication_number="EP1A1")
		with self.assertRaises(IntegrityError), transaction.atomic():
			PatentPublication.objects.create(patent=other, publication_number="EP1A1")

	def test_applicant_sequence_is_unique_per_patent(self):
		PatentApplicant.objects.create(patent=self.patent, raw_name="ACME", sequence=1)
		with self.assertRaises(IntegrityError), transaction.atomic():
			PatentApplicant.objects.create(patent=self.patent, raw_name="OTHER", sequence=1)
		PatentApplicant.objects.create(patent=self.patent, raw_name="OTHER", sequence=2)

	def test_sponsor_is_protected_while_applicant_rows_exist(self):
		from django.db.models import ProtectedError

		sponsor = Sponsor.objects.create(name="Acme", slug="acme")
		PatentApplicant.objects.create(patent=self.patent, sponsor=sponsor, raw_name="ACME")
		with self.assertRaises(ProtectedError):
			sponsor.delete()

	def test_individual_applicant_has_no_sponsor(self):
		row = PatentApplicant.objects.create(
			patent=self.patent, raw_name="Smith John", is_individual=True
		)
		self.assertIsNone(row.sponsor)

	def test_applicants_m2m_goes_through_applicant_rows(self):
		sponsor = Sponsor.objects.create(name="Acme", slug="acme")
		PatentApplicant.objects.create(patent=self.patent, sponsor=sponsor, raw_name="ACME")
		self.assertEqual(list(self.patent.applicants.all()), [sponsor])
		self.assertEqual(list(sponsor.patents.all()), [self.patent])

	def test_history_records_changes(self):
		self.patent.has_grant = True
		self.patent.save()
		self.assertEqual(self.patent.history.count(), 2)


class MergeSponsorsPatentTests(TestCase):
	def test_merge_repoints_patent_applicants(self):
		target = Sponsor.objects.create(name="Target", slug="target")
		source = Sponsor.objects.create(name="Source", slug="source")
		SponsorAlias.objects.create(sponsor=source, key="source", raw_sample="Source")
		patent = Patents.objects.create(family_id="1", title="P")
		row = PatentApplicant.objects.create(patent=patent, sponsor=source, raw_name="SOURCE")

		with self.assertLogs("gregory.utils.sponsor_merge", level="INFO") as logs:
			merge_sponsors(target, [source])

		row.refresh_from_db()
		self.assertEqual(row.sponsor, target)
		self.assertFalse(Sponsor.objects.filter(pk=source.pk).exists())
		self.assertTrue(any("1 patent applicant" in line for line in logs.output))


class EpoOpsCredentialsTests(TestCase):
	def setUp(self):
		self.org, _ = make_org_team("creds")

	def test_no_credentials_row(self):
		self.assertEqual(get_epo_ops_credentials(self.org), (None, None))

	def test_both_fields_required(self):
		creds = OrganizationCredentials.objects.create(
			organization=self.org, epo_ops_consumer_key="key"
		)
		self.assertEqual(get_epo_ops_credentials(self.org), (None, None))
		creds.epo_ops_consumer_secret = "secret"
		creds.save()
		org = Organization.objects.get(pk=self.org.pk)
		self.assertEqual(get_epo_ops_credentials(org), ("key", "secret"))


class PatentSourceTests(TestCase):
	def setUp(self):
		_, self.team = make_org_team("src")
		self.source = Sources.objects.create(
			name="MS patents", source_for="patents", method="epo_ops", team=self.team
		)

	def test_no_fetch_yet(self):
		self.assertEqual(self.source.get_health_status(), "no_content")

	def test_health_follows_last_successful_fetch(self):
		now = timezone.now()
		for days, expected in ((3, "healthy"), (20, "warning"), (45, "error")):
			self.source.last_successful_fetch_at = now - timedelta(days=days)
			self.assertEqual(self.source.get_health_status(), expected, days)

	def test_inactive(self):
		self.source.active = False
		self.assertEqual(self.source.get_health_status(), "inactive")

	def test_patent_count(self):
		patent = Patents.objects.create(family_id="1", title="P")
		patent.sources.add(self.source)
		self.assertEqual(self.source.get_patent_count(), 1)


class TeamCategoryPatentTests(TestCase):
	def setUp(self):
		_, self.team = make_org_team("cat")
		self.subject = Subject.objects.create(
			subject_name="MS", subject_slug="ms-cat", team=self.team
		)
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Ocrelizumab", category_terms=["ocrelizumab"]
		)

	def test_patent_weights_default(self):
		self.assertEqual(
			self.category.get_match_weights("patent"),
			{"title": 3, "summary": 2, "claims": 1},
		)
		self.assertIn("patent", default_match_weights())

	def test_legacy_match_weights_without_patent_key_fall_back(self):
		self.category.match_weights = {"article": {"title": 5}}
		self.category.save()
		self.assertEqual(self.category.get_match_weights("patent")["title"], 3)
		self.assertEqual(self.category.match_min_score_patents, 3)

	def test_patents_count(self):
		patent = Patents.objects.create(family_id="1", title="P")
		PatentCategoryAssignment.objects.create(
			patents=patent, teamcategory=self.category, source="automatic"
		)
		self.assertEqual(self.category.patents_count(), 1)


class PatentAdminTests(TestCase):
	def setUp(self):
		self.org_a, self.team_a = make_org_team("a")
		self.org_b, self.team_b = make_org_team("b")
		self.source_a = Sources.objects.create(
			name="A", source_for="patents", method="epo_ops", team=self.team_a
		)
		self.source_b = Sources.objects.create(
			name="B", source_for="patents", method="epo_ops", team=self.team_b
		)
		self.patent_a = Patents.objects.create(family_id="1", title="Patent of A")
		self.patent_a.sources.add(self.source_a)
		self.patent_b = Patents.objects.create(family_id="2", title="Patent of B")
		self.patent_b.sources.add(self.source_b)
		sponsor = Sponsor.objects.create(name="Acme", slug="acme", sponsor_type="industry")
		PatentApplicant.objects.create(patent=self.patent_a, sponsor=sponsor, raw_name="ACME")
		PatentPublication.objects.create(
			patent=self.patent_a,
			publication_number="EP1A1",
			publication_date=datetime.date(2024, 1, 1),
		)
		self.patent_a.earliest_priority_date = datetime.date(2022, 5, 5)
		self.patent_a.save()

		self.staff = User.objects.create_user(username="staff-a", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(content_type__model="patents")
		)
		self.superuser = User.objects.create_superuser(
			username="root", email="root@example.com", password="pw"
		)

	def test_queryset_scoped_to_user_organisation(self):
		request = RequestFactory().get("/")
		request.user = self.staff
		qs = PatentAdmin(Patents, AdminSite()).get_queryset(request)
		self.assertEqual(list(qs), [self.patent_a])

	def test_superuser_sees_all(self):
		request = RequestFactory().get("/")
		request.user = self.superuser
		qs = PatentAdmin(Patents, AdminSite()).get_queryset(request)
		self.assertEqual(qs.count(), 2)

	def test_changelist_loads_for_scoped_staff(self):
		client = Client()
		client.force_login(self.staff)
		response = client.get(reverse("admin:gregory_patents_changelist"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Patent of A")
		self.assertNotContains(response, "Patent of B")

	def test_changelist_filters_load(self):
		client = Client()
		client.force_login(self.superuser)
		url = reverse("admin:gregory_patents_changelist")
		for query in ("?priority_year=2022", "?has_grant__exact=0", "?applicants__sponsor_type=industry"):
			response = client.get(url + query)
			self.assertEqual(response.status_code, 200, query)

	def test_change_page_loads(self):
		client = Client()
		client.force_login(self.superuser)
		response = client.get(
			reverse("admin:gregory_patents_change", args=[self.patent_a.pk])
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "EP1A1")
