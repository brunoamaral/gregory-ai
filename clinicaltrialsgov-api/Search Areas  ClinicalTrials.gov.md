---
title: "Search Areas | ClinicalTrials.gov"
source: "https://clinicaltrials.gov/data-api/about-api/search-areas"
author:
published:
created: 2025-12-25
description: "Description of the parts of a study record that can be searched using different fields. This applies to the API and the ClinicalTrials.gov search form"
tags:
  - "clippings"
---
## Introduction

The Search Areas describe the parts of a study record that are searched for content using different fields. Some search areas consist of groups of weighted study fields that can be searched at once (for example, BasicSearch area consists of 43 data fields). This also applies to the search form on the ClinicalTrials.gov homepage.

Search areas can also consist of a single data field (for example, Acronym, BriefTitle).

See the discussions of [Constructing Complex Search Queries](https://clinicaltrials.gov/find-studies/constructing-complex-search-queries "Constructing Complex Search Queries") (https://clinicaltrials.gov/find-studies/constructing-complex-search-queries) for more information on using search areas when conducting searches and to learn how to build detailed searches on ClinicalTrials.gov.

The Data Field column refers to "Piece Name" within the [Study Data Structure](https://clinicaltrials.gov/study-data-structure "Study Data Structure") (https://clinicaltrials.gov/study-data-structure). Fields producing synonyms are marked with **✓**.

ClinicalTrials.gov only supports one search document: Study. It contains 19 search areas.

## BasicSearch area

This is a default search area for a query entered in "Other terms" input field of search form in UI.

Request parameter: `query.term`.

The area contains 57 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| NCTId | 1 | nct |
| Acronym | 1 | text **✓** |
| BriefTitle | 0.89 | text **✓** |
| OfficialTitle | 0.85 | text **✓** |
| Condition | 0.81 | text **✓** |
| InterventionName | 0.8 | text **✓** |
| InterventionOtherName | 0.75 | text **✓** |
| Phase | 0.65 | [enum Phase](https://clinicaltrials.gov/study-data-structure#enum-Phase "enum Phase") (https://clinicaltrials.gov/study-data-structure#enum-Phase) |
| StdAge | 0.65 | [enum StandardAge](https://clinicaltrials.gov/study-data-structure#enum-StandardAge "enum StandardAge") (https://clinicaltrials.gov/study-data-structure#enum-StandardAge) |
| PrimaryOutcomeMeasure  Keyword | 0.6 | text **✓** |
| BriefSummary | 0.6 | markup **✓** |
| ArmGroupLabel  SecondaryOutcomeMeasure | 0.5 | text **✓** |
| InterventionDescription  ArmGroupDescription | 0.45 | markup **✓** |
| PrimaryOutcomeDescription | 0.4 | markup **✓** |
| LeadSponsorName  OrgStudyId  SecondaryId | 0.4 | text |
| NCTIdAlias | 0.4 | nct |
| InterventionType | 0.35 | [enum InterventionType](https://clinicaltrials.gov/study-data-structure#enum-InterventionType "enum InterventionType") (https://clinicaltrials.gov/study-data-structure#enum-InterventionType) |
| ArmGroupType | 0.35 | [enum ArmGroupType](https://clinicaltrials.gov/study-data-structure#enum-ArmGroupType "enum ArmGroupType") (https://clinicaltrials.gov/study-data-structure#enum-ArmGroupType) |
| SecondaryOutcomeDescription | 0.35 | markup **✓** |
| LocationFacility | 0.35 | text |
| LocationStatus | 0.35 | [enum RecruitmentStatus](https://clinicaltrials.gov/study-data-structure#enum-RecruitmentStatus "enum RecruitmentStatus") (https://clinicaltrials.gov/study-data-structure#enum-RecruitmentStatus) |
| LocationState | 0.35 | GeoName |
| LocationCountry | 0.35 | text |
| LocationCity | 0.35 | GeoName |
| BioSpecDescription | 0.3 | markup **✓** |
| ResponsiblePartyInvestigatorFullName  ResponsiblePartyInvestigatorTitle  ResponsiblePartyInvestigatorAffiliation  ResponsiblePartyOldNameTitle  ResponsiblePartyOldOrganization  OverallOfficialAffiliation  OverallOfficialRole  OverallOfficialName  CentralContactName  ConditionMeshTerm  InterventionMeshTerm | 0.3 | text **✓** |
| DesignAllocation | 0.3 | [enum DesignAllocation](https://clinicaltrials.gov/study-data-structure#enum-DesignAllocation "enum DesignAllocation") (https://clinicaltrials.gov/study-data-structure#enum-DesignAllocation) |
| DesignInterventionModel | 0.3 | [enum InterventionalAssignment](https://clinicaltrials.gov/study-data-structure#enum-InterventionalAssignment "enum InterventionalAssignment") (https://clinicaltrials.gov/study-data-structure#enum-InterventionalAssignment) |
| DesignMasking | 0.3 | [enum DesignMasking](https://clinicaltrials.gov/study-data-structure#enum-DesignMasking "enum DesignMasking") (https://clinicaltrials.gov/study-data-structure#enum-DesignMasking) |
| DesignWhoMasked | 0.3 | [enum WhoMasked](https://clinicaltrials.gov/study-data-structure#enum-WhoMasked "enum WhoMasked") (https://clinicaltrials.gov/study-data-structure#enum-WhoMasked) |
| DesignObservationalModel | 0.3 | [enum ObservationalModel](https://clinicaltrials.gov/study-data-structure#enum-ObservationalModel "enum ObservationalModel") (https://clinicaltrials.gov/study-data-structure#enum-ObservationalModel) |
| DesignPrimaryPurpose | 0.3 | [enum PrimaryPurpose](https://clinicaltrials.gov/study-data-structure#enum-PrimaryPurpose "enum PrimaryPurpose") (https://clinicaltrials.gov/study-data-structure#enum-PrimaryPurpose) |
| DesignTimePerspective | 0.3 | [enum DesignTimePerspective](https://clinicaltrials.gov/study-data-structure#enum-DesignTimePerspective "enum DesignTimePerspective") (https://clinicaltrials.gov/study-data-structure#enum-DesignTimePerspective) |
| StudyType | 0.3 | [enum StudyType](https://clinicaltrials.gov/study-data-structure#enum-StudyType "enum StudyType") (https://clinicaltrials.gov/study-data-structure#enum-StudyType) |
| ConditionAncestorTerm  InterventionAncestorTerm | 0.25 | text **✓** |
| CollaboratorName | 0.25 | text |
| OtherOutcomeMeasure  OutcomeMeasureTitle | 0.15 | text **✓** |
| OtherOutcomeDescription  OutcomeMeasureDescription | 0.1 | markup **✓** |
| LocationContactName | 0.1 | text |

## ConditionSearch area

This is a default search area for a query entered in "Conditions or disease" input field of search form in UI.

Request parameter: `query.cond`.

The area contains 7 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| Condition | 0.95 | text **✓** |
| BriefTitle | 0.6 | text **✓** |
| OfficialTitle | 0.55 | text **✓** |
| ConditionMeshTerm | 0.5 | text **✓** |
| ConditionAncestorTerm | 0.4 | text **✓** |
| Keyword | 0.3 | text **✓** |
| NCTId | 0.2 | nct |

## InterventionSearch area

This is a default search area for a query entered in "Intervention / treatment" input field of search form in UI.

Request parameter: `query.intr`.

The area contains 12 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| InterventionName | 0.95 | text **✓** |
| InterventionType | 0.85 | [enum InterventionType](https://clinicaltrials.gov/study-data-structure#enum-InterventionType "enum InterventionType") (https://clinicaltrials.gov/study-data-structure#enum-InterventionType) |
| ArmGroupType | 0.85 | [enum ArmGroupType](https://clinicaltrials.gov/study-data-structure#enum-ArmGroupType "enum ArmGroupType") (https://clinicaltrials.gov/study-data-structure#enum-ArmGroupType) |
| InterventionOtherName | 0.75 | text **✓** |
| BriefTitle | 0.65 | text **✓** |
| OfficialTitle | 0.6 | text **✓** |
| ArmGroupLabel | 0.5 | text **✓** |
| InterventionMeshTerm | 0.5 | text **✓** |
| Keyword | 0.5 | text **✓** |
| InterventionAncestorTerm | 0.4 | text **✓** |
| InterventionDescription | 0.4 | markup **✓** |
| ArmGroupDescription | 0.4 | markup **✓** |

## InterventionNameSearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| InterventionName | 1 | text **✓** |
| InterventionOtherName | 0.9 | text **✓** |

## ObsoleteConditionSearch area

The area contains 4 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| Condition | 0.95 | text **✓** |
| ConditionMeshTerm | 0.8 | text **✓** |
| ConditionAncestorTerm | 0.8 | text **✓** |
| Keyword | 0.6 | text **✓** |

## ExternalIdsSearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| OrgStudyId | 0.9 | text |
| SecondaryId | 0.7 | text |

## ExternalIdTypesSearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| OrgStudyIdType | 0.9 | [enum OrgStudyIdType](https://clinicaltrials.gov/study-data-structure#enum-OrgStudyIdType "enum OrgStudyIdType") (https://clinicaltrials.gov/study-data-structure#enum-OrgStudyIdType) |
| SecondaryIdType | 0.7 | [enum SecondaryIdType](https://clinicaltrials.gov/study-data-structure#enum-SecondaryIdType "enum SecondaryIdType") (https://clinicaltrials.gov/study-data-structure#enum-SecondaryIdType) |

## EligibilitySearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| EligibilityCriteria | 0.95 | markup **✓** |
| StudyPopulation | 0.8 | markup **✓** |

## OutcomeSearch area

This is a default search area for a query entered in "Outcome measure" input field of search form in UI.

Request parameter: `query.outc`.

The area contains 9 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| PrimaryOutcomeMeasure | 0.9 | text **✓** |
| SecondaryOutcomeMeasure | 0.8 | text **✓** |
| PrimaryOutcomeDescription | 0.6 | markup **✓** |
| SecondaryOutcomeDescription | 0.5 | markup **✓** |
| OtherOutcomeMeasure | 0.4 | text **✓** |
| OutcomeMeasureTitle | 0.4 | text **✓** |
| OtherOutcomeDescription | 0.3 | markup **✓** |
| OutcomeMeasureDescription | 0.3 | markup **✓** |
| OutcomeMeasurePopulationDescription | 0.3 | markup **✓** |

## OutcomeNameSearch area

The area contains 4 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| PrimaryOutcomeMeasure | 0.98 | text **✓** |
| SecondaryOutcomeMeasure | 0.8 | text **✓** |
| OtherOutcomeMeasure | 0.5 | text **✓** |
| OutcomeMeasureTitle | 0.3 | text **✓** |

## TitleSearch area

This is a default search area for a query entered in "Title / acronym" input field of search form in UI.

Request parameter: `query.titles`.

The area contains 3 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| Acronym | 1 | text **✓** |
| BriefTitle | 0.95 | text **✓** |
| OfficialTitle | 0.8 | text **✓** |

## LocationSearch area

This is a default search area for a query entered in "Location terms" input field of search form in UI.

Request parameter: `query.locn`.

The area contains 5 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| LocationCity | 0.95 | GeoName |
| LocationState | 0.95 | GeoName |
| LocationCountry | 0.95 | text |
| LocationFacility | 0.95 | text |
| LocationZip | 0.35 | text |

## ContactSearch area

The area contains 4 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| OverallOfficialName | 0.95 | text |
| CentralContactName | 0.9 | text |
| OverallOfficialAffiliation | 0.85 | text |
| LocationContactName | 0.8 | text |

## NCTIdSearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| NCTId | 1 | nct |
| NCTIdAlias | 0.9 | nct |

## IdSearch area

This is a default search area for a query entered in "Study IDs" input field of search form in UI.

Request parameter: `query.id`.

The area contains 5 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| NCTId | 1 | nct |
| NCTIdAlias | 0.9 | nct |
| Acronym | 0.85 | text **✓** |
| OrgStudyId | 0.8 | text |
| SecondaryId | 0.75 | text |

## FunderTypeSearch area

The area contains 2 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| LeadSponsorClass | 1 | [enum AgencyClass](https://clinicaltrials.gov/study-data-structure#enum-AgencyClass "enum AgencyClass") (https://clinicaltrials.gov/study-data-structure#enum-AgencyClass) |
| CollaboratorClass | 0.9 | [enum AgencyClass](https://clinicaltrials.gov/study-data-structure#enum-AgencyClass "enum AgencyClass") (https://clinicaltrials.gov/study-data-structure#enum-AgencyClass) |

## ResponsiblePartySearch area

The area contains 5 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| ResponsiblePartyInvestigatorFullName | 0.9 | text **✓** |
| ResponsiblePartyOldNameTitle | 0.8 | text **✓** |
| ResponsiblePartyInvestigatorAffiliation | 0.8 | text **✓** |
| ResponsiblePartyOldOrganization | 0.7 | text **✓** |
| ResponsiblePartyInvestigatorTitle | 0.7 | text **✓** |

## PatientSearch area

Request parameter: `query.patient`.

The area contains 47 data fields:

| Data Field | Weight | Type |
| --- | --- | --- |
| Acronym | 1 | text **✓** |
| Condition | 0.95 | text **✓** |
| BriefTitle | 0.9 | text **✓** |
| OfficialTitle | 0.85 | text **✓** |
| ConditionMeshTerm | 0.8 | text **✓** |
| ConditionAncestorTerm | 0.7 | text **✓** |
| BriefSummary | 0.65 | markup **✓** |
| Keyword  InterventionName  InterventionOtherName  PrimaryOutcomeMeasure | 0.6 | text **✓** |
| StdAge | 0.6 | [enum StandardAge](https://clinicaltrials.gov/study-data-structure#enum-StandardAge "enum StandardAge") (https://clinicaltrials.gov/study-data-structure#enum-StandardAge) |
| ArmGroupLabel | 0.5 | text **✓** |
| SecondaryOutcomeMeasure | 0.5 | text **✓** |
| InterventionDescription | 0.45 | markup **✓** |
| ArmGroupDescription | 0.45 | markup **✓** |
| PrimaryOutcomeDescription | 0.45 | markup **✓** |
| LeadSponsorName | 0.4 | text |
| OrgStudyId | 0.4 | text |
| SecondaryId | 0.4 | text |
| NCTIdAlias | 0.4 | nct |
| SecondaryOutcomeDescription | 0.35 | markup **✓** |
| LocationFacility | 0.35 | text |
| LocationState | 0.35 | GeoName |
| LocationCountry | 0.35 | text |
| LocationCity | 0.35 | GeoName |
| BioSpecDescription | 0.3 | markup **✓** |
| ResponsiblePartyInvestigatorFullName | 0.3 | text **✓** |
| ResponsiblePartyInvestigatorTitle | 0.3 | text **✓** |
| ResponsiblePartyInvestigatorAffiliation | 0.3 | text **✓** |
| ResponsiblePartyOldNameTitle | 0.3 | text **✓** |
| ResponsiblePartyOldOrganization | 0.3 | text **✓** |
| OverallOfficialAffiliation | 0.3 | text |
| OverallOfficialName | 0.3 | text |
| CentralContactName | 0.3 | text |
| DesignInterventionModel | 0.3 | [enum InterventionalAssignment](https://clinicaltrials.gov/study-data-structure#enum-InterventionalAssignment "enum InterventionalAssignment") (https://clinicaltrials.gov/study-data-structure#enum-InterventionalAssignment) |
| DesignMasking | 0.3 | [enum DesignMasking](https://clinicaltrials.gov/study-data-structure#enum-DesignMasking "enum DesignMasking") (https://clinicaltrials.gov/study-data-structure#enum-DesignMasking) |
| DesignWhoMasked | 0.3 | [enum WhoMasked](https://clinicaltrials.gov/study-data-structure#enum-WhoMasked "enum WhoMasked") (https://clinicaltrials.gov/study-data-structure#enum-WhoMasked) |
| DesignObservationalModel | 0.3 | [enum ObservationalModel](https://clinicaltrials.gov/study-data-structure#enum-ObservationalModel "enum ObservationalModel") (https://clinicaltrials.gov/study-data-structure#enum-ObservationalModel) |
| DesignPrimaryPurpose | 0.3 | [enum PrimaryPurpose](https://clinicaltrials.gov/study-data-structure#enum-PrimaryPurpose "enum PrimaryPurpose") (https://clinicaltrials.gov/study-data-structure#enum-PrimaryPurpose) |
| DesignTimePerspective | 0.3 | [enum DesignTimePerspective](https://clinicaltrials.gov/study-data-structure#enum-DesignTimePerspective "enum DesignTimePerspective") (https://clinicaltrials.gov/study-data-structure#enum-DesignTimePerspective) |
| InterventionMeshTerm | 0.3 | text **✓** |
| InterventionAncestorTerm | 0.25 | text **✓** |
| CollaboratorName | 0.25 | text |
| OtherOutcomeMeasure | 0.15 | text **✓** |
| OtherOutcomeDescription | 0.1 | markup **✓** |
| LocationContactName | 0.1 | text |

Last updated on **June 07, 2024**

[Back to Top](https://clinicaltrials.gov/data-api/about-api/#)