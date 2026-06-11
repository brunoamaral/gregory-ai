"""
Tests for the WHO ICTRP XML importer (importWHOXML).

Verifies that results_url_link is captured from the XML and persists on
both create and update paths.

Run:
  docker exec gregory python manage.py test gregory.tests.test_who_importer
"""
import os
import tempfile

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
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
	org = Organization.objects.create(name='WHO Test Org')
	team = Team.objects.create(organization=org, name='WHO Test Team', slug='who-test-team')
	subject = Subject.objects.create(subject_name='WHO MS', subject_slug='who-ms')
	return Sources.objects.create(
		name='WHO ICTRP',
		source_for='trials',
		method='xml',
		subject=subject,
		team=team,
	)


def _run_import(xml_content, source_id):
	with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
		f.write(xml_content)
		path = f.name
	try:
		with open(os.devnull, 'w') as devnull:
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
			trial_id='ISRCTN12345678',
			results_url_link='https://www.isrctn.com/ISRCTN12345678#results',
		)
		_run_import(xml, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn='ISRCTN12345678')
		self.assertEqual(t.results_url_link, 'https://www.isrctn.com/ISRCTN12345678#results')

	def test_update_fills_empty_results_url_link(self):
		xml_no_url = _WHO_XML_TEMPLATE.format(
			trial_id='ISRCTN11111111',
			results_url_link='',
		)
		_run_import(xml_no_url, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn='ISRCTN11111111')
		self.assertFalse(t.results_url_link)

		xml_with_url = _WHO_XML_TEMPLATE.format(
			trial_id='ISRCTN11111111',
			results_url_link='https://www.isrctn.com/ISRCTN11111111#results',
		)
		_run_import(xml_with_url, self.source.source_id)
		t.refresh_from_db()
		self.assertEqual(t.results_url_link, 'https://www.isrctn.com/ISRCTN11111111#results')

	def test_update_does_not_blank_results_url_link(self):
		xml_with_url = _WHO_XML_TEMPLATE.format(
			trial_id='ISRCTN22222222',
			results_url_link='https://www.isrctn.com/ISRCTN22222222#results',
		)
		_run_import(xml_with_url, self.source.source_id)
		t = Trials.objects.get(identifiers__isrctn='ISRCTN22222222')
		self.assertEqual(t.results_url_link, 'https://www.isrctn.com/ISRCTN22222222#results')

		xml_no_url = _WHO_XML_TEMPLATE.format(
			trial_id='ISRCTN22222222',
			results_url_link='',
		)
		_run_import(xml_no_url, self.source.source_id)
		t.refresh_from_db()
		self.assertEqual(t.results_url_link, 'https://www.isrctn.com/ISRCTN22222222#results')
