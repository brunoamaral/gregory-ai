# Trials API

The Trials API provides access to clinical trials collected and processed by Gregory.

## Endpoints

### List Trials

```
GET /api/v1/trials/
```

Returns a paginated list of all clinical trials across all subjects and sources.

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| page | integer | Page number for pagination |
| ordering | string | Field to order by (e.g., `-discovery_date`, `title`) |
| search | string | Full-text search across title and description |
| discovery_date_after | date | Filter trials discovered after this date |
| discovery_date_before | date | Filter trials discovered before this date |
| status | string | Filter by trial status (e.g., "Recruiting", "Completed") |
| phase | string | Filter by trial phase (e.g., "Phase 1", "Phase 2") |

#### Response

```json
{
  "count": 500,
  "next": "https://api.example.com/api/v1/trials/?page=2",
  "previous": null,
  "results": [
    {
      "id": 12345,
      "title": "Example Clinical Trial",
      "description": "This is an example description for...",
      "discovery_date": "2023-05-15",
      "start_date": "2023-06-01",
      "end_date": "2024-06-01",
      "url": "https://clinicaltrials.gov/study/NCT12345678",
      "nct_id": "NCT12345678",
      "status": "Recruiting",
      "phase": "Phase 2",
      "source": {
        "id": 3,
        "name": "ClinicalTrials.gov"
      },
      "subject": {
        "id": 5,
        "name": "Neurology"
      },
      "categories": [
        {
          "id": 8,
          "name": "Drug Trials"
        }
      ]
    },
    // Additional trials...
  ]
}
```

### Get Trial Detail

```
GET /api/v1/trials/{id}/
```

Returns detailed information about a specific clinical trial.

#### Response

Same format as individual trial in list response, with additional fields:

```json
{
  "id": 12345,
  "title": "Example Clinical Trial",
  "description": "This is an example description for...",
  "discovery_date": "2023-05-15",
  "start_date": "2023-06-01",
  "end_date": "2024-06-01",
  "url": "https://clinicaltrials.gov/study/NCT12345678",
  "nct_id": "NCT12345678",
  "status": "Recruiting",
  "phase": "Phase 2",
  "source": {...},
  "subject": {...},
  "categories": [...],
  "eligibility_criteria": "Inclusion criteria: ...\nExclusion criteria: ...",
  "locations": [
    {
      "facility": "Example Medical Center",
      "city": "Boston",
      "state": "Massachusetts",
      "country": "United States"
    }
  ],
  "sponsors": [
    {
      "name": "Example Pharmaceutical",
      "type": "Industry"
    }
  ],
  "interventions": [
    {
      "type": "Drug",
      "name": "Example Drug",
      "description": "Once daily oral tablet"
    }
  ],
  "outcome_measures": [
    {
      "type": "Primary",
      "measure": "Change in symptoms",
      "timeframe": "24 weeks"
    }
  ]
}
```

## Related Endpoints

For more specific trial endpoints, see:

- [Trials by Subject](../subject-api.md)
- [Trials by Source](../source-api.md)
- [Trials by Team](../team-api.md)
- [Trial Search](../trial-search-api.md)
