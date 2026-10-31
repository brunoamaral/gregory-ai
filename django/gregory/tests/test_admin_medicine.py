"""The Medicine admin is read-only, and every staff user can browse it.

Medicines are public EMA and FDA data with no team or organisation, so there is nothing to scope.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from gregory.models import Medicine, MedicineEvent, Regulator

User = get_user_model()


class MedicineAdminTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.medicine = Medicine.objects.create(
			ema_product_number="EMEA/H/C/004043",
			name="Ocrevus",
			inn="ocrelizumab",
			substances=["ocrelizumab"],
			status="Authorised",
			authorisation_date=date(2018, 1, 8),
			latest_ec_decision_date=date(2026, 10, 15),
			url="https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus",
		)
		MedicineEvent.objects.create(
			medicine=cls.medicine,
			kind=MedicineEvent.Kind.AUTHORISED,
			event_date=date(2018, 1, 8),
			basis=MedicineEvent.Basis.RECONSTRUCTED,
		)
		MedicineEvent.objects.create(
			medicine=cls.medicine,
			kind=MedicineEvent.Kind.EC_DECISION,
			event_date=date(2026, 10, 15),
			basis=MedicineEvent.Basis.RECONSTRUCTED,
		)
		# Staff with no model permissions at all.
		cls.staff = User.objects.create_user("staff", "staff@example.org", "x", is_staff=True)
		cls.outsider = User.objects.create_user("plain", "plain@example.org", "x")

	def test_staff_user_without_permissions_sees_the_list_and_the_page(self):
		self.client.force_login(self.staff)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		self.assertContains(response, "Ocrevus")
		response = self.client.get(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk])
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Latest EC decision")
		self.assertContains(response, 'href="https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus"')

	def test_events_are_listed_newest_first(self):
		self.client.force_login(self.staff)
		response = self.client.get(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk])
		)
		inline = next(
			i for i in response.context["inline_admin_formsets"] if i.formset.model is MedicineEvent
		)
		self.assertEqual(
			[form.instance.kind for form in inline.formset.forms],
			[MedicineEvent.Kind.EC_DECISION, MedicineEvent.Kind.AUTHORISED],
		)

	def test_nothing_can_be_added_changed_or_deleted(self):
		self.client.force_login(self.staff)
		self.assertEqual(self.client.get(reverse("admin:gregory_medicine_add")).status_code, 403)
		response = self.client.post(
			reverse("admin:gregory_medicine_change", args=[self.medicine.pk]),
			{"name": "Changed"},
		)
		self.assertEqual(response.status_code, 403)
		response = self.client.post(
			reverse("admin:gregory_medicine_delete", args=[self.medicine.pk]), {"post": "yes"}
		)
		self.assertEqual(response.status_code, 403)
		self.medicine.refresh_from_db()
		self.assertEqual(self.medicine.name, "Ocrevus")

	def test_a_non_staff_user_is_sent_to_login(self):
		self.client.force_login(self.outsider)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		self.assertEqual(response.status_code, 302)

	def test_search_by_product_number(self):
		self.client.force_login(self.staff)
		response = self.client.get(
			reverse("admin:gregory_medicine_changelist"), {"q": "004043"}
		)
		self.assertContains(response, "Ocrevus")
		response = self.client.get(reverse("admin:gregory_medicine_changelist"), {"q": "zzz"})
		self.assertNotContains(response, "Ocrevus")


class FdaMedicineAdminTests(TestCase):
	"""The same read-only admin shows FDA rows: their own fields, a regulator column and
	filter, and search by application number and brand."""

	@classmethod
	def setUpTestData(cls):
		cls.ema = Medicine.objects.create(
			ema_product_number="EMEA/H/C/004043",
			name="Ocrevus",
			substances=["ocrelizumab"],
			status="Authorised",
			url="https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus",
		)
		cls.fda = Medicine.objects.create(
			regulator=Regulator.FDA,
			fda_application_number="BLA761053",
			fda_application_type="BLA",
			fda_center="CDER",
			name="OCREVUS",
			brand_names=["OCREVUS", "ZUNOVO"],
			substances=["ocrelizumab"],
			status="Approved",
			holder="GENENTECH INC",
			fda_original_class="TYPE 1",
			fda_priority_review=True,
			fda_pharm_classes=["CD20-directed Cytolytic Antibody [EPC]"],
			authorisation_date=date(2017, 3, 28),
			url="https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=761053",
		)
		cls.cber = Medicine.objects.create(
			regulator=Regulator.FDA,
			fda_application_number="BLA125646",
			fda_center="CBER",
			name="Kymriah",
			status="Approved",
			url="https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=abc",
		)
		MedicineEvent.objects.create(
			medicine=cls.fda,
			kind=MedicineEvent.Kind.FDA_EFFICACY_SUPPLEMENT,
			event_date=date(2026, 5, 8),
			basis=MedicineEvent.Basis.RECONSTRUCTED,
			to_value="S-38",
		)
		cls.staff = User.objects.create_user("fda-staff", "fda-staff@example.org", "x", is_staff=True)

	def setUp(self):
		self.client.force_login(self.staff)

	def change_page(self, medicine):
		return self.client.get(reverse("admin:gregory_medicine_change", args=[medicine.pk]))

	def test_the_list_shows_the_regulator_and_filters_by_it(self):
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		self.assertContains(response, "OCREVUS")
		self.assertContains(response, "Kymriah")
		response = self.client.get(reverse("admin:gregory_medicine_changelist"), {"regulator__exact": "fda"})
		self.assertContains(response, "Kymriah")
		self.assertEqual(response.context["cl"].result_count, 2)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"), {"regulator__exact": "ema"})
		self.assertEqual(response.context["cl"].result_count, 1)

	def test_search_by_application_number_and_by_brand(self):
		url = reverse("admin:gregory_medicine_changelist")
		self.assertEqual(self.client.get(url, {"q": "761053"}).context["cl"].result_count, 1)
		self.assertEqual(self.client.get(url, {"q": "zunovo"}).context["cl"].result_count, 1)
		self.assertEqual(self.client.get(url, {"q": "nothing-like-this"}).context["cl"].result_count, 0)

	def test_an_fda_page_shows_the_fda_fields_and_hides_the_ema_ones(self):
		response = self.change_page(self.fda)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "BLA761053")
		self.assertContains(response, "GENENTECH INC")
		self.assertContains(response, "TYPE 1")
		self.assertContains(response, "CD20-directed Cytolytic Antibody")
		self.assertContains(response, "Drugs@FDA</a>")
		self.assertNotContains(response, "EPAR page")
		self.assertNotContains(response, "Latest EC decision")
		self.assertNotContains(response, "Orphan medicine")
		# The event inline shows the submission number.
		self.assertContains(response, "S-38")

	def test_a_cber_page_links_to_dailymed(self):
		self.assertContains(self.change_page(self.cber), "DailyMed</a>")

	def test_the_link_pages_show_the_regulator_next_to_the_medicine(self):
		from organizations.models import Organization

		from gregory.models import MedicineCategoryAssignment, MedicineSubjectLink, Subject, Team, TeamCategory

		org = Organization.objects.create(name="Reg Org", slug="reg-org")
		team = Team.objects.create(organization=org, name="Reg Team", slug="reg-team")
		subject = Subject.objects.create(subject_name="MS", subject_slug="ms", team=team)
		category = TeamCategory.objects.create(team=team, category_name="Anti-CD20")
		MedicineCategoryAssignment.objects.create(medicine=self.fda, teamcategory=category)
		MedicineSubjectLink.objects.create(medicine=self.ema, subject=subject)
		admin_user = User.objects.create_superuser("fda-admin", "fda-admin@example.org", "x")
		self.client.force_login(admin_user)

		response = self.client.get(reverse("admin:gregory_medicinecategoryassignment_changelist"))
		self.assertContains(response, "FDA")
		self.assertContains(response, "Regulator")
		response = self.client.get(reverse("admin:gregory_medicinesubjectlink_changelist"))
		self.assertContains(response, "EMA")
		response = self.client.get(reverse("admin:gregory_teamcategory_change", args=[category.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "OCREVUS (BLA761053)")
		self.assertContains(response, "FDA")

	def test_the_category_autocomplete_offers_fda_medicines_too(self):
		admin_user = User.objects.create_superuser("fda-auto", "fda-auto@example.org", "x")
		self.client.force_login(admin_user)
		params = {
			"app_label": "gregory",
			"model_name": "medicinecategoryassignment",
			"field_name": "medicine",
		}
		url = reverse("admin:autocomplete")
		results = self.client.get(url, {**params, "term": "ocrevus"}).json()["results"]
		self.assertEqual({r["id"] for r in results}, {str(self.ema.pk), str(self.fda.pk)})
		kymriah = self.client.get(url, {**params, "term": "kymriah"}).json()["results"]
		self.assertEqual(len(kymriah), 1)

	def test_the_medicine_list_search_finds_fda_rows(self):
		url = reverse("admin:gregory_medicine_changelist")
		self.assertEqual(self.client.get(url, {"q": "kymriah"}).context["cl"].result_count, 1)

	def test_an_fda_medicine_can_be_linked_to_a_category_by_hand(self):
		from django.forms import modelform_factory

		from gregory.admin import MedicineCategoryAssignmentForm
		from gregory.models import MedicineCategoryAssignment

		form_class = modelform_factory(
			MedicineCategoryAssignment, form=MedicineCategoryAssignmentForm, fields=["medicine", "suppressed"]
		)
		self.assertTrue(form_class(data={"medicine": self.fda.pk}).is_valid())
		self.assertTrue(form_class(data={"medicine": self.ema.pk}).is_valid())

	def test_an_fda_page_shows_each_dailymed_label_read_only(self):
		Medicine.objects.filter(pk=self.fda.pk).update(
			label_indications=[
				{
					"setid": "aaa-111",
					"version": "24",
					"title": "OCREVUS (OCRELIZUMAB) INJECTION",
					"published": "2026-05-20",
					"indication": "OCREVUS is indicated for multiple sclerosis.",
				},
				{
					"setid": "bbb-222",
					"version": "3",
					"title": "ZUNOVO <b>injection</b>",
					"published": "2026-01-02",
					"indication": "ZUNOVO is indicated for primary progressive MS.",
				},
			]
		)
		response = self.change_page(self.fda)
		self.assertContains(response, "DailyMed labels")
		self.assertContains(response, "OCREVUS (OCRELIZUMAB) INJECTION")
		self.assertContains(response, "Published 2026-05-20 (version 24)")
		self.assertContains(response, "OCREVUS is indicated for multiple sclerosis.")
		self.assertContains(response, "ZUNOVO is indicated for primary progressive MS.")
		self.assertContains(response, "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=aaa-111")
		# A title is text, never markup.
		self.assertNotContains(response, "ZUNOVO <b>injection</b>")
		self.assertContains(response, "ZUNOVO &lt;b&gt;injection&lt;/b&gt;")

	def test_an_fda_page_without_labels_says_so_and_an_ema_page_has_no_labels(self):
		self.assertContains(self.change_page(self.fda), "DailyMed labels")
		self.assertNotContains(self.change_page(self.ema), "DailyMed labels")

	def test_an_ema_page_hides_the_fda_fields(self):
		response = self.change_page(self.ema)
		self.assertContains(response, "EPAR page")
		self.assertContains(response, "Latest EC decision")
		self.assertNotContains(response, "Application number")
		self.assertNotContains(response, "Brand names")
