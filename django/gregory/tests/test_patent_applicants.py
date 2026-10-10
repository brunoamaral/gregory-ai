"""Tests for gregory.utils.patent_applicants."""

from django.test import SimpleTestCase, TestCase

from gregory.models import PatentApplicant, Patents, Sponsor, SponsorAlias
from gregory.utils.epo_ops import Biblio
from gregory.utils.patent_applicants import (
	applicant_names,
	resolve_applicants,
	save_applicants,
	strip_epodoc_suffix,
)
from gregory.utils.trial_field_normalizers import normalize_sponsor_key


def biblio(original=(), epodoc=(), inventors=()):
	return Biblio(
		publication_number="EP1A1",
		country="EP",
		doc_number="1",
		kind="A1",
		applicants_original=list(original),
		applicants_epodoc=list(epodoc),
		inventors=list(inventors),
	)


class ApplicantNamesTests(SimpleTestCase):
	def test_strip_epodoc_suffix(self):
		self.assertEqual(strip_epodoc_suffix("TDK SYSTEMS EUROP LTD [GB]"), "TDK SYSTEMS EUROP LTD")
		self.assertEqual(strip_epodoc_suffix("IBM"), "IBM")

	def test_original_preferred_and_deduplicated_by_key(self):
		names = applicant_names(
			biblio(original=["9SOLUTIONS OY", "9Solutions Oy"], epodoc=["9SOLUTIONS OY [FI]"])
		)
		self.assertEqual(names, ["9SOLUTIONS OY"])

	def test_epodoc_fallback_strips_country_suffix(self):
		self.assertEqual(applicant_names(biblio(epodoc=["DOPAMINE LABS LTD [GB]"])), ["DOPAMINE LABS LTD"])


class ResolveApplicantsTests(TestCase):
	def test_exact_alias_hit_links_existing_sponsor(self):
		sponsor = Sponsor.objects.create(name="Biogen", slug="biogen", sponsor_type="industry", sponsor_type_source="ctgov")
		SponsorAlias.objects.create(sponsor=sponsor, key=normalize_sponsor_key("Biogen"), raw_sample="Biogen")
		resolved = resolve_applicants(biblio(original=["BIOGEN"]))
		self.assertEqual(len(resolved), 1)
		self.assertEqual(resolved[0].sponsor, sponsor)
		self.assertFalse(resolved[0].is_individual)

	def test_existing_ctgov_type_is_not_overridden(self):
		# "Pharma" in the name would make the keyword rules say industry; the sponsor is
		# academic from ClinicalTrials.gov evidence and must stay that way.
		sponsor = Sponsor.objects.create(
			name="Oxford Pharma Institute", slug="oxford-pharma", sponsor_type="academic", sponsor_type_source="ctgov"
		)
		SponsorAlias.objects.create(
			sponsor=sponsor, key=normalize_sponsor_key("Oxford Pharma Institute"), raw_sample="x"
		)
		resolve_applicants(biblio(original=["Oxford Pharma Institute"]))
		sponsor.refresh_from_db()
		self.assertEqual((sponsor.sponsor_type, sponsor.sponsor_type_source), ("academic", "ctgov"))

	def test_new_applicant_creates_sponsor_with_rules_type(self):
		resolved = resolve_applicants(biblio(original=["Neuronova Therapeutics Inc."]))
		sponsor = resolved[0].sponsor
		self.assertEqual(sponsor.name, "Neuronova Therapeutics Inc.")
		self.assertTrue(
			SponsorAlias.objects.filter(sponsor=sponsor, key="neuronova therapeutics inc").exists()
		)
		if sponsor.sponsor_type:
			self.assertEqual(sponsor.sponsor_type_source, "rules")

	def test_applicant_who_is_inventor_is_individual(self):
		resolved = resolve_applicants(
			biblio(original=["Garcia, Maria"], inventors=["GARCIA MARIA"])
		)
		self.assertTrue(resolved[0].is_individual)
		self.assertIsNone(resolved[0].sponsor)
		self.assertEqual(Sponsor.objects.count(), 0)

	def test_second_resolution_reuses_the_sponsor(self):
		first = resolve_applicants(biblio(original=["Acme Neuro"]))[0].sponsor
		second = resolve_applicants(biblio(original=["ACME NEURO"]))[0].sponsor
		self.assertEqual(first, second)
		self.assertEqual(Sponsor.objects.count(), 1)

	def test_save_applicants_is_idempotent_and_ordered(self):
		patent = Patents.objects.create(family_id="1", title="P")
		data = biblio(original=["Acme Neuro", "Garcia, Maria"], inventors=["GARCIA, MARIA"])
		save_applicants(patent, data)
		save_applicants(patent, data)
		rows = list(PatentApplicant.objects.filter(patent=patent).order_by("sequence"))
		self.assertEqual([r.sequence for r in rows], [1, 2])
		self.assertEqual([r.is_individual for r in rows], [False, True])
