"""Tests for the patent coverage heuristic and the patent admin's coverage and
category-modality filters."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.models import (
	CategoryModality,
	PatentCategoryAssignment,
	Patents,
	Sources,
	Subject,
	Team,
	TeamCategory,
)
from gregory.utils import patent_coverage as pc

User = get_user_model()

# One family per bucket, plus edge cases, with the bucket each should land in.
CASES = {
	"compound": (["A61K31/4439", "A61P25/28", "C07D401/04"], pc.NEW_SUBSTANCE),
	"antibody": (["A61P37/02", "C07K16/2887"], pc.NEW_SUBSTANCE),
	"cell therapy": (["A61K35/17", "A61P25/00"], pc.NEW_SUBSTANCE),
	"gene therapy": (["A61K48/005", "A61P25/00"], pc.NEW_SUBSTANCE),
	"repurposing": (["A61K31/00", "A61P25/00"], pc.NEW_USE),
	"plant extract": (["A61K36/185", "A61P25/00"], pc.NEW_USE),
	"stimulator": (["A61N1/36", "A61B5/00"], pc.DEVICE),
	"biomarker": (["G01N33/6896", "C12Q1/6883"], pc.DIAGNOSTIC),
	"imaging": (["A61B5/055", "G06T7/00"], pc.DIAGNOSTIC),
	"medicine without activity code": (["A61K31/519", "C07D487/04"], pc.NEW_SUBSTANCE),
	"formulation without activity code": (["A61K9/0053", "A61K31/00"], pc.NEW_USE),
	"cosmetic": (["A61K8/97", "A61Q19/00"], pc.OTHER),
	"contrast agent": (["A61K49/00", "G01N33/58"], pc.DIAGNOSTIC),
	"food": (["A23L33/105"], pc.OTHER),
	"unclassified": ([], pc.OTHER),
	# C12N 15 is gene therapy; C12N 1 (micro-organisms) is not
	"microbe": (["A61P25/00", "C12N1/20"], pc.NEW_USE),
}


class ClassifyTests(SimpleTestCase):
	def test_cases(self):
		for name, (cpc, bucket) in CASES.items():
			with self.subTest(name):
				self.assertEqual(pc.classify(cpc), bucket)

	def test_none_is_other(self):
		self.assertEqual(pc.classify(None), pc.OTHER)

	def test_every_bucket_has_labels(self):
		for bucket in (pc.NEW_SUBSTANCE, pc.NEW_USE, pc.DEVICE, pc.DIAGNOSTIC, pc.OTHER):
			self.assertIn(bucket, pc.COVERAGE_LABELS)
			self.assertIn(bucket, pc.COVERAGE_SHORT_LABELS)

	def test_unknown_bucket_raises(self):
		with self.assertRaises(ValueError):
			pc.coverage_q("nope")


class CoverageQueryTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.patents = {
			name: Patents.objects.create(family_id=str(i), title=name, cpc_classes=cpc)
			for i, (name, (cpc, _)) in enumerate(CASES.items())
		}

	def test_sql_matches_python_and_buckets_partition(self):
		seen = set()
		for bucket, _ in pc.COVERAGE_CHOICES:
			with self.subTest(bucket):
				qs = pc.with_cpc_text(Patents.objects.all()).filter(pc.coverage_q(bucket))
				got = set(qs.values_list("title", flat=True))
				expected = {name for name, (_, b) in CASES.items() if b == bucket}
				self.assertEqual(got, expected)
				self.assertFalse(got & seen)
				seen |= got
		self.assertEqual(seen, set(CASES))


def make_org_team(suffix):
	org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
	team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}")
	return org, team


class PatentAdminFilterTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org_a, cls.team_a = make_org_team("a")
		cls.org_b, cls.team_b = make_org_team("b")
		source_a = Sources.objects.create(
			name="A", source_for="patents", method="epo_ops", team=cls.team_a
		)
		cls.drug = Patents.objects.create(
			family_id="1", title="Drug patent", cpc_classes=["A61P25/00", "C07D401/04"]
		)
		cls.test_kit = Patents.objects.create(
			family_id="2", title="Test kit patent", cpc_classes=["G01N33/68"]
		)
		for p in (cls.drug, cls.test_kit):
			p.sources.add(source_a)

		subject_a = Subject.objects.create(subject_name="MS A", subject_slug="ms-a", team=cls.team_a)
		subject_b = Subject.objects.create(subject_name="MS B", subject_slug="ms-b", team=cls.team_b)
		cat_a = TeamCategory.objects.create(
			team=cls.team_a,
			category_name="Drug A",
			category_slug="drug-a",
			modality=CategoryModality.SMALL_MOLECULE,
		)
		cat_a.subjects.add(subject_a)
		# Another organisation tagged the test kit as an antibody
		cat_b = TeamCategory.objects.create(
			team=cls.team_b,
			category_name="Drug B",
			category_slug="drug-b",
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		cat_b.subjects.add(subject_b)
		PatentCategoryAssignment.objects.create(patents=cls.drug, teamcategory=cat_a)
		PatentCategoryAssignment.objects.create(patents=cls.test_kit, teamcategory=cat_b)

		cls.staff = User.objects.create_user(username="staff-a", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=cls.org_a, user=cls.staff)
		cls.staff.user_permissions.add(*Permission.objects.filter(content_type__model="patents"))
		cls.superuser = User.objects.create_superuser(
			username="root", email="root@example.com", password="pw"
		)
		cls.url = reverse("admin:gregory_patents_changelist")

	def titles(self, user, query):
		client = Client()
		client.force_login(user)
		response = client.get(self.url + query)
		self.assertEqual(response.status_code, 200, query)
		return {p.title for p in response.context["cl"].result_list}

	def test_coverage_filter(self):
		self.assertEqual(self.titles(self.superuser, "?covers=new_substance"), {"Drug patent"})
		self.assertEqual(self.titles(self.superuser, "?covers=diagnostic"), {"Test kit patent"})
		self.assertEqual(self.titles(self.superuser, "?covers=new_use"), set())

	def test_coverage_filter_combines_with_search(self):
		self.assertEqual(self.titles(self.superuser, "?covers=new_substance&q=kit"), set())

	def test_unknown_values_are_ignored(self):
		everything = {"Drug patent", "Test kit patent"}
		self.assertEqual(self.titles(self.superuser, "?covers=bogus"), everything)
		self.assertEqual(self.titles(self.superuser, "?modality=bogus"), everything)

	def test_modality_filter_for_superuser(self):
		self.assertEqual(
			self.titles(self.superuser, "?modality=biologic_antibody"), {"Test kit patent"}
		)
		self.assertEqual(self.titles(self.superuser, "?modality=small_molecule"), {"Drug patent"})

	def test_modality_filter_ignores_other_organisations_categories(self):
		self.assertEqual(self.titles(self.staff, "?modality=biologic_antibody"), set())
		self.assertEqual(self.titles(self.staff, "?modality=small_molecule"), {"Drug patent"})

	def test_coverage_column(self):
		client = Client()
		client.force_login(self.staff)
		response = client.get(self.url)
		self.assertContains(response, "What it covers")
		self.assertContains(response, "New substance")
		self.assertContains(response, "Diagnostic")
