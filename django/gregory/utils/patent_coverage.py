"""What a patent family covers, read from its CPC classes.

A heuristic for spotting patents on new treatments. Each family lands in exactly one
bucket, tried in order:

- new_substance: a therapy class (A61P, or a medicinal-preparation group of A61K) plus a
  class for a new compound, biologic, cell or gene therapy (C07*, C12N 5/15, A61K 35/48).
  Usually a new active substance.
- new_use: a therapy class without such a class. Usually a known substance in a new use,
  dose, combination or formulation (repurposing).
- device: no therapy class, but a therapy-device class (electro/magnetic therapy, physical
  therapy apparatus, drug-delivery devices).
- diagnostic: none of the above, but a diagnosis, testing or health-data class.
- other: anything else, including families with no CPC classes.

``classify`` (Python, for display) and ``coverage_q`` (SQL, for filtering) read the same
prefix lists, and a test checks they agree.
"""

from django.db.models import F, Func, Q, TextField, Value
from django.db.models.functions import Concat

# A61P is the therapeutic activity. Medicinal-preparation groups of A61K count too, since
# recent filings often carry them before an A61P code is assigned; A61K 8 (cosmetics) and
# A61K 49/51 (in-vivo diagnostic and contrast agents) are left out.
THERAPY_PREFIXES = (
	"A61P",
	"A61K9/",
	"A61K31/",
	"A61K33/",
	"A61K35/",
	"A61K36/",
	"A61K38/",
	"A61K39/",
	"A61K40/",
	"A61K41/",
	"A61K45/",
	"A61K47/",
	"A61K48/",
)
SUBSTANCE_PREFIXES = (
	"C07C",
	"C07D",
	"C07F",
	"C07H",
	"C07J",
	"C07K",
	"C12N5/",
	"C12N15/",
	"A61K35/",
	"A61K48/",
)
DEVICE_PREFIXES = ("A61N", "A61H", "A61M")
DIAGNOSTIC_PREFIXES = ("G01N", "C12Q", "A61B", "G16H", "G16B")

NEW_SUBSTANCE = "new_substance"
NEW_USE = "new_use"
DEVICE = "device"
DIAGNOSTIC = "diagnostic"
OTHER = "other"

COVERAGE_CHOICES = [
	(NEW_SUBSTANCE, "New substance (compound, biologic, cell or gene therapy)"),
	(NEW_USE, "New use, dose or formulation"),
	(DEVICE, "Therapy device"),
	(DIAGNOSTIC, "Diagnostic, test or monitoring"),
	(OTHER, "Other / no classification"),
]
COVERAGE_LABELS = dict(COVERAGE_CHOICES)

# Short labels for the changelist column.
COVERAGE_SHORT_LABELS = {
	NEW_SUBSTANCE: "New substance",
	NEW_USE: "New use / formulation",
	DEVICE: "Therapy device",
	DIAGNOSTIC: "Diagnostic",
	OTHER: "Other",
}

# Name of the alias coverage_q filters on; see with_cpc_text.
CPC_TEXT = "cpc_text"


def _has(cpc_classes, prefixes):
	return any(c.startswith(prefixes) for c in cpc_classes)


def classify(cpc_classes):
	"""Return the coverage bucket of a family's CPC classes."""
	cpc = cpc_classes or []
	if _has(cpc, THERAPY_PREFIXES):
		return NEW_SUBSTANCE if _has(cpc, SUBSTANCE_PREFIXES) else NEW_USE
	if _has(cpc, DEVICE_PREFIXES):
		return DEVICE
	if _has(cpc, DIAGNOSTIC_PREFIXES):
		return DIAGNOSTIC
	return OTHER


def with_cpc_text(queryset):
	"""Alias the CPC classes as one "|"-joined string with a leading "|", so a prefix
	test is a substring test for "|<prefix>"."""
	return queryset.alias(
		**{
			CPC_TEXT: Concat(
				Value("|"),
				Func(
					F("cpc_classes"),
					Value("|"),
					function="array_to_string",
					output_field=TextField(),
				),
				output_field=TextField(),
			)
		}
	)


def _has_q(prefixes):
	q = Q()
	for prefix in prefixes:
		q |= Q(**{f"{CPC_TEXT}__contains": f"|{prefix}"})
	return q


def coverage_q(bucket):
	"""Q for one coverage bucket. Apply to a queryset passed through with_cpc_text."""
	therapy = _has_q(THERAPY_PREFIXES)
	substance = _has_q(SUBSTANCE_PREFIXES)
	device = _has_q(DEVICE_PREFIXES)
	diagnostic = _has_q(DIAGNOSTIC_PREFIXES)
	if bucket == NEW_SUBSTANCE:
		return therapy & substance
	if bucket == NEW_USE:
		return therapy & ~substance
	if bucket == DEVICE:
		return ~therapy & device
	if bucket == DIAGNOSTIC:
		return ~therapy & ~device & diagnostic
	if bucket == OTHER:
		return ~therapy & ~device & ~diagnostic
	raise ValueError(f"Unknown coverage bucket: {bucket!r}")
