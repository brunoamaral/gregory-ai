"""Tests for gregory.utils.trial_field_normalizers.normalize_countries/normalize_regions,
the Trials.save() hook that keeps regions_normalized and the TrialCountry rows in
lockstep, the generalized backfill_trial_normalized_fields management command, and the
admin "Recompute normalized fields" action.

Mirrors the structure of test_trial_phase_normalization.py /
test_trial_recruitment_status_normalization.py. See
docs/TRIAL-COUNTRY-NORMALIZATION-PLAN.md for the design and
docs/trials-field-normalization.md for the shared machinery this extends.
"""

from io import StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from gregory.admin import TrialAdmin
from gregory.models import Trials, TrialCountry
from gregory.utils.registry_utils import merge_countries_by_source
from gregory.utils.trial_field_normalizers import (
	TrialRecruitmentStatus,
	TrialRegion,
	normalize_countries,
	normalize_regions,
)

User = get_user_model()


# --- normalize_countries: countries_by_source (CTGov ", "-joined lists) ----------------


def test_ctgov_style_comma_list():
	rows = normalize_countries({"ctgov": "France, United States"}, None, None, None)
	assert rows == [
		{
			"country": "FR",
			"status": None,
			"status_raw": None,
			"decision_date": None,
			"sources": ["ctgov"],
		},
		{
			"country": "US",
			"status": None,
			"status_raw": None,
			"decision_date": None,
			"sources": ["ctgov"],
		},
	]


def test_returns_none_when_every_input_empty():
	assert normalize_countries(None, None, None, None) is None
	assert normalize_countries({}, "", "", {}) is None


# --- normalize_countries: countries_by_source (WHO ";"-joined lists) -------------------


def test_ictrp_style_semicolon_list_with_duplicates_and_trailing_semicolon():
	rows = normalize_countries(
		{"ictrp": "France;Iran (Islamic Republic of);France;"}, None, None, None
	)
	codes = [row["country"] for row in rows]
	assert codes == ["FR", "IR"]  # duplicate collapsed, trailing ";" ignored


def test_who_reported_typos_and_uk_subdivisions_map_correctly():
	rows = normalize_countries(
		{
			"ictrp": "United Kindgdom;England;Scotland;Wales;Northern Ireland;Chian;Modalvia;Bosnial and Herzegovina"
		},
		None,
		None,
		None,
	)
	codes = sorted(row["country"] for row in rows)
	# GB collapses every UK-subdivision spelling into one row.
	assert codes == ["BA", "CN", "GB", "MD"]


def test_none_and_other_literals_are_dropped():
	rows = normalize_countries({"ictrp": "none;Other;France"}, None, None, None)
	assert [row["country"] for row in rows] == ["FR"]


def test_unmapped_value_is_logged_and_dropped(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		rows = normalize_countries(
			{"ictrp": "Some future registry country;France"}, None, None, None
		)
	assert [row["country"] for row in rows] == ["FR"]
	assert "Some future registry country" in caplog.text


# --- normalize_countries: single value stored alone (protects internal commas) --------


def test_single_value_with_internal_comma_is_not_split():
	"""'Korea, Republic of' stored alone must resolve to one country (KR), not be
	comma-split into 'Korea' + 'Republic of'."""
	rows = normalize_countries({"ictrp": "Korea, Republic of"}, None, None, None)
	assert [row["country"] for row in rows] == ["KR"]


def test_internal_comma_name_inside_a_longer_list_is_rejoined():
	rows = normalize_countries(
		{"ictrp": "France;Korea, Republic of;Germany"}, None, None, None
	)
	assert sorted(row["country"] for row in rows) == ["DE", "FR", "KR"]


# --- normalize_countries: legacy `countries` fallback (format detection) --------------


def test_legacy_countries_fallback_detects_semicolon_as_ictrp():
	rows = normalize_countries(None, "France;Finland;Spain;Germany;", None, None)
	assert sorted(row["country"] for row in rows) == ["DE", "ES", "FI", "FR"]
	assert all(row["sources"] == ["ictrp"] for row in rows)


def test_legacy_countries_fallback_detects_comma_as_ctgov():
	rows = normalize_countries(None, "France, United States", None, None)
	assert sorted(row["country"] for row in rows) == ["FR", "US"]
	assert all(row["sources"] == ["ctgov"] for row in rows)


def test_countries_by_source_present_skips_legacy_fallback():
	"""Once countries_by_source is seeded, the legacy `countries` column is not
	consulted again — it only serves as a one-time backfill source."""
	rows = normalize_countries({"ctgov": "France"}, "Germany;Spain", None, None)
	assert [row["country"] for row in rows] == ["FR"]


# --- normalize_countries: country_status (EU CTIS) -------------------------------------


def test_country_status_parses_comma_containing_status_values():
	rows = normalize_countries(
		None,
		None,
		"Spain:Authorised, recruitment pending, France:Authorised, recruitment pending, Italy:Ongoing, recruiting",
		None,
	)
	by_code = {row["country"]: row for row in rows}
	assert set(by_code) == {"ES", "FR", "IT"}
	assert by_code["ES"]["status_raw"] == "Authorised, recruitment pending"
	assert by_code["ES"]["status"] == TrialRecruitmentStatus.NOT_YET_RECRUITING
	assert by_code["IT"]["status_raw"] == "Ongoing, recruiting"
	assert by_code["IT"]["status"] == TrialRecruitmentStatus.RECRUITING
	assert all(row["sources"] == ["ctis"] for row in by_code.values())


# --- normalize_countries: countries_decision_date (EU CTIS) ----------------------------


def test_decision_date_keys_are_alpha2_and_attach_date_and_source():
	rows = normalize_countries(
		None, None, None, {"DE": "2024-07-19", "ES": "2024-07-22"}
	)
	by_code = {row["country"]: row for row in rows}
	assert by_code["DE"]["decision_date"] == "2024-07-19"
	assert by_code["DE"]["sources"] == ["ctis"]
	assert by_code["ES"]["decision_date"] == "2024-07-22"


def test_non_iso_decision_date_key_is_dropped(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		rows = normalize_countries(None, None, None, {"ZZ": "2024-01-01", "DE": "2024-07-19"})
	assert [row["country"] for row in rows] == ["DE"]
	assert "ZZ" in caplog.text


# --- normalize_countries: mixed-provenance union ---------------------------------------


def test_mixed_provenance_row_unions_sources_and_status():
	"""A country seen via a site location (ctgov) AND a CTIS regulatory decision must
	appear once, with both sources recorded and the CTIS status/decision_date attached."""
	rows = normalize_countries(
		{"ctgov": "Germany"},
		None,
		"Germany:Authorised, recruiting",
		{"DE": "2024-07-19"},
	)
	assert len(rows) == 1
	row = rows[0]
	assert row["country"] == "DE"
	assert row["status"] == TrialRecruitmentStatus.RECRUITING
	assert row["status_raw"] == "Authorised, recruiting"
	assert row["decision_date"] == "2024-07-19"
	assert row["sources"] == ["ctgov", "ctis"]


def test_result_is_sorted_by_country_code():
	rows = normalize_countries({"ctgov": "United States, France, Germany"}, None, None, None)
	assert [row["country"] for row in rows] == ["DE", "FR", "US"]


# --- normalize_regions ------------------------------------------------------------------


def test_normalize_regions_from_country_codes():
	assert normalize_regions(["DE", "FR"], None) == [TrialRegion.EUROPE]
	assert normalize_regions(["US"], None) == [TrialRegion.NORTH_AMERICA]
	assert normalize_regions(["DE", "US"], None) == sorted(
		[TrialRegion.EUROPE, TrialRegion.NORTH_AMERICA]
	)


def test_normalize_regions_from_literal_region_token():
	assert normalize_regions([], "Europe") == [TrialRegion.EUROPE]
	assert normalize_regions(None, "Asia(except Japan)") == [TrialRegion.ASIA]
	assert normalize_regions(None, "European Union") == [TrialRegion.EUROPE]


def test_normalize_regions_unions_codes_and_literal_tokens():
	assert normalize_regions(["US"], "Europe") == sorted(
		[TrialRegion.NORTH_AMERICA, TrialRegion.EUROPE]
	)


def test_normalize_regions_returns_none_when_empty():
	assert normalize_regions(None, None) is None
	assert normalize_regions([], "") is None


def test_normalize_regions_ignores_unmapped_country_code():
	# AQ (Antarctica) has no region bucket in this vocabulary.
	assert normalize_regions(["AQ"], None) is None


# --- registry_utils.merge_countries_by_source -------------------------------------------


def test_merge_countries_by_source_sets_new_key():
	assert merge_countries_by_source(None, "ctgov", "France") == {"ctgov": "France"}


def test_merge_countries_by_source_never_touches_other_keys():
	existing = {"ictrp": "France;Germany"}
	merged = merge_countries_by_source(existing, "ctgov", "France, United States")
	assert merged == {"ictrp": "France;Germany", "ctgov": "France, United States"}


def test_merge_countries_by_source_refreshes_value_unlike_merge_links():
	"""Unlike merge_links' first-value-wins semantics, a source's own key is always
	refreshed — a source's country list can legitimately change between syncs."""
	existing = {"ctgov": "France"}
	merged = merge_countries_by_source(existing, "ctgov", "France, Germany")
	assert merged == {"ctgov": "France, Germany"}


def test_merge_countries_by_source_empty_value_is_a_no_op():
	existing = {"ctgov": "France"}
	assert merge_countries_by_source(existing, "ctgov", None) == existing
	assert merge_countries_by_source(existing, "ctgov", "") == existing


# --- Trials.save() hook: regions_normalized + TrialCountry sync ------------------------


class TrialSaveHookCountryTests(TestCase):
	def test_create_computes_regions_normalized(self):
		trial = Trials.objects.create(
			title="Country hook trial",
			link="https://example.com/country-hook-1",
			countries_by_source={"ctgov": "France, Germany"},
		)
		assert trial.regions_normalized == [TrialRegion.EUROPE]

	def test_create_populates_trial_countries(self):
		trial = Trials.objects.create(
			title="Country hook trial 2",
			link="https://example.com/country-hook-2",
			countries_by_source={"ctgov": "France, United States"},
		)
		codes = sorted(tc.country.code for tc in trial.trial_countries.all())
		assert codes == ["FR", "US"]

	def test_changing_countries_and_saving_resyncs_trial_countries(self):
		trial = Trials.objects.create(
			title="Country hook trial 3",
			link="https://example.com/country-hook-3",
			countries_by_source={"ctgov": "France"},
		)
		assert [tc.country.code for tc in trial.trial_countries.all()] == ["FR"]

		trial.countries_by_source = {"ctgov": "Germany"}
		trial.save()

		codes = [tc.country.code for tc in trial.trial_countries.all()]
		assert codes == ["DE"]  # France row removed, Germany row added
		assert trial.regions_normalized == [TrialRegion.EUROPE]

	def test_trial_country_row_status_and_decision_date_update_in_place(self):
		trial = Trials.objects.create(
			title="Country hook trial 4",
			link="https://example.com/country-hook-4",
			country_status="Germany:Authorised, recruitment pending",
			countries_decision_date={"DE": "2024-01-01"},
		)
		row = trial.trial_countries.get(country="DE")
		assert row.status == TrialRecruitmentStatus.NOT_YET_RECRUITING
		assert row.decision_date.isoformat() == "2024-01-01"
		assert row.sources == ["ctis"]

		trial.country_status = "Germany:Ongoing, recruiting"
		trial.countries_decision_date = {"DE": "2024-02-02"}
		trial.save()

		row.refresh_from_db()
		assert row.status == TrialRecruitmentStatus.RECRUITING
		assert row.decision_date.isoformat() == "2024-02-02"

	def test_save_update_fields_countries_only_still_persists_regions_normalized(self):
		"""regions_normalized is derived from a tuple of raw fields (see
		raw_field_names()); saving with update_fields=["countries"] must still extend to
		persist regions_normalized, mirroring the single-field phase/recruitment_status
		update_fields shim."""
		trial = Trials.objects.create(
			title="Country hook trial 5",
			link="https://example.com/country-hook-5",
		)
		assert trial.regions_normalized is None

		trial.countries = "France;Germany"
		trial.save(update_fields=["countries"])

		trial.refresh_from_db()
		assert trial.regions_normalized == [TrialRegion.EUROPE]

	def test_no_countries_leaves_regions_normalized_none_and_no_trial_countries(self):
		trial = Trials.objects.create(
			title="No countries trial", link="https://example.com/country-hook-6"
		)
		assert trial.regions_normalized is None
		assert trial.trial_countries.count() == 0


# --- backfill_trial_normalized_fields command (regions/TrialCountry coverage) ----------


class BackfillTrialCountryTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_normalized_fields", stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, title, link, **fields):
		"""Create a trial, then blank regions_normalized and delete its TrialCountry rows
		via bulk-style writes to simulate a row written before these hooks existed."""
		trial = Trials.objects.create(title=title, link=link, **fields)
		Trials.objects.filter(pk=trial.pk).update(regions_normalized=None)
		TrialCountry.objects.filter(trial=trial).delete()
		trial.refresh_from_db()
		return trial

	def test_backfill_restores_regions_normalized(self):
		t1 = self._make_stale(
			"A", "https://example.com/country-bf-1", countries_by_source={"ctgov": "France"}
		)

		self.run_command(field="regions")

		t1.refresh_from_db()
		assert t1.regions_normalized == [TrialRegion.EUROPE]

	def test_backfill_rebuilds_trial_countries(self):
		t1 = self._make_stale(
			"A",
			"https://example.com/country-bf-2",
			countries_by_source={"ctgov": "France, United States"},
		)
		assert t1.trial_countries.count() == 0

		out, _ = self.run_command(field="regions")

		codes = sorted(tc.country.code for tc in t1.trial_countries.all())
		assert codes == ["FR", "US"]
		assert "Synced TrialCountry rows for" in out

	def test_dry_run_does_not_sync_trial_countries(self):
		t1 = self._make_stale(
			"A",
			"https://example.com/country-bf-3",
			countries_by_source={"ctgov": "France"},
		)

		out, _ = self.run_command(field="regions", dry_run=True)

		assert t1.trial_countries.count() == 0
		assert "were not synced" in out

	def test_idempotent_rerun_updates_nothing(self):
		Trials.objects.create(
			title="A",
			link="https://example.com/country-bf-4",
			countries_by_source={"ctgov": "France"},
		)

		out, _ = self.run_command(field="regions")

		self.assertIn("Updated 0 rows", out)

	def test_field_filter_scopes_to_regions_only(self):
		trial = self._make_stale(
			"A",
			"https://example.com/country-bf-field",
			phase="Phase III",
			countries_by_source={"ctgov": "France"},
		)
		Trials.objects.filter(pk=trial.pk).update(phase_normalized=None)
		trial.refresh_from_db()

		self.run_command(field="regions")

		trial.refresh_from_db()
		assert trial.regions_normalized == [TrialRegion.EUROPE]
		assert trial.phase_normalized is None  # untouched: not selected

	def test_default_scope_backfills_regions_alongside_phase_and_status(self):
		trial = self._make_stale(
			"A",
			"https://example.com/country-bf-default",
			phase="Phase III",
			recruitment_status="Recruiting",
			countries_by_source={"ctgov": "France"},
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()

		self.run_command()

		trial.refresh_from_db()
		assert trial.phase_normalized == "phase_3"
		assert trial.recruitment_status_normalized == "recruiting"
		assert trial.regions_normalized == [TrialRegion.EUROPE]
		assert [tc.country.code for tc in trial.trial_countries.all()] == ["FR"]


# --- Admin "Recompute normalized fields" action (TrialCountry coverage) ----------------


class TrialAdminRecomputeTrialCountriesTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="country-admin-root", email="country-root@example.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_rebuilds_trial_countries_and_regions_normalized(self):
		trial = Trials.objects.create(
			title="Admin country trial",
			link="https://example.com/country-admin-1",
			countries_by_source={"ctgov": "France, Germany"},
		)
		Trials.objects.filter(pk=trial.pk).update(regions_normalized=None)
		TrialCountry.objects.filter(trial=trial).delete()
		trial.refresh_from_db()
		assert trial.regions_normalized is None
		assert trial.trial_countries.count() == 0

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		assert trial.regions_normalized == [TrialRegion.EUROPE]
		codes = sorted(tc.country.code for tc in trial.trial_countries.all())
		assert codes == ["DE", "FR"]
