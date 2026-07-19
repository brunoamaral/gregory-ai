# CTIS public-status code -> portal label mapping. Originally derived 2026-07-19 by
# joining RSS labels to API codes for the same trials (codes 2,3,4,5,8,11), then fully
# confirmed and completed against the authoritative source: the public portal's own
# frontend code (the `optionName`/`id` array in its compiled Angular bundle, e.g.
# https://euclinicaltrials.eu/ctis-public/chunk-TT6KNQDE.js — chunk hash rotates on
# portal deploys, re-grep for `optionName:"Authorised, recruiting"` to relocate it).
# See docs/ctis-public-api-schema.md. The same vocabulary applies at trial level
# (search.ctStatus) and country level (trialCountries "Name:code"). Any code NOT in
# this table must still be log-and-skip — NEVER write a bare numeric code into a
# status column — since the portal can add new statuses without notice.
CTIS_PUBLIC_STATUS_LABELS = {
	1: "Under evaluation",
	2: "Authorised, recruitment pending",
	3: "Authorised, recruiting",
	4: "Ongoing, recruiting",
	5: "Ongoing, recruitment ended",
	6: "Temporarily halted",
	7: "Suspended",
	8: "Ended",
	9: "Expired",
	10: "Revoked",
	11: "Not authorised",
	12: "Cancelled",
}

# trialRegion code -> portal label. Code 3 cross-verified RSS<->API 2026-07-18; codes 1
# and 2 confirmed 2026-07-19 against the same portal frontend source as above
# (`optionName:"EEA only"` / `optionName:"Non-EEA only"` / `optionName:"In both EEA and
# non-EEA"`).
CTIS_TRIAL_REGION_LABELS = {
	1: "EEA only",
	2: "Non-EEA only",
	3: "In both EEA and non-EEA",
}
