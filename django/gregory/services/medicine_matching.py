"""
The rule that decides which medicines belong to a team category.

Pure functions, no database access: ``match_medicines`` loads the rows and calls these.
The rule, per category:

1. Patterns. ``medicine_terms`` when set. Otherwise ``category_terms``, but only for the
   intervention modalities (small molecule, antibody / biologic, cell / gene therapy,
   natural product). Any other category gets no automatic links.
2. A pattern matches a product when it matches one of the product's ``substances`` or its
   ``name``. Without ``*`` a substance matches when it equals the pattern or is the
   pattern plus one more word (``pixantrone`` matches ``pixantrone dimaleate``). With
   ``*`` the pattern must match the whole substance, and ``*`` stands for any run of
   characters. A name must equal the pattern (or fit it, with ``*``): no extra word. An
   FDA row has one name per brand sold under its application, so a pattern may match any
   of ``brand_names`` as well as ``name``.
3. Combination products are skipped unless the category opts in.
4. ``category_exclude_terms`` rules a product out when one appears as a whole word in a
   substance or in the name.
5. Disease check. Steps 1 to 4 are ``pattern_match``; this step depends on the regulator.
   - EMA: ``areas`` is the union of ``ema_therapeutic_areas`` over the category's
     subjects. When it is empty the check is skipped; otherwise one of the product's
     therapeutic areas must start with one of them, ignoring case.
   - FDA: the FDA publishes no disease areas, so the check reads the labels. ``terms`` is
     the union of ``fda_indication_terms`` over the category's subjects. When it is empty
     the check is skipped; otherwise one term must appear as a whole word, ignoring case,
     in the indications of any of the application's DailyMed labels (not just the one
     shown, which is what keeps Kesimpta linked through its shared application with
     Arzerra). A medicine with no label gets no link.
6. Diagnostics never match a category: an EMA product whose ATC code starts V04, V08 or
   V09, an FDA row with an openFDA pharmacologic class containing "diagnostic" or
   "contrast".

The same module holds the two other rules ``match_medicines`` applies:

- ``subject_match``: a subject's own EMA areas against a product, with no category. It
  uses the same prefix rule as the disease check above, and skips diagnostics. An FDA row
  never gets a subject link (it would need a label for every application): it reaches a
  subject only through a category.
- ``group_key`` and ``group_lead``: products of one regulator that share their substances
  and their indication are copies of one medicine; one leads and the others point at it.
  Grouping across regulators is left to the frontend.
"""

import re
from functools import lru_cache

from gregory.models import CategoryModality, Regulator

# Only these modalities match on their ordinary category terms. A research topic such as
# "BTK inhibitors" or "Stem cell research" names no product, so it gets medicines only
# through medicine_terms.
INTERVENTION_MODALITIES = frozenset(
	{
		CategoryModality.SMALL_MOLECULE,
		CategoryModality.BIOLOGIC_ANTIBODY,
		CategoryModality.CELL_GENE_THERAPY,
		CategoryModality.NATURAL_PRODUCT,
	}
)

# matched_on is a CharField(255).
MATCHED_ON_MAX = 255

# ATC groups of diagnostics: V04 diagnostic agents, V08 contrast media, V09 diagnostic
# radiopharmaceuticals. We don't track diagnostics.
DIAGNOSTIC_ATC_PREFIXES = ("V04", "V08", "V09")

# An FDA row has no ATC code; its openFDA established pharmacologic classes say it
# ("Radiographic Contrast Agent [EPC]", "Radioactive Diagnostic Agent [EPC]").
DIAGNOSTIC_FDA_CLASS_WORDS = ("diagnostic", "contrast")

# Everything that is not a letter, digit or underscore: replaced by a space when comparing
# indications, so "contra-indication" and "contra indication" read the same.
_PUNCTUATION = re.compile(r"[^\w\s]")


def normalise(text):
	"""Lowercase and collapse whitespace, the form substances are stored in."""
	return " ".join((text or "").lower().split())


def is_diagnostic(medicine):
	"""Whether ``medicine`` is a diagnostic.

	EMA: the ATC code starts with V04, V08 or V09; a product with no ATC code yet (new
	products often have none) isn't caught. FDA: one of its pharmacologic classes contains
	"diagnostic" or "contrast"; a row with no openFDA block (about 3,700 applications)
	isn't caught."""
	# getattr: a bare stand-in with only an ATC code is an EMA product.
	if getattr(medicine, "regulator", Regulator.EMA) == Regulator.FDA:
		return any(
			word in (pharm_class or "").lower()
			for pharm_class in medicine.fda_pharm_classes or []
			for word in DIAGNOSTIC_FDA_CLASS_WORDS
		)
	return (medicine.atc_code or "").upper().startswith(DIAGNOSTIC_ATC_PREFIXES)


def category_patterns(category):
	"""The normalised substance patterns that apply to ``category``, in order, without
	duplicates or blanks. Empty when the category gets no automatic medicines."""
	if category.medicine_terms:
		raw = category.medicine_terms
	elif category.modality in INTERVENTION_MODALITIES:
		raw = category.category_terms
	else:
		return []
	patterns = []
	for term in raw or []:
		pattern = normalise(term)
		if pattern and pattern not in patterns:
			patterns.append(pattern)
	return patterns


@lru_cache(maxsize=2048)
def _wildcard_regex(pattern):
	return re.compile("^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$", re.DOTALL)


def substance_matches(pattern, substance):
	"""Whether ``pattern`` matches ``substance`` (see the module docstring)."""
	pattern = normalise(pattern)
	substance = normalise(substance)
	if not pattern or not substance:
		return False
	if "*" in pattern:
		return bool(_wildcard_regex(pattern).match(substance))
	if substance == pattern:
		return True
	# The pattern plus exactly one more word: a salt, as in "pixantrone dimaleate".
	if substance.startswith(pattern + " "):
		return " " not in substance[len(pattern) + 1 :]
	return False


def _name_matches(pattern, name):
	name = normalise(name)
	if "*" in pattern:
		return bool(_wildcard_regex(pattern).match(name))
	return name == pattern


@lru_cache(maxsize=2048)
def _exclusion_regex(term):
	# Whole-word containment, as rebuild_categories does for articles; ``*`` is any run of
	# non-space characters.
	body = r"\S*".join(re.escape(part) for part in term.split("*"))
	return re.compile(r"\b" + body + r"\b")


def _is_excluded(category, medicine):
	texts = [normalise(s) for s in medicine.substances]
	texts.append(normalise(medicine.name))
	for term in category.category_exclude_terms or []:
		term = normalise(term)
		if not term:
			continue
		regex = _exclusion_regex(term)
		if any(regex.search(text) for text in texts):
			return True
	return False


def _matching_area(areas, medicine):
	"""The first of ``areas`` that one of the product's therapeutic areas starts with."""
	product_areas = [normalise(a) for a in medicine.therapeutic_areas]
	for area in areas:
		needle = normalise(area)
		if needle and any(p.startswith(needle) for p in product_areas):
			return area
	return None


def _names(medicine):
	"""The names a pattern may match: the product's name, and for an FDA row each brand
	sold under the application (``name`` is the brands joined, which no pattern equals)."""
	if medicine.regulator == Regulator.FDA:
		return [medicine.name, *(medicine.brand_names or [])]
	return [medicine.name]


def pattern_match(category, medicine):
	"""The pattern that puts ``medicine`` in ``category``, or ``None`` if it doesn't.

	Steps 1 to 4 of the rule and the diagnostic exclusion: patterns, combinations and
	exclusions, with no disease check. ``sync_fda_labels`` uses it to find the FDA
	medicines whose labels are worth downloading."""
	if is_diagnostic(medicine):
		return None
	patterns = category_patterns(category)
	if not patterns:
		return None
	if (medicine.is_combination or len(medicine.substances) > 1) and not (
		category.medicine_include_combinations
	):
		return None
	names = _names(medicine)
	matched = next(
		(
			pattern
			for pattern in patterns
			if any(substance_matches(pattern, s) for s in medicine.substances)
			or any(_name_matches(pattern, name) for name in names)
		),
		None,
	)
	if matched is None:
		return None
	if _is_excluded(category, medicine):
		return None
	return matched


@lru_cache(maxsize=512)
def _term_regex(term):
	# Whole words: no word character on either side. Look-arounds rather than a word
	# boundary so a term that starts or ends with punctuation still works. "alzheimer"
	# matches "Alzheimer's" and "Alzheimer" + a curly apostrophe (both are non-word
	# characters), but "parkinson" does not match "parkinsonism".
	body = r"\s+".join(re.escape(word) for word in term.split())
	return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


def _matching_term(terms, medicine):
	"""The first of ``terms`` found as a whole word in the indications of any of the
	medicine's labels."""
	texts = [
		entry.get("indication") or ""
		for entry in medicine.label_indications or []
		if isinstance(entry, dict)
	]
	for term in terms:
		needle = normalise(term)
		if needle and any(_term_regex(needle).search(text) for text in texts):
			return term.strip()
	return None


def product_matches(category, medicine, areas, terms=()):
	"""The label for why ``medicine`` belongs in ``category``, or ``None`` if it doesn't.

	``areas`` is the union of the category's subjects' ``ema_therapeutic_areas`` and
	``terms`` the union of their ``fda_indication_terms``; each applies to its own
	regulator's rows, and an empty list skips the disease check. The label is
	``"<pattern> · <area or term>"``, or just ``"<pattern>"`` when the check was skipped.
	It is what ``matched_on`` stores, so an editor can see why a link exists.
	"""
	matched = pattern_match(category, medicine)
	if matched is None:
		return None
	if medicine.regulator == Regulator.FDA:
		if not terms:
			return matched[:MATCHED_ON_MAX]
		term = _matching_term(terms, medicine)
		if term is None:
			return None
		return f"{matched} · {term}"[:MATCHED_ON_MAX]
	if not areas:
		return matched[:MATCHED_ON_MAX]
	area = _matching_area(areas, medicine)
	if area is None:
		return None
	return f"{matched} · {area}"[:MATCHED_ON_MAX]


def subject_match(areas, medicine):
	"""The subject area that ``medicine`` was approved for, or ``None`` if it wasn't.

	``areas`` is the subject's ``ema_therapeutic_areas``. Same prefix rule as the disease
	check of a category, with no pattern: any product approved for the area matches, except
	a diagnostic. An empty ``areas`` matches nothing, since a subject without areas is a
	research area, not a condition. The area is what ``matched_on`` stores.

	An FDA row never matches: its disease check reads labels, and fetching a label for
	every active application is too costly for now.
	"""
	if medicine.regulator != Regulator.EMA or not areas or is_diagnostic(medicine):
		return None
	area = _matching_area(areas, medicine)
	if area is None:
		return None
	return area.strip()[:MATCHED_ON_MAX]


def _indication_text(medicine):
	"""The product's indication in a comparable form: lowercase, the product's own name
	removed (indications name their product: "Exelon is indicated for ..."), punctuation
	turned into spaces and whitespace collapsed. An FDA row's labels name each brand, so
	every brand is removed too."""
	text = normalise(medicine.indication)
	names = {normalise(medicine.name)}
	if medicine.regulator == Regulator.FDA:
		names.update(normalise(brand) for brand in medicine.brand_names or [])
	# Longest first: "ocrevus zunovo" before "ocrevus".
	for name in sorted(filter(None, names), key=len, reverse=True):
		text = text.replace(name, " ")
	return " ".join(_PUNCTUATION.sub(" ", text).split())


def group_key(medicine):
	"""The key products must share to be copies of one medicine, or ``None`` when the
	product is never grouped.

	The key is the regulator, the sorted substances and the cleaned indication. Substances
	alone would merge different medicines (Avonex and Rebif, Tecfidera and Skilarence); the
	same substances with the same indication are one medicine sold under several names. The
	regulator is part of the key because the two regulators' products are never copies of
	each other here (the frontend groups across them). On an FDA row the indication is the
	main label's, so in practice only rows with a label can group.
	Generics and biosimilars keep their own flags and aren't grouped, nor is a product that
	was never authorised (a refusal or a withdrawn application is its own news), nor one
	with no indication, since an empty text would group unrelated products.
	"""
	if medicine.generic or medicine.biosimilar or not medicine.authorisation_date:
		return None
	indication = _indication_text(medicine)
	if not indication:
		return None
	substances = tuple(sorted(normalise(s) for s in medicine.substances))
	return (medicine.regulator, substances, indication)


def _lead_status(medicine):
	return "Approved" if medicine.regulator == Regulator.FDA else "Authorised"


def _regulator_key(medicine):
	if medicine.regulator == Regulator.FDA:
		return medicine.fda_application_number or ""
	return medicine.ema_product_number or ""


def group_lead(products):
	"""The lead of a group of products with the same ``group_key``.

	An authorised product (status exactly ``Authorised`` on EMA rows, ``Approved`` on FDA
	rows) leads over a withdrawn or discontinued one, then the earliest authorisation, then
	the lowest EMA product number or FDA application number."""
	return min(
		products,
		key=lambda p: (p.status != _lead_status(p), p.authorisation_date, _regulator_key(p)),
	)


def category_areas(category):
	"""The union of the EMA areas of ``category``'s subjects, in a stable order.

	Reads ``category.subjects.all()``, so prefetch ``subjects`` when calling this for many
	categories."""
	areas = []
	seen = set()
	for subject in sorted(category.subjects.all(), key=lambda s: s.pk):
		for area in subject.ema_therapeutic_areas or []:
			key = normalise(area)
			if key and key not in seen:
				seen.add(key)
				areas.append(area.strip())
	return areas


def category_fda_terms(category):
	"""The union of the FDA indication terms of ``category``'s subjects, in a stable order.

	Reads ``category.subjects.all()``, so prefetch ``subjects`` when calling this for many
	categories."""
	terms = []
	seen = set()
	for subject in sorted(category.subjects.all(), key=lambda s: s.pk):
		for term in subject.fda_indication_terms or []:
			key = normalise(term)
			if key and key not in seen:
				seen.add(key)
				terms.append(term.strip())
	return terms
