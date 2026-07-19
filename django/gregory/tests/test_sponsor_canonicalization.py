"""Tests for sponsor canonicalization: normalize_sponsor_key, map_sponsor_type, the
Trials.save() sponsor-resolution hook, and the sponsor-seed collision/merge-trap guards.

See TRIALS-SPONSOR-CANONICALIZATION-PLAN.md PR 1. Backfill-command coverage lives in
gregory/tests/management/test_backfill_trial_sponsors.py and
test_backfill_trial_sponsors_from_ctgov.py; sync_sponsor_seeds coverage lives in
gregory/tests/management/test_sync_sponsor_seeds.py.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from gregory.admin import TrialAdmin
from gregory.models import (
	Sponsor,
	SponsorAlias,
	Trials,
	_create_sponsor_for_key,
)

User = get_user_model()
from gregory.utils.sponsor_seeds import SPONSOR_SEEDS
from gregory.utils.trial_field_normalizers import (
	SponsorType,
	map_sponsor_type,
	normalize_sponsor_key,
)


# --- normalize_sponsor_key ------------------------------------------------------------


class NormalizeSponsorKeyTests(TestCase):
	def test_none_and_blank_return_none(self):
		self.assertIsNone(normalize_sponsor_key(None))
		self.assertIsNone(normalize_sponsor_key(""))
		self.assertIsNone(normalize_sponsor_key("   "))

	def test_whitespace_and_case_merge(self):
		self.assertEqual(
			normalize_sponsor_key("Novartis   Pharmaceuticals"),
			normalize_sponsor_key("novartis pharmaceuticals"),
		)
		self.assertEqual(
			normalize_sponsor_key("  Biogen  "), normalize_sponsor_key("Biogen")
		)

	def test_diacritics_fold(self):
		self.assertEqual(
			normalize_sponsor_key("sanofi-aventis recherche & développement"),
			normalize_sponsor_key("sanofi-aventis recherche and developpement"),
		)

	def test_ampersand_folds_to_and(self):
		self.assertEqual(
			normalize_sponsor_key("Merck Sharp & Dohme"),
			normalize_sponsor_key("Merck Sharp and Dohme"),
		)

	def test_trailing_punctuation_merges(self):
		self.assertEqual(
			normalize_sponsor_key("F. HOFFMANN-LA ROCHE LTD.,"),
			normalize_sponsor_key("F. Hoffmann-La Roche Ltd"),
		)

	def test_university_of_rochester_does_not_collide_with_roche(self):
		# Merge trap from the audit: "roche" is a substring of "Rochester", but this is a
		# whole-string key comparison, never substring matching.
		self.assertNotEqual(
			normalize_sponsor_key("University of Rochester"),
			normalize_sponsor_key("Hoffmann-La Roche"),
		)
		self.assertNotEqual(
			normalize_sponsor_key("University of Rochester"),
			normalize_sponsor_key("Roche"),
		)

	def test_legal_suffixes_are_not_stripped(self):
		# "Ltd"/"Inc"/"GmbH"/"AG" differences are an editorial merge decision (seed table),
		# never an automatic one.
		self.assertNotEqual(
			normalize_sponsor_key("Biogen Idec Ltd"), normalize_sponsor_key("Biogen Idec")
		)
		self.assertNotEqual(
			normalize_sponsor_key("Novartis Pharma AG"),
			normalize_sponsor_key("Novartis Pharma GmbH"),
		)

	def test_truncates_to_500_chars(self):
		long_name = "A" * 600
		self.assertEqual(len(normalize_sponsor_key(long_name)), 500)


# --- map_sponsor_type -------------------------------------------------------------------


class MapSponsorTypeTests(TestCase):
	def test_ctgov_industry(self):
		self.assertEqual(
			map_sponsor_type("INDUSTRY", None, "Whatever Inc"),
			(SponsorType.INDUSTRY, "ctgov"),
		)

	def test_ctgov_government_classes(self):
		for cls in ("NIH", "FED", "OTHER_GOV"):
			with self.subTest(cls=cls):
				self.assertEqual(
					map_sponsor_type(cls, None, "Whatever"),
					(SponsorType.GOVERNMENT, "ctgov"),
				)

	def test_ctgov_non_mapping_classes_fall_through(self):
		for cls in ("INDIV", "NETWORK", "OTHER", "AMBIG", "UNKNOWN"):
			with self.subTest(cls=cls):
				self.assertEqual(map_sponsor_type(cls, None, None), (None, None))

	def test_no_signal_returns_none_none(self):
		self.assertEqual(map_sponsor_type(None, None, None), (None, None))
		self.assertEqual(map_sponsor_type(None, None, "Unclassifiable Name Co"), (None, None))

	def test_ctis_raw_values(self):
		cases = [
			("Pharmaceutical company", SponsorType.INDUSTRY),
			("Pharmaceutical company, Pharmaceutical company", SponsorType.INDUSTRY),
			("Hospital/Clinic/Other health care facility", SponsorType.ACADEMIC_MEDICAL),
			("Laboratory/Research/Testing facility", SponsorType.ACADEMIC_MEDICAL),
			("Educational Institution", SponsorType.ACADEMIC_MEDICAL),
			("Patient organisation/association", SponsorType.NONPROFIT),
			("Industry", SponsorType.INDUSTRY),
		]
		for raw, expected in cases:
			with self.subTest(raw=raw):
				self.assertEqual(map_sponsor_type(None, raw, None), (expected, "ctis"))

	def test_ctis_wins_over_rules_when_ctgov_falls_through(self):
		self.assertEqual(
			map_sponsor_type("INDIV", "Industry", "University Hospital Trust"),
			(SponsorType.INDUSTRY, "ctis"),
		)

	def test_rule_ordering_academic_beats_industry(self):
		# Contains both an academic token ("University") and industry tokens ("AG") —
		# academic is checked first in the fixed ladder, so it must win.
		self.assertEqual(
			map_sponsor_type(None, None, "University Pharma AG"),
			(SponsorType.ACADEMIC_MEDICAL, "rules"),
		)

	def test_rule_government(self):
		self.assertEqual(
			map_sponsor_type(None, None, "Ministry of Health, Singapore"),
			(SponsorType.GOVERNMENT, "rules"),
		)

	def test_rule_nonprofit(self):
		self.assertEqual(
			map_sponsor_type(None, None, "Multiple Sclerosis Foundation"),
			(SponsorType.NONPROFIT, "rules"),
		)

	def test_rule_industry_last_resort(self):
		self.assertEqual(
			map_sponsor_type(None, None, "Acme Pharmaceuticals Inc."),
			(SponsorType.INDUSTRY, "rules"),
		)

	def test_nih_style_government_institute_name(self):
		self.assertEqual(
			map_sponsor_type(
				None, None, "National Institute of Neurological Disorders and Stroke (NINDS)"
			),
			(SponsorType.GOVERNMENT, "rules"),
		)


# --- Trials.save() sponsor resolution ----------------------------------------------------


class TrialSponsorResolutionTests(TestCase):
	def _trial(self, n, **extra):
		return Trials.objects.create(
			title=f"Trial {n}", link=f"https://example.com/sponsor-hook-{n}", **extra
		)

	def test_auto_creates_sponsor_on_first_sight(self):
		trial = self._trial(1, primary_sponsor="Acme Research Corp")
		self.assertIsNotNone(trial.primary_sponsor_normalized)
		self.assertEqual(trial.primary_sponsor_normalized.name, "Acme Research Corp")
		key = normalize_sponsor_key("Acme Research Corp")
		self.assertTrue(SponsorAlias.objects.filter(key=key).exists())

	def test_second_trial_with_same_key_reuses_sponsor(self):
		t1 = self._trial(2, primary_sponsor="Acme Research Corp")
		t2 = self._trial(3, primary_sponsor="  acme research corp  ")
		self.assertEqual(t1.primary_sponsor_normalized_id, t2.primary_sponsor_normalized_id)
		self.assertEqual(Sponsor.objects.filter(name__icontains="Acme Research").count(), 1)

	def test_blank_primary_sponsor_gives_null_fk(self):
		trial = self._trial(4, primary_sponsor=None)
		self.assertIsNone(trial.primary_sponsor_normalized)

		trial2 = self._trial(5, primary_sponsor="")
		self.assertIsNone(trial2.primary_sponsor_normalized)

	def test_resave_after_alias_repoint_picks_up_new_sponsor(self):
		trial = self._trial(6, primary_sponsor="Repoint Corp")
		key = normalize_sponsor_key("Repoint Corp")
		new_sponsor = Sponsor.objects.create(name="Repoint Corp Canonical", slug="repoint-corp-canon")
		SponsorAlias.objects.filter(key=key).update(sponsor=new_sponsor)

		trial.save()
		trial.refresh_from_db()

		self.assertEqual(trial.primary_sponsor_normalized_id, new_sponsor.pk)

	def test_update_fields_without_primary_sponsor_still_resolves_in_memory(self):
		trial = self._trial(7, primary_sponsor="Scoped Corp")
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(trial.primary_sponsor_normalized)

		trial.title = "Renamed"
		trial.save(update_fields=["title"])
		trial.refresh_from_db()
		# Scoped update did not include primary_sponsor -> the stale None FK is not
		# clobbered by the in-memory recompute, mirroring the other derived fields' rule.
		self.assertIsNone(trial.primary_sponsor_normalized)

	def test_update_fields_with_primary_sponsor_persists_resolution(self):
		trial = self._trial(8, primary_sponsor="First Name Corp")
		trial.primary_sponsor = "Renamed Corp"
		trial.save(update_fields=["primary_sponsor"])
		trial.refresh_from_db()
		self.assertEqual(trial.primary_sponsor_normalized.name, "Renamed Corp")

	def test_sponsor_type_derived_on_creation(self):
		trial = self._trial(9, primary_sponsor="Acme Foundation", sponsor_type=None)
		self.assertEqual(trial.primary_sponsor_normalized.sponsor_type, SponsorType.NONPROFIT)
		self.assertEqual(trial.primary_sponsor_normalized.sponsor_type_source, "rules")

	def test_curated_sponsor_type_never_overwritten(self):
		sponsor = Sponsor.objects.create(
			name="Curated Corp",
			slug="curated-corp",
			sponsor_type=SponsorType.NONPROFIT,
			sponsor_type_source="curated",
		)
		SponsorAlias.objects.create(
			sponsor=sponsor, key=normalize_sponsor_key("Curated Corp"), raw_sample="Curated Corp"
		)
		trial = self._trial(10, primary_sponsor="Curated Corp", lead_sponsor_class="INDUSTRY")
		sponsor.refresh_from_db()
		self.assertEqual(sponsor.sponsor_type, SponsorType.NONPROFIT)
		self.assertEqual(sponsor.sponsor_type_source, "curated")
		self.assertEqual(trial.primary_sponsor_normalized_id, sponsor.pk)


# --- _create_sponsor_for_key race/collision handling -----------------------------------


class CreateSponsorForKeyTests(TestCase):
	def test_integrity_error_race_falls_back_to_existing_alias(self):
		key = "race-corp"
		winner = Sponsor.objects.create(name="Race Corp", slug="race-corp")
		winner_alias = SponsorAlias.objects.create(
			sponsor=winner, key=key, raw_sample="Race Corp"
		)

		result = _create_sponsor_for_key(key, "Race Corp (concurrent variant)")

		self.assertEqual(result.pk, winner_alias.pk)
		self.assertEqual(result.sponsor_id, winner.pk)
		# No orphan sponsor left behind by the failed attempt.
		self.assertEqual(Sponsor.objects.filter(name__icontains="Race Corp").count(), 1)

	def test_name_collision_on_different_key_gets_suffixed(self):
		Sponsor.objects.create(name="Acme Corp", slug="acme-corp")
		key2 = "acme-corp-different-key"

		result = _create_sponsor_for_key(key2, "Acme Corp")

		self.assertEqual(result.key, key2)
		self.assertNotEqual(result.sponsor.name, "Acme Corp")
		self.assertTrue(result.sponsor.name.startswith("Acme Corp ("))


# --- Sponsor seed data guards ------------------------------------------------------------


class SponsorSeedGuardTests(TestCase):
	def test_no_variant_key_appears_in_two_families(self):
		seen: dict[str, str] = {}
		collisions = []
		for canonical, (_type, variants) in SPONSOR_SEEDS.items():
			for variant in variants:
				key = normalize_sponsor_key(variant)
				if key in seen and seen[key] != canonical:
					collisions.append((key, seen[key], canonical))
				seen.setdefault(key, canonical)
		self.assertEqual(collisions, [], f"Cross-family key collisions: {collisions}")

	def test_msd_and_merck_kgaa_are_distinct_families(self):
		self.assertIn("Merck Sharp & Dohme (MSD)", SPONSOR_SEEDS)
		self.assertIn("Merck KGaA", SPONSOR_SEEDS)
		msd_keys = {
			normalize_sponsor_key(v)
			for v in SPONSOR_SEEDS["Merck Sharp & Dohme (MSD)"][1]
		}
		kgaa_keys = {normalize_sponsor_key(v) for v in SPONSOR_SEEDS["Merck KGaA"][1]}
		self.assertEqual(msd_keys & kgaa_keys, set())

	def test_msdx_is_not_in_the_msd_family(self):
		msd_variants = {v.casefold() for v in SPONSOR_SEEDS["Merck Sharp & Dohme (MSD)"][1]}
		self.assertNotIn("msdx, inc.", msd_variants)

	def test_university_of_rochester_not_seeded_into_roche_family(self):
		roche_variants = {v.casefold() for v in SPONSOR_SEEDS["F. Hoffmann-La Roche"][1]}
		self.assertNotIn("university of rochester", roche_variants)


# --- Admin "Recompute normalized fields" action, sponsor resolution ---------------------


class TrialAdminRecomputeSponsorTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="sponsor-admin-root", email="root@example.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_resolves_stale_sponsor(self):
		trial = Trials.objects.create(
			title="Admin sponsor trial",
			link="https://example.com/admin-sponsor-1",
			primary_sponsor="Admin Test Corp",
		)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(trial.primary_sponsor_normalized)

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		self.assertIsNotNone(trial.primary_sponsor_normalized)
		self.assertEqual(trial.primary_sponsor_normalized.name, "Admin Test Corp")
