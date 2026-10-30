"""
The database side of medicine matching: what ``match_medicines`` runs, and what the
category save hooks (the admin, the editor API) run for one category.

The rules live in ``gregory/services/medicine_matching.py`` and touch no database. This
module loads the products, asks the rules what should be linked, and writes the
difference. Three kinds of write, each re-derived from scratch on every run:

- ``link_category``: automatic ``MedicineCategoryAssignment`` rows of one category;
- ``link_subject``: ``MedicineSubjectLink`` rows of one subject, from its EMA areas;
- ``group_medicines``: ``Medicine.grouped_under``, the lead product of each group of copies.

A link an editor made or suppressed is never touched: a manual category link skips every
rule, and a suppressed link (an editor removed an automatic one, or took a medicine off a
subject) is never recreated, changed or restored. These are pipeline writes, so no editor
is recorded, as with ``rebuild_categories``.

Every function takes ``medicines`` (the result of ``load_medicines``) so a caller that
runs many of them loads the products once, and ``dry_run`` to compute without writing.
"""

from collections import defaultdict

from django.db import transaction

from gregory.models import (
	CategoryAssignmentSource,
	CategoryType,
	Medicine,
	MedicineCategoryAssignment,
	MedicineSubjectLink,
)
from gregory.services.medicine_matching import (
	category_areas,
	category_fda_terms,
	group_key,
	group_lead,
	product_matches,
	subject_match,
)

BATCH_SIZE = 500

# Only the fields the rules read.
MEDICINE_FIELDS = (
	"id",
	"regulator",
	"name",
	"substances",
	"is_combination",
	"therapeutic_areas",
	"atc_code",
	"indication",
	"generic",
	"biosimilar",
	"status",
	"authorisation_date",
	"ema_product_number",
	"grouped_under",
	"fda_application_number",
	"brand_names",
	"fda_pharm_classes",
	# Only filled on the candidates sync_fda_labels downloaded, so loading it for every
	# row stays small.
	"label_indications",
)


def load_medicines():
	"""Every product, with only the fields the matching rules read."""
	return list(Medicine.objects.only(*MEDICINE_FIELDS))


def _empty_counts():
	return {"created": 0, "removed": 0, "updated": 0, "kept": 0}


def _apply_links(model, owner, expected, managed, new_row, dry_run):
	"""Make ``model``'s rows for ``owner`` match ``expected`` (``{medicine id: matched_on}``).

	``owner`` is ``{"<field>": <instance>}``, the row's category or subject. ``managed``
	says which existing rows this run may change or delete (``{"suppressed": False}`` and
	so on); every other row is left alone. Returns the counts of what was, or on a dry run
	would be, created, removed, relabelled and kept.
	"""
	rows = list(
		model.objects.filter(**owner).only(
			"id", "medicine_id", "matched_on", *managed.keys()
		)
	)
	# Any existing row occupies its medicine: a manual link is the editor's, a suppressed
	# one was removed by an editor, and either way the unique constraint on
	# (medicine, owner) leaves no room for a second row.
	by_medicine = {row.medicine_id: row for row in rows}

	to_create = [
		new_row(medicine_id, label)
		for medicine_id, label in expected.items()
		if medicine_id not in by_medicine
	]
	to_remove = []
	to_update = []
	kept = 0
	for row in rows:
		if any(getattr(row, field) != value for field, value in managed.items()):
			continue
		label = expected.get(row.medicine_id)
		if label is None:
			to_remove.append(row.pk)
		elif row.matched_on != label:
			row.matched_on = label
			to_update.append(row)
		else:
			kept += 1

	if not dry_run and (to_create or to_remove or to_update):
		with transaction.atomic():
			if to_remove:
				# The managed filter again: an editor may have suppressed a row since it was read.
				model.objects.filter(pk__in=to_remove, **managed).delete()
			model.objects.bulk_update(to_update, ["matched_on"], batch_size=BATCH_SIZE)
			# ignore_conflicts: an editor may have added the same medicine by hand since the
			# rows were read; the unique constraint keeps theirs.
			model.objects.bulk_create(to_create, batch_size=BATCH_SIZE, ignore_conflicts=True)
	return {
		"created": len(to_create),
		"removed": len(to_remove),
		"updated": len(to_update),
		"kept": kept,
	}


def link_category(category, medicines=None, dry_run=False):
	"""Re-derive the automatic medicine links of one category.

	Creates a missing link (``source=automatic``, ``matched_on`` says why), deletes an
	automatic link that no longer matches and refreshes ``matched_on``. Manual links and
	suppressed links are never touched. A manual category gets no automatic links, so this
	returns zeros for it. Returns ``{"created", "removed", "updated", "kept"}``.
	"""
	if category.category_type != CategoryType.AUTOMATIC:
		return _empty_counts()
	if medicines is None:
		medicines = load_medicines()
	areas = category_areas(category)
	terms = category_fda_terms(category)
	expected = {}
	for medicine in medicines:
		label = product_matches(category, medicine, areas, terms)
		if label is not None:
			expected[medicine.pk] = label
	return _apply_links(
		MedicineCategoryAssignment,
		{"teamcategory": category},
		expected,
		{"source": CategoryAssignmentSource.AUTOMATIC, "suppressed": False},
		lambda medicine_id, label: MedicineCategoryAssignment(
			medicine_id=medicine_id,
			teamcategory=category,
			source=CategoryAssignmentSource.AUTOMATIC,
			matched_on=label,
		),
		dry_run,
	)


def link_subject(subject, medicines=None, dry_run=False):
	"""Re-derive the medicine links of one subject from its EMA therapeutic areas.

	Every non-diagnostic product approved for one of the subject's areas is linked,
	generics, biosimilars and grouped copies included (the read side hides those by
	default). A subject with no areas is a research area, not a condition: it gets no links
	and loses any non-suppressed ones, so clearing a subject's areas cleans up on the next
	run. A suppressed link is never touched. Returns the same counts as ``link_category``.
	"""
	if medicines is None:
		medicines = load_medicines()
	areas = subject.ema_therapeutic_areas or []
	expected = {}
	for medicine in medicines:
		label = subject_match(areas, medicine)
		if label is not None:
			expected[medicine.pk] = label
	return _apply_links(
		MedicineSubjectLink,
		{"subject": subject},
		expected,
		{"suppressed": False},
		lambda medicine_id, label: MedicineSubjectLink(
			medicine_id=medicine_id, subject=subject, matched_on=label
		),
		dry_run,
	)


def group_medicines(medicines=None, dry_run=False):
	"""Point each copy of a medicine at its lead (``Medicine.grouped_under``).

	Products with the same ``group_key`` are copies of one medicine; ``group_lead`` picks
	the lead. The copies get ``grouped_under = lead``; leads, singletons and products that
	are never grouped get null. Re-derived on every run, so a copy whose indication EMA
	rewrites leaves its group. Only rows whose value changed are written.

	Returns ``{"groups": n, "grouped": n, "changed": n}``: the groups with more than one
	product, the copies that point at a lead, and the rows written (or, on a dry run, that
	would be).
	"""
	if medicines is None:
		medicines = load_medicines()
	by_key = defaultdict(list)
	for medicine in medicines:
		key = group_key(medicine)
		if key is not None:
			by_key[key].append(medicine)

	expected = {}
	groups = 0
	for products in by_key.values():
		if len(products) < 2:
			continue
		groups += 1
		lead = group_lead(products)
		for product in products:
			if product.pk != lead.pk:
				expected[product.pk] = lead.pk

	changed = []
	for medicine in medicines:
		wanted = expected.get(medicine.pk)
		if medicine.grouped_under_id != wanted:
			changed.append((medicine, wanted))
	if not dry_run and changed:
		for medicine, wanted in changed:
			medicine.grouped_under_id = wanted
		with transaction.atomic():
			Medicine.objects.bulk_update(
				[medicine for medicine, _ in changed], ["grouped_under"], batch_size=BATCH_SIZE
			)
	return {"groups": groups, "grouped": len(expected), "changed": len(changed)}
