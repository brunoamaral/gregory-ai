"""Curated sponsor families — the editorial layer on top of automatic key resolution.

normalize_sponsor_key() (gregory.utils.trial_field_normalizers) deliberately never strips
legal-entity suffixes (Ltd/Inc/GmbH/AG/S.A....) or groups spellings that could plausibly
name different real-world entities — that grouping decision belongs here, in curated
human-reviewed data, applied by the sync_sponsor_seeds management command.

Each entry maps a canonical display name to (sponsor_type, [raw spelling variants]).
Variants are matched by normalize_sponsor_key(variant) — a whole-string match, never a
substring match ("University of Rochester" must never fold into "Roche"/"Hoffmann-La
Roche" just because it contains "roche").

Built from the dev DB's top ~150 `primary_sponsor` names plus a targeted search for the
known variant families called out in TRIALS-SPONSOR-CANONICALIZATION-PLAN.md (2026-07-19
audit query: `SELECT primary_sponsor, count(*) FROM trials WHERE primary_sponsor IS NOT
NULL GROUP BY 1 ORDER BY 2 DESC`, plus `icontains` probes for novartis/roche/merck/
sanofi/pfizer/wyeth/biogen/genzyme/emd serono).

Deliberately conservative merge traps (do NOT add these to any family below):
  - "Merck Sharp & Dohme LLC" (US MSD) vs "Merck KGaA, Darmstadt, Germany" / "Merck
    Serono" / "EMD Serono" (a separate German company) — never merge these two families.
  - "MSDx, Inc." — an unrelated MS-focused biotech; contains "MSD" but is not Merck.
  - Bare "Merck" / "Merck AB" — ambiguous between the MSD and KGaA families without more
    context; left unmerged (auto-creates its own singleton Sponsor).
  - "NeuroBiogen Co., Ltd" — name collision with Biogen; a different company.
  - "BIOGEN-DOMPE` SRL" — a Biogen/Dompé joint venture, its own legal entity.
  - "Pfizer's Upjohn has merged with Mylan to form Viatris Inc." — the registry text
    itself says this arm is now Viatris, not Pfizer; never merge into Pfizer.
  - "Genentech, Inc. c/o F. Hoffmann-La Roche Ltd" and the long Spanish "... que actúa
    como representante de F. Hoffmann-La Roche..." local-representative strings — left
    unmerged (single-digit occurrences, genuinely different display entities).

When in doubt whether two names are the same company: leave them separate. Merging later
is one sync_sponsor_seeds edit; un-merging is manual surgery.
"""

SPONSOR_SEEDS: dict[str, tuple[str | None, list[str]]] = {
	"Novartis": (
		"industry",
		[
			"Novartis",
			"Novartis Pharmaceuticals",
			"Novartis Pharma AG",
			"Novartis Pharma Services AG",
			"Novartis Pharma GmbH",
			"Novartis Pharma Vertriebs GmbH",
			"Novartis Pharma S.A.S",
			"Novartis Pharma K.K.",
			"NOVARTIS FARMA",
			"NOVARTIS FARMA S.p.A",
			"Novartis Farma SpA",
			"Novartis Farmaceutica S.A.",
			"Novartis Farmacéutica S.A.",
			"Novartis Healthcare Pvt Ltd",
			"Novartis Healthcare Private Limited",
			"NOVARTIS BIOSCIENSES PERU S.A.,",
			"Novartis Pharmaceuticals Australia Pty Limited",
			"Novartis Pharmaceuticals Ltd (UK)",
		],
	),
	"F. Hoffmann-La Roche": (
		"industry",
		[
			"Hoffmann-La Roche",
			"F. Hoffmann-La Roche AG",
			"F. Hoffmann-La Roche Ltd",
			"F. Hoffmann-La Roche Ltd.",
			"F. Hoffman-La Roche Ltd",
			"F.Hoffmann-La Roche",
			"F. HOFFMANN - LA ROCHE LTD.",
			"F. HOFFMANN-LA ROCHE LTD",
			"F. HOFFMANN-LA ROCHE LTD.",
			"F. HOFFMANN-LA ROCHE LTD.,",
			"Hoffman La Roche Ltd",
			"ROCHE",
			"Roche (France)",
			"Roche (Germany)",
			"Roche (Switzerland)",
			"Roche (United States)",
			"Roche Nederland B.V.",
			"Roche Pharma AG",
			"Roche Products India Pvt Ltd",
		],
	),
	"Merck Sharp & Dohme (MSD)": (
		"industry",
		[
			"Merck Sharp & Dohme LLC",
		],
	),
	"Merck KGaA": (
		"industry",
		[
			"Merck KGaA",
			"Merck KGaA, Darmstadt, Germany",
			"Merck Healthcare KGaA",
			"Merck Healthcare Germany GmbH",
			"Merck Healthcare KGaA, Darmstadt Germany, an affiliate of Merck KGaA, Darmstadt, Germany",
			"Merck Healthcare KGaA, Darmstadt, Germany, an affiliate of Merck KGaA, Darmstadt, Germany",
			"Merck Serono",
			"Merck Serono (Germany)",
			"Merck Serono GmbH, Germany",
			"Merck Serono International S.A.",
			"Merck Serono International SA",
			"Merck Serono SA",
			"MERCK SERONO SA",
			"Merck Serono S.A. - Geneva",
			"Merck Serono SA – Geneva",
			"Merck Serono S.A. - Geneva, an affiliate of Merck KGaA, Darmstadt, Germany",
			"Merck Serono S.A.-Geneva, an affiliate of Merck KGaA Darmstadt, Germany",
			"Merck Serono SA - Geneva, An affiliate of Merck KGaA, Darmstadt, Germany",
			"MERCK SERONO s.a.s",
			"Merck Serono S.p.A.",
			"MERCK SERONO SPA",
			"Merck, S.L.",
			"MERCK S.P.A.",
			"EMD Serono",
			"EMD Serono Research & Development Institute, Inc.",
		],
	),
	"Pfizer": (
		"industry",
		[
			"Pfizer",
			"Pfizer Inc.",
			"Pfizer Inc. (USA)",
			"Wyeth is now a wholly owned subsidiary of Pfizer",
		],
	),
	"Biogen": (
		"industry",
		[
			"Biogen",
			"Biogen GmbH",
			"Biogen Idec",
			"Biogen Idec A/S",
			"Biogen Idec Australia Pty Ltd",
			"Biogen Idec GmbH",
			"Biogen Idec Inc",
			"Biogen Idec International GmbH",
			"Biogen Idec Italia S.r.l.",
			"Biogen Idec Limited",
			"Biogen Idec Ltd",
			"Biogen Idec Ltd.",
			"Biogen Idec LTD",
			"BIOGEN IDEC LTD",
			"Biogen Idec MA Inc",
			"Biogen Idec Norway",
			"Biogen Idec Research Limited",
			"Biogen Idec (USA)",
			"Biogen Japan Ltd.",
			"Biogen MA",
			"Biogen MA Inc Biogen Idec Research Limited",
			"Biogen Portugal",
		],
	),
	"Sanofi": (
		"industry",
		[
			"Sanofi",
			"Sanofi-aventis",
			"sanofi-aventis Recherche & Development,",
			"sanofi-aventis recherche & developpement",
			"Sanofi-Aventis Recherche & Developpement",
			"sanofi-aventis recherche & développement",
			"sanofi-aventis Recherche & Développement",
			"Sanofi-aventis recherche & développement",
			"Sanofi-Aventis recherche & développement",
			"sanofi-aventis recherche et development",
			"Sanofi-Aventis Recherche et Développement",
			"Sanofi-Aventis Research & Development",
			"Sanofi-aventis U.S. Inc.",
			"Sanofi-aventis US, Inc",
			"Sanofi B.V.",
			"Sanofi Healthcare India Private Limited",
			"SanofiSynthelaboIndiaPrivate Limited",
			"Genzyme",
			"Genzyme, a Sanofi Company",
			"Genzyme Corp.",
			"Genzyme Corporation",
			"Genzyme GmbH",
		],
	),
}
