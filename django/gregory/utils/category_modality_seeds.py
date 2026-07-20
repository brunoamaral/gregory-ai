"""Curated intervention-modality classification for TeamCategory.

Maps category_slug -> CategoryModality value for the ~100 hand-curated categories
that existed in the dev/prod DB as of the 2026-07-20 audit (see
CATEGORY-MODALITY-PLAN.md). Applied by sync_category_modalities, which only ever
sets `modality` on categories where it is currently null — the admin is the source
of truth after the initial seed, this file is just the starting point.

`research_topic` marks categories that are not interventions at all (disease
mechanisms, biomarkers, review collections, a bare target name, or a vendor name).
It is load-bearing, not a dumping ground: the sponsor-landscape view excludes it
so that e.g. `Myelin` (411 trials) is never grouped as if it were a treatment.

Ambiguous coded compounds (BMS-986368, PTD802) and the generic
`pharmacological-agents` bucket are deliberately left out of this mapping — null
is a legitimate "not curated yet" state, and guessing would be worse than leaving
them for hand curation in the admin.
"""

CATEGORY_MODALITY_SEEDS: dict[str, str] = {
	# Small-molecule drugs
	"aa147": "small_molecule",
	"amiselimod": "small_molecule",
	"bexarotene": "small_molecule",
	"biib091": "small_molecule",
	"biotin": "small_molecule",
	"cladribine": "small_molecule",
	"clemastine-fumarate": "small_molecule",
	"cnm-au8": "small_molecule",
	"tecfidera": "small_molecule",
	"vumerity": "small_molecule",
	"domperidone": "small_molecule",
	"donepezil": "small_molecule",
	"ebselen": "small_molecule",
	"evobrutinib": "small_molecule",
	"fingolimod": "small_molecule",
	"fingolimod-fty720": "small_molecule",
	"gsk239512": "small_molecule",
	"high-dose-biotin-md1003": "small_molecule",
	"hydroxychloroquine": "small_molecule",
	"ibudilast-mn-166": "small_molecule",
	"icp-022": "small_molecule",
	"imu-838": "small_molecule",
	"indapamide": "small_molecule",
	"irx4204": "small_molecule",
	"liothyronine-t3": "small_molecule",
	"ll-341070": "small_molecule",
	"masitinib": "small_molecule",
	"metformin": "small_molecule",
	"mitoxantrone": "small_molecule",
	"mn-166": "small_molecule",
	"monomethyl-fumarate": "small_molecule",
	"naltrexone": "small_molecule",
	# "Niraparib" and "Vafidemstat" (below) are capitalized on purpose: those two
	# rows' category_slug is stored capitalized in the live DB (not the usual
	# slugify() lowercase output) — verified against dev/prod, no lowercase
	# counterpart exists for either.
	"Niraparib": "small_molecule",
	"ozanimod": "small_molecule",
	"pipe-307": "small_molecule",
	"ponesimod": "small_molecule",
	"rg7845": "small_molecule",
	"sildenafil-viagra": "small_molecule",
	"simvastatin": "small_molecule",
	"simvastatin-ms-stat2": "small_molecule",
	"siponimod": "small_molecule",
	"teriflunomide": "small_molecule",
	"tolebrutinib": "small_molecule",
	"Vafidemstat": "small_molecule",  # see comment above Niraparib
	# Antibodies / biologics
	"alemtuzumab": "biologic_antibody",
	"anti-nogo-receptor-therapies": "biologic_antibody",
	"elezanumab": "biologic_antibody",
	"foralumab": "biologic_antibody",
	"frexalimab": "biologic_antibody",
	"hizentra": "biologic_antibody",
	"sa-il-33": "biologic_antibody",
	"maresin-1": "biologic_antibody",
	"natalizumab": "biologic_antibody",
	"ocrelizumab": "biologic_antibody",
	"ofatumumab": "biologic_antibody",
	"opicinumab": "biologic_antibody",
	"rhigm22": "biologic_antibody",
	"rituximab": "biologic_antibody",
	"sar441344": "biologic_antibody",
	"ublituximab": "biologic_antibody",
	# Cell / gene therapy
	"ahsct": "cell_gene_therapy",
	"car-t": "cell_gene_therapy",
	"cell-based-therapies": "cell_gene_therapy",
	"gene-therapy": "cell_gene_therapy",
	"imcy-0141": "cell_gene_therapy",
	"msc-therapies": "cell_gene_therapy",
	"neurovax": "cell_gene_therapy",
	"opc-transplantation": "cell_gene_therapy",
	"raav-vectors": "cell_gene_therapy",
	"stem-cells": "cell_gene_therapy",
	"tcelna": "cell_gene_therapy",
	"ytb323": "cell_gene_therapy",
	# Rehabilitation / physical
	"physical-therapy-and-telerehabilitation": "rehabilitation",
	# Device / neuromodulation
	"biomaterials-and-scaffolds": "device_neuromodulation",
	"neuromodulation-and-alternative": "device_neuromodulation",
	"tms-rtms": "device_neuromodulation",
	# Natural product / supplement
	"cordycepin": "natural_product",
	"dihydroartemisinin": "natural_product",
	"ginsenoside-rg1": "natural_product",
	"honokiol": "natural_product",
	"kynurenine": "natural_product",
	"lipoic-acid": "natural_product",
	"nanocurcumin": "natural_product",
	"ninjinyoeito": "natural_product",
	"piperine": "natural_product",
	"sativex": "natural_product",
	# Research topic (not an intervention)
	"btk-inhibitors": "research_topic",
	"clinical-trials": "research_topic",
	"epstein-barr-virus": "research_topic",
	"gpr17": "research_topic",
	"imaging-and-biomarkers": "research_topic",
	"multiple-sclerosis-reviews": "research_topic",
	"myelin": "research_topic",
	"neuroinflammation-reviews": "research_topic",
	"remyelination-general": "research_topic",
	"sandoz": "research_topic",
	"tdp6": "research_topic",
}
