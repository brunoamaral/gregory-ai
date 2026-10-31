"""
The key that groups one drug's records across regulators.

``Medicine.substances`` holds what each regulator calls the active ingredient, and they
do not agree: EMA files Gilenya under ``fingolimod`` and the FDA under ``fingolimod
hydrochloride``. ``substance_key`` removes the parts that differ (the salt, a delivery
enzyme) and joins what is left, so both records of Gilenya get ``fingolimod``.

Pure functions with no database access: the syncs, the ``refresh_substance_keys``
command, the data migration and the API's ``substance`` filter all read the rules here.
A change to the word lists reaches stored rows on the next pipeline run, because
``refresh_substance_keys`` recomputes every key.
"""

import re

_WHITESPACE = re.compile(r"\s+")

MAX_LENGTH = 500

#: Cations. They follow the anion in a few FDA names ("naproxen sodium"). Also the guard
#: of ``strip_salt``: when what would remain is one of these ("sodium" in "sodium
#: chloride") the name is an electrolyte, not a drug with a salt.
COUNTER_IONS = frozenset(
	{
		"sodium",
		"disodium",
		"potassium",
		"dipotassium",
		"calcium",
		"magnesium",
		"lithium",
		"zinc",
		"meglumine",
		"tromethamine",
		"lysine",
	}
)

#: Trailing words that make a substance a salt, hydrate or ester salt of the same drug.
SALT_WORDS = (
	frozenset(
		{
			"hydrochloride",
			"dihydrochloride",
			"hydrobromide",
			"hydrate",
			"monohydrate",
			"dihydrate",
			"trihydrate",
			"hemihydrate",
			"sesquihydrate",
			"anhydrous",
			"tartrate",
			"bitartrate",
			"mesylate",
			"mesilate",
			"dimesylate",
			"maleate",
			"fumarate",
			"hemifumarate",
			"succinate",
			"citrate",
			"sulfate",
			"bisulfate",
			"acetate",
			"diacetate",
			"phosphate",
			"besylate",
			"besilate",
			"tosylate",
			"tosilate",
			"malate",
			"lactate",
			"gluconate",
			"nitrate",
			"oxalate",
			"bromide",
			"chloride",
			"iodide",
			"pamoate",
			"hyclate",
			"edisylate",
			"napsylate",
			"xinafoate",
			"trifenatate",
		}
	)
	| COUNTER_IONS
)

#: Salt words written as two words, dropped as a pair ("fingolimod lauryl sulfate").
SALT_PHRASES = (("lauryl", "sulfate"),)

#: Names whose last word looks like a salt but is part of the drug.
KEEP_WHOLE = frozenset({"dimethyl fumarate", "diroximel fumarate", "monomethyl fumarate"})

#: Enzymes the FDA lists as a second ingredient of a subcutaneous antibody. EMA files the
#: product under the antibody alone, so the enzyme is dropped when something else remains.
DELIVERY_ENZYMES = frozenset(
	{"hyaluronidase", "hyaluronidase (human recombinant)", "berahyaluronidase alfa"}
)


def _normalise(text):
	return _WHITESPACE.sub(" ", (text or "").lower()).strip()


def strip_salt(substance):
	"""``substance`` without its salt: ``fingolimod hydrochloride`` gives ``fingolimod``.

	Trailing salt words go one at a time while more than one word remains. Stripping
	stops when what would remain is itself a counter-ion (``sodium chloride`` stays as
	it is). Names in ``KEEP_WHOLE`` are returned as they are."""
	words = _normalise(substance).split(" ")
	while len(words) > 1 and " ".join(words) not in KEEP_WHOLE:
		if len(words) > 2 and tuple(words[-2:]) in SALT_PHRASES:
			remainder = words[:-2]
		elif words[-1] in SALT_WORDS:
			remainder = words[:-1]
		else:
			break
		if len(remainder) == 1 and remainder[0] in COUNTER_IONS:
			break
		words = remainder
	return " ".join(words)


def standalone_keys(medicines):
	"""Give each medicine in ``medicines`` that has no key yet (no substances, and a pk now
	that it is saved) its ``medicine-<pk>`` key. Returns the ones changed, for a
	``bulk_update`` of ``substance_key``."""
	fixed = []
	for medicine in medicines:
		if not medicine.substance_key and medicine.pk is not None:
			medicine.substance_key = substance_key([], pk=medicine.pk)
			fixed.append(medicine)
	return fixed


def substance_key(substances, regulator=None, pk=None):
	"""The key for a record: each substance through ``strip_salt``, delivery enzymes
	removed (only when another substance remains), then unique, sorted and joined with
	``+``. Gilenya at both regulators is ``fingolimod``; Exforge is
	``amlodipine+valsartan``.

	No substances gives ``medicine-<pk>`` so the record stands alone, or ``""`` when
	``pk`` is not known yet. ``regulator`` is accepted for callers that have it; the
	rule is the same for both."""
	stripped = {strip_salt(s) for s in (substances or [])}
	stripped.discard("")
	parts = stripped - DELIVERY_ENZYMES or stripped
	if not parts:
		return f"medicine-{pk}" if pk is not None else ""
	# The column is varchar(500); an absurdly long combination must not stop a sync.
	return "+".join(sorted(parts))[:MAX_LENGTH]
