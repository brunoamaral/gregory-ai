"""
Reading labels from DailyMed, the National Library of Medicine's collection of FDA drug
labels (SPL documents). Pure functions with no database access: ``sync_fda_labels`` and
the tests share one reading of a response, and the tests mock the two download functions.

Two calls are enough:

* ``/services/v2/spls.json?application_number=NDA022561`` lists the labels filed under an
  application: ``setid``, ``spl_version``, ``title`` and ``published_date``. An application
  can have up to ten labels (repackagers, authorised generics).
* ``/services/v2/spls/<setid>.xml`` is one label, about 240 KB. Its indications are the
  section coded LOINC 34067-9.

No key, no documented rate limit: the downloads send the importers' User-Agent and pause
briefly between requests. The label XML comes from outside, so it is parsed with
``defusedxml``, never the plain ``xml.etree`` parser.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from xml.etree.ElementTree import ParseError  # the exception only; parsing is defusedxml's

import requests
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from gregory.utils.regulator_http import USER_AGENT, get_with_retries

logger = logging.getLogger(__name__)

BASE_URL = "https://dailymed.nlm.nih.gov/dailymed"
LIST_URL = BASE_URL + "/services/v2/spls.json"
LABEL_URL = BASE_URL + "/services/v2/spls/{setid}.xml"
LABEL_PAGE_URL = BASE_URL + "/drugInfo.cfm?setid={setid}"

PAGE_SIZE = 100
MAX_PAGES = 5  # an application has at most ten labels; this only guards against a loop
REQUEST_PAUSE = 0.2

HL7_NAMESPACE = "urn:hl7-org:v3"
INDICATIONS_CODE = "34067-9"

_WHITESPACE = re.compile(r"\s+")


class DailyMedError(Exception):
	"""DailyMed could not be read (a network problem, an error status, a bad response)."""


@dataclass(frozen=True)
class DailyMedLabel:
	"""One entry of the list for an application."""

	setid: str
	version: str
	title: str
	published: str  # ISO date, or the text DailyMed sent when it was not a date


def parse_published(value):
	"""DailyMed's ``"Jan 08, 2025"`` as an ISO date string. Anything else comes back as it
	was sent (an unreadable date must not lose the label)."""
	text = (value or "").strip()
	try:
		return datetime.strptime(text, "%b %d, %Y").date().isoformat()
	except ValueError:
		if text:
			logger.warning("Unreadable DailyMed date %r", text)
		return text


def label_page_url(setid):
	"""The label's page on dailymed.nlm.nih.gov."""
	return LABEL_PAGE_URL.format(setid=setid)


def parse_label_list(payload):
	"""The labels in one page of the ``spls.json`` response, in DailyMed's order.

	An entry without a ``setid`` is dropped. Raises ``DailyMedError`` when ``payload`` is
	not the shape DailyMed documents."""
	if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
		raise DailyMedError("The DailyMed list has no 'data' array.")
	labels = []
	for item in payload["data"]:
		if not isinstance(item, dict) or not item.get("setid"):
			continue
		labels.append(
			DailyMedLabel(
				setid=str(item["setid"]),
				version=str(item.get("spl_version", "")),
				title=_WHITESPACE.sub(" ", str(item.get("title") or "")).strip(),
				published=parse_published(item.get("published_date")),
			)
		)
	return labels


def parse_indications(xml):
	"""The text of a label's "Indications and usage" section (LOINC 34067-9), whitespace
	collapsed, or ``""`` when the label has none. ``xml`` is bytes or text.

	Raises ``DailyMedError`` when the document is not parseable XML."""
	try:
		root = ElementTree.fromstring(xml)
	except (ParseError, DefusedXmlException) as exc:
		raise DailyMedError(f"The label is not valid XML: {exc}")
	section_tag = "{%s}section" % HL7_NAMESPACE
	code_tag = "{%s}code" % HL7_NAMESPACE
	for section in root.iter(section_tag):
		code = section.find(code_tag)
		if code is not None and code.get("code") == INDICATIONS_CODE:
			return _WHITESPACE.sub(" ", " ".join(section.itertext())).strip()
	return ""


# --- downloading ---------------------------------------------------------


def _get(url, params=None):
	"""One GET, paused first so a run of them stays polite. ``DailyMedError`` on a network
	failure; the caller judges the status."""
	time.sleep(REQUEST_PAUSE)
	label = "DailyMed " + url
	if params:
		url = requests.Request("GET", url, params=params).prepare().url
	try:
		return get_with_retries(url, {"User-Agent": USER_AGENT}, label)
	except requests.RequestException as exc:
		raise DailyMedError(str(exc))


def list_labels(application_number):
	"""Every label DailyMed holds under an application (``NDA022561``, ``BLA125326``), as
	``DailyMedLabel`` entries. An application with none gives ``[]``. Raises
	``DailyMedError`` on any failure."""
	labels = []
	for page in range(1, MAX_PAGES + 1):
		response = _get(
			LIST_URL,
			{"application_number": application_number, "pagesize": PAGE_SIZE, "page": page},
		)
		if response.status_code == 404:
			return labels
		if response.status_code != 200:
			raise DailyMedError(f"DailyMed returned HTTP {response.status_code} for {application_number}.")
		try:
			payload = response.json()
		except ValueError as exc:
			raise DailyMedError(f"DailyMed sent no JSON for {application_number}: {exc}")
		labels.extend(parse_label_list(payload))
		pages = ((payload or {}).get("metadata") or {}).get("total_pages")
		if not isinstance(pages, int) or page >= pages:
			break
	return labels


def fetch_indications(setid):
	"""The indications text of one label, downloading its XML. ``""`` when the label has no
	indications section. Raises ``DailyMedError`` on any failure."""
	response = _get(LABEL_URL.format(setid=setid))
	if response.status_code != 200:
		raise DailyMedError(f"DailyMed returned HTTP {response.status_code} for label {setid}.")
	return parse_indications(response.content)
