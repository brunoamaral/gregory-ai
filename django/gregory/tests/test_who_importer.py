"""
Tests for the WHO ICTRP XML importer (importWHOXML).

Verifies that results_url_link is captured from the XML and persists on
both create and update paths, that embedded HTML is cleaned at ingest, and
that each date is read in the convention its ICTRP field uses.

Run:
  docker exec gregory python manage.py test gregory.tests.test_who_importer
"""

import datetime
import os
import tempfile

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.management.commands.importWHOXML import Command as WHOCommand
from gregory.models import Sources, Subject, Team, Trials


_WHO_XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Trials_central>
  <Trial>
    <TrialID>{trial_id}</TrialID>
    <Public_title>Test Trial Results URL</Public_title>
    <Scientific_title>Scientific Test Trial</Scientific_title>
    <Primary_sponsor>Test Sponsor</Primary_sponsor>
    <Date_registration>2023-01-15</Date_registration>
    <web_address>https://trialsearch.who.int/Trial2.aspx?TrialID={trial_id}</web_address>
    <results_url_link>{results_url_link}</results_url_link>
    <results_yes_no>Yes</results_yes_no>
  </Trial>
</Trials_central>
"""


def _who_source():
	org = Organization.objects.create(name="WHO Test Org")
	team = Team.objects.create(
		organization=org, name="WHO Test Team", slug="who-test-team"
	)
	subject = Subject.objects.create(subject_name="WHO MS", subject_slug="who-ms")
	return Sources.objects.create(
		name="WHO ICTRP",
		source_for="trials",
		method="xml",
		subject=subject,
		team=team,
	)


def _run_import(xml_content, source_id):
	with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
		f.write(xml_content)
		path = f.name
	try:
		with open(os.devnull, "w") as devnull:
			cmd = WHOCommand()
			cmd.stdout = devnull
			cmd.parse_xml(path, source_id)
	finally:
		os.unlink(path)


class WHOResultsUrlLinkTest(TestCase):
	def setUp(self):
		self.source = _who_source()

	def test_create_stores_results_url_link(self):
		xml = _WHO_XML_TEMPLATE.format(
			trial_id="ISRCTN12345678",
			results_url_link="https://www.isrctn.com/ISRCTN12345678#results",
		)
		_run_import(xml, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn="ISRCTN12345678")
		self.assertEqual(
			t.results_url_link, "https://www.isrctn.com/ISRCTN12345678#results"
		)

	def test_update_fills_empty_results_url_link(self):
		xml_no_url = _WHO_XML_TEMPLATE.format(
			trial_id="ISRCTN11111111",
			results_url_link="",
		)
		_run_import(xml_no_url, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn="ISRCTN11111111")
		self.assertFalse(t.results_url_link)

		xml_with_url = _WHO_XML_TEMPLATE.format(
			trial_id="ISRCTN11111111",
			results_url_link="https://www.isrctn.com/ISRCTN11111111#results",
		)
		_run_import(xml_with_url, self.source.source_id)
		t.refresh_from_db()
		self.assertEqual(
			t.results_url_link, "https://www.isrctn.com/ISRCTN11111111#results"
		)

	def test_update_does_not_blank_results_url_link(self):
		xml_with_url = _WHO_XML_TEMPLATE.format(
			trial_id="ISRCTN22222222",
			results_url_link="https://www.isrctn.com/ISRCTN22222222#results",
		)
		_run_import(xml_with_url, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn="ISRCTN22222222")
		self.assertEqual(
			t.results_url_link, "https://www.isrctn.com/ISRCTN22222222#results"
		)

		xml_no_url = _WHO_XML_TEMPLATE.format(
			trial_id="ISRCTN22222222",
			results_url_link="",
		)
		_run_import(xml_no_url, self.source.source_id)
		t.refresh_from_db()
		self.assertEqual(
			t.results_url_link, "https://www.isrctn.com/ISRCTN22222222#results"
		)


_WHO_XML_HTML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Trials_central>
  <Trial>
    <TrialID>{trial_id}</TrialID>
    <Public_title>Test Trial HTML Cleaning</Public_title>
    <Scientific_title>Scientific Test Trial</Scientific_title>
    <Primary_sponsor>Test Sponsor</Primary_sponsor>
    <Date_registration>2023-01-15</Date_registration>
    <web_address>https://trialsearch.who.int/Trial2.aspx?TrialID={trial_id}</web_address>
    <Inclusion_Criteria>{inclusion_criteria}</Inclusion_Criteria>
    <Inclusion_gender>{inclusion_gender}</Inclusion_gender>
  </Trial>
</Trials_central>
"""


class WHOHTMLCleaningTest(TestCase):
	def setUp(self):
		self.source = _who_source()

	def test_html_markup_is_stripped_at_ingest(self):
		# Real WHO ICTRP XML entity-encodes embedded HTML (&lt;br&gt;), since a raw
		# unescaped <br> would be parsed as a child element, not text content.
		xml = _WHO_XML_HTML_TEMPLATE.format(
			trial_id="ISRCTN33333333",
			inclusion_criteria="Age &gt;18&lt;br&gt;Confirmed diagnosis&lt;br&gt;",
			inclusion_gender="&lt;br&gt;Female: yes&lt;br&gt;Male: yes&lt;br&gt;",
		)
		_run_import(xml, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn="ISRCTN33333333")
		self.assertEqual(t.inclusion_criteria, "Age >18 Confirmed diagnosis")
		self.assertEqual(t.inclusion_gender, "Female: yes Male: yes")
		self.assertNotIn("<br>", t.inclusion_criteria)
		self.assertNotIn("<br>", t.inclusion_gender)

	def test_angle_bracket_quotation_marks_are_preserved(self):
		xml = _WHO_XML_HTML_TEMPLATE.format(
			trial_id="ISRCTN44444444",
			inclusion_criteria=(
				"Diagnosis with reference to diagnostic criteria "
				"&lt;the guide of diagnosis and treatment&gt; required"
			),
			inclusion_gender="Both",
		)
		_run_import(xml, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn="ISRCTN44444444")
		self.assertEqual(
			t.inclusion_criteria,
			"Diagnosis with reference to diagnostic criteria "
			"<the guide of diagnosis and treatment> required",
		)


# Dates as ICTRP exported NCT07758270 on 2026-09-18: Export_date is
# month-first, Date_registration day-first beside its yyyymmdd twin
# Date_registration3, and the rest day-first, year-first or textual.
_WHO_XML_DATES_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Trials_central>
  <Trial>
    <Export_date>{export_date}</Export_date>
    <TrialID>{trial_id}</TrialID>
    <Last_Refreshed_on>{last_refreshed_on}</Last_Refreshed_on>
    <Public_title>Test Trial Dates</Public_title>
    <Date_registration3>{date_registration3}</Date_registration3>
    <Date_registration>{date_registration}</Date_registration>
    <web_address>https://clinicaltrials.gov/study/{trial_id}</web_address>
    <Date_enrollement>{date_enrollement}</Date_enrollement>
    <Ethics_review_approval_date>{ethics_review_approval_date}</Ethics_review_approval_date>
    <results_date_completed>{results_date_completed}</results_date_completed>
  </Trial>
</Trials_central>
"""

_DATES_TRIAL_ID = "NCT07758270"


def _dates_xml(**fields):
	values = {
		"trial_id": _DATES_TRIAL_ID,
		"export_date": "09/18/2026 10:42:00",
		"last_refreshed_on": "24 August 2026",
		"date_registration3": "20260805",
		"date_registration": "05/08/2026",
		"date_enrollement": "August 15, 2026",
		"ethics_review_approval_date": "",
		"results_date_completed": "",
	}
	values.update(fields)
	return _WHO_XML_DATES_TEMPLATE.format(**values)


class WHODateConventionsTest(TestCase):
	"""
	Reading every ICTRP date month-first swapped day and month whenever the
	day was 12 or lower, so a trial also fed by ClinicalTrials.gov flipped
	between the two dates on every import.
	"""

	def setUp(self):
		self.source = _who_source()

	def _import(self, **fields):
		_run_import(_dates_xml(**fields), self.source.source_id)
		return Trials.objects.get(identifiers__nct=_DATES_TRIAL_ID)

	def _existing_trial(self, date_registration):
		return Trials.objects.create(
			title="Test Trial Dates",
			link=f"https://clinicaltrials.gov/study/{_DATES_TRIAL_ID}",
			identifiers={"nct": _DATES_TRIAL_ID},
			date_registration=date_registration,
		)

	def test_create_reads_registration_date_as_day_first(self):
		t = self._import()
		self.assertEqual(t.date_registration, datetime.date(2026, 8, 5))
		self.assertEqual(t.published_date.date(), datetime.date(2026, 8, 5))

	def test_update_corrects_a_swapped_registration_date(self):
		# How the month-first parser left the row: 05/08/2026 as 8 May.
		t = self._existing_trial(datetime.date(2026, 5, 8))
		self._import()
		t.refresh_from_db()
		self.assertEqual(t.date_registration, datetime.date(2026, 8, 5))
		self.assertEqual(t.published_date.date(), datetime.date(2026, 8, 5))

	def test_update_keeps_the_date_clinicaltrials_gov_stored(self):
		# ClinicalTrials.gov stores its first-submitted date, 2026-08-05. The
		# WHO import now agrees with it instead of flipping it to 8 May.
		t = self._existing_trial(datetime.date(2026, 8, 5))
		self._import()
		self.assertIn(self.source, t.sources.all())
		self.assertEqual(
			set(t.history.values_list("date_registration", flat=True)),
			{datetime.date(2026, 8, 5)},
		)

	def test_date_registration_is_the_day_first_fallback(self):
		t = self._import(date_registration3="")
		self.assertEqual(t.date_registration, datetime.date(2026, 8, 5))

	def test_malformed_date_registration3_falls_back(self):
		t = self._import(date_registration3="2026-08-05")
		self.assertEqual(t.date_registration, datetime.date(2026, 8, 5))

	def test_year_first_date_registration_is_not_swapped(self):
		# NL-OMON and ChiCTR records carry a year-first Date_registration.
		t = self._import(date_registration3="", date_registration="2022-12-05")
		self.assertEqual(t.date_registration, datetime.date(2022, 12, 5))

	def test_export_date_stays_month_first(self):
		t = self._import(export_date="09/05/2026 10:42:00")
		self.assertEqual(t.export_date.date(), datetime.date(2026, 9, 5))

	def test_numeric_registry_dates_are_day_first(self):
		t = self._import(
			date_enrollement="08/07/2026",
			ethics_review_approval_date="07/10/2023",
			results_date_completed="03/02/2025",
		)
		self.assertEqual(t.date_enrollement, datetime.date(2026, 7, 8))
		self.assertEqual(t.ethics_review_approval_date, datetime.date(2023, 10, 7))
		self.assertEqual(t.results_date_completed, datetime.date(2025, 2, 3))

	def test_year_first_registry_dates_are_not_swapped(self):
		# ChiCTR, IRCT and NL-OMON write "2020-04-10"; JPRN (UMIN) "2021/07/05".
		t = self._import(
			date_enrollement="2020-04-10",
			results_date_completed="2021/07/05",
		)
		self.assertEqual(t.date_enrollement, datetime.date(2020, 4, 10))
		self.assertEqual(t.results_date_completed, datetime.date(2021, 7, 5))

	def test_textual_dates_still_parse(self):
		t = self._import(
			date_enrollement="August 15, 2026",
			last_refreshed_on="3 August 2026",
		)
		self.assertEqual(t.date_enrollement, datetime.date(2026, 8, 15))
		self.assertEqual(t.last_refreshed_on, datetime.date(2026, 8, 3))
