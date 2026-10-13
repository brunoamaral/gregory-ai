"""Export patent families to an XLSX workbook, one sheet per subject.

Spec: specs/patent-export.md. The workbook is built by
``gregory.exports.patents_xlsx.build_patents_workbook``, which the admin actions share.
"""

from django.core.management.base import BaseCommand, CommandError

from gregory.exports.patents_xlsx import build_patents_workbook, default_output_name
from gregory.exports.xlsx_common import _resolve_site_attribution
from gregory.models import Subject


class Command(BaseCommand):
	help = "Export patent families to an XLSX workbook, one sheet per subject."

	def add_arguments(self, parser):
		parser.add_argument(
			"--subjects",
			type=str,
			default="",
			help="Comma-separated subject IDs to export.",
		)
		parser.add_argument(
			"--all-subjects",
			action="store_true",
			default=False,
			help="Export every subject (one sheet each).",
		)
		parser.add_argument(
			"--output",
			type=str,
			default="",
			help="Output file path (default: patents_export_YYYYMMDD.xlsx in current directory).",
		)
		parser.add_argument(
			"--team",
			type=int,
			default=None,
			help="Optional team ID; filters which subjects are exported.",
		)
		parser.add_argument(
			"--site",
			type=str,
			required=True,
			help="Site ID or domain to attribute this export to. Required -- the "
			"export no longer infers a site from the exported subjects' teams "
			"(Team.site is retired), so pass the target site explicitly.",
		)

	def handle(self, *args, **options):
		# --- Resolve subjects ---
		if options["all_subjects"]:
			qs = Subject.objects.all()
			if options["team"]:
				qs = qs.filter(team_id=options["team"])
			subjects = list(qs.order_by("subject_name"))
		else:
			raw = options["subjects"].strip()
			if not raw:
				raise CommandError("Provide --subjects <ids> or --all-subjects.")
			try:
				ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
			except ValueError:
				raise CommandError("--subjects must be comma-separated integers.")
			subject_qs = Subject.objects.all()
			if options["team"]:
				subject_qs = subject_qs.filter(team_id=options["team"])
			valid_ids = set(subject_qs.filter(pk__in=ids).values_list("pk", flat=True))
			missing = set(ids) - valid_ids
			if missing:
				all_subs = subject_qs.values_list("pk", "subject_name")
				valid_list = ", ".join(f"{pk} ({name})" for pk, name in all_subs)
				raise CommandError(
					f"Subject ID(s) not found: {sorted(missing)}. Valid IDs: {valid_list}"
				)
			subjects = list(
				subject_qs.filter(pk__in=ids).order_by("subject_name")
			)

		if not subjects:
			raise CommandError("No subjects found.")

		# --- Resolve site attribution ---
		attribution = _resolve_site_attribution(options["site"])

		output_path = (
			options["output"] or default_output_name()
		)

		self.stdout.write(f"Exporting {len(subjects)} subject(s) → {output_path}")

		options_summary_parts = ["subjects: " + ", ".join(s.subject_name for s in subjects)]
		if options["team"]:
			options_summary_parts.append(f"team: {options['team']}")
		options_summary_parts.append(f"site: {attribution.site.domain}")
		wb = build_patents_workbook(
			subjects,
			attribution,
			options_summary="; ".join(options_summary_parts),
		)

		for ws in wb.worksheets:
			if ws.title in ("About this file", "Categories", "Glossary", "Data source"):
				continue
			if ws.title in ("Trial links", "Publications"):
				self.stdout.write(f'  Sheet "{ws.title}": {max(ws.max_row - 1, 0)} row(s)')
			else:
				first = ws.cell(row=2, column=1).value
				count = 0 if isinstance(first, str) and first.startswith("No patents") else ws.max_row - 1
				self.stdout.write(f'  Sheet "{ws.title}": {count} patent famil(ies)')

		wb.save(output_path)
		self.stdout.write(self.style.SUCCESS(f"Saved: {output_path}"))
