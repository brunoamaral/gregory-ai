---
title: "ClinicalTrials.gov API | ClinicalTrials.gov"
source: "https://clinicaltrials.gov/data-api/api"
author:
published:
created: 2025-12-25
description: "ClinicalTrials.gov API | ClinicalTrials.gov"
tags:
  - "clippings"
---
### Notice to API users:

ClinicalTrials.gov has a modernized data ingest as of August 26, 2025. Two major groups of data are impacted.

- Some "markup" fields, which contain rich formatted text in the legacy "chintzy" format (a subset of HTML formatting), do not have the exact format of the classic data pipeline.
- Locations and geopoint data are now pulling from a different database for geographic data.

To learn more about the Modernized ClinicalTrials.gov API, please visit [About the API](https://clinicaltrials.gov/data-api/about-api "About the API") (https://clinicaltrials.gov/data-api/about-api), which includes a [Migration Guide](https://clinicaltrials.gov/data-api/about-api/api-migration "Migration Guide") (https://clinicaltrials.gov/data-api/about-api/api-migration), descriptions of the [Search Areas](https://clinicaltrials.gov/data-api/about-api/search-areas "Search Areas") (https://clinicaltrials.gov/data-api/about-api/search-areas), and more.

## Introduction

The [CTG API specification](https://clinicaltrials.gov/api/oas/v2 "CTG API specification") (https://clinicaltrials.gov/api/oas/v2/ctg-oas-v2.yaml) is available in YAML format and can be used with a variety of [tools](https://openapi.tools/ "tools (opens in a new tab)") (https://openapi.tools/) and other software frameworks to generate client code for interacting with the REST API in a way that is specific for the target language or environment.

The [OpenAPI 3.0 Specification](https://spec.openapis.org/oas/v3.0.3 "OpenAPI 3.0 Specification (opens in a new tab)") (https://spec.openapis.org/oas/v3.0.3) is an open-source format for describing and documenting HTTP APIs. An OpenAPI 3.0 specification serves as the core definition for the API of the ClinicalTrials.gov website.

### Schedule of data updates

Data on ClinicalTrials.gov is refreshed daily Monday through Friday, *generally* by 9 a.m. ET (14:00 UTC). However, to ensure your API requests gather the most recent data, please check the “dataTimestamp” field available at [https://clinicaltrials.gov/api/v2/version](https://clinicaltrials.gov/api/v2/version "https://clinicaltrials.gov/api/v2/version") (https://clinicaltrials.gov/api/v2/version) to ensure the refresh has completed.

## ClinicalTrials.gov REST API

Last updated on **August 26, 2025**

Looking for U.S. government information and services?

[Visit USA.gov](https://www.usa.gov/ "Visit USA.gov (opens in a new tab)") (https://www.usa.gov/)

Revision: v3.3.3