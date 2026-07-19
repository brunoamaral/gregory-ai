# Empirical CTIS public-status code -> portal label mapping, derived 2026-07-18 by
# joining RSS labels to API codes for the same trials — see
# docs/ctis-public-api-schema.md. The same vocabulary applies at trial level
# (search.ctStatus) and country level (trialCountries "Name:code"). Codes not in this
# table (1, 6, 7, 9, 10, 12+ exist for statuses like "Temporarily halted", "Suspended",
# "Ended prematurely") must be log-and-skip — NEVER write a bare numeric code into a
# status column.
CTIS_PUBLIC_STATUS_LABELS = {
	2: "Authorised, recruitment pending",
	3: "Authorised, recruiting",
	4: "Ongoing, recruiting",
	5: "Ongoing, recruitment ended",
	8: "Ended",
	11: "Not authorised",
}

# trialRegion code -> portal label, cross-verified RSS<->API 2026-07-18. Code 1 was
# observed but its label was NOT cross-verified against an RSS "Trial region" string —
# leave it unmapped (log-and-skip) rather than guessing.
CTIS_TRIAL_REGION_LABELS = {
	3: "In both EEA and non-EEA",
}
