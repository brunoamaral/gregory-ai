# PubMed fixtures

- `efetch-sample-3-records.xml`: three real records fetched from NCBI EFetch on 2026-10-17
  (multiple sclerosis papers). Unedited.
- `edge-cases.xml`: hand-trimmed and synthetic records that exercise parser branches the
  real samples don't: inline markup, unstructured abstracts, `MedlineDate`, `Season`,
  numeric months, collective and nameless authors, URL-form ORCIDs, retractions, a book
  article and a DOI found only in `ELocationID`. The PMIDs and names are invented.

Tests never call NCBI; these files are the whole input.
