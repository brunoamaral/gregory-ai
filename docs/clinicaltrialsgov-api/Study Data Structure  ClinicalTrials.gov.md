---
title: "Study Data Structure | ClinicalTrials.gov"
source: "https://clinicaltrials.gov/data-api/about-api/study-data-structure"
author:
published:
created: 2025-12-25
description: "Information about clinical study data fields on ClinicalTrials.gov, their data type, and JSON attributes"
tags:
  - "clippings"
---
## Introduction

The information below shows study data fields and their data type and other JSON attributes.

- "Piece Name" and "Alt Piece Names" are unique, so a field can be referenced by them.
- Fields marked with ⤷ start nested documents, which allow use of a SEARCH operator to target search results.
	- For example, this query matches city, state, and country inside one location: SEARCH\[Location\](AREA\[LocationCity\]Florence AND AREA\[LocationState\]South Carolina AND AREA\[LocationCountry\]United States)
- Fields marked with ✗ are available for search but not for retrieval.
- Fields producing synonyms are marked with ✓.

## Protocol Section

| Index Field | **protocolSection** |
| --- | --- |
| Data Type | ProtocolSection |
| Definition | [Study Protocol](https://clinicaltrials.gov/policy/protocol-definitions "Study Protocol") (https://clinicaltrials.gov/policy/protocol-definitions) |

| Index Field | protocolSection.**identificationModule** |
| --- | --- |
| Data Type | IdentificationModule |
| Definition | [Study Identification](https://clinicaltrials.gov/policy/protocol-definitions#identification "Study Identification") (https://clinicaltrials.gov/policy/protocol-definitions#identification) |

| Index Field | protocolSection.identificationModule.**nctId** |
| --- | --- |
| Data Type | nct ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NCTId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NCTId)) |
| Description | The unique identification code given to each clinical study upon registration at ClinicalTrials.gov. The format is "NCT" followed by an 8-digit number. Also known as ClinicalTrials.gov Identifier |

| Index Field | protocolSection.identificationModule.**nctIdAliases** |
| --- | --- |
| Data Type | nct\[\] ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NCTIdAlias "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NCTIdAlias)) |
| Description | Identifier(s) that are considered "Obsolete" or "Duplicate". No study is displayed on public site. Request is redirected/forwarded to another NCT Identifier |

| Index Field | protocolSection.identificationModule.**numNctAliases** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumNCTAliases "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumNCTAliases)) |
| Description | Number of obsolete identifiers for a NCTId |

## OrgStudyIdInfo

| Index Field | protocolSection.identificationModule.**orgStudyIdInfo** |
| --- | --- |
| Data Type | OrgStudyIdInfo |

| Index Field | protocolSection.identificationModule.orgStudyIdInfo.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OrgStudyId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OrgStudyId)) |
| Definition | [Unique Protocol Identification Number](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryId "Unique Protocol Identification Number") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryId) |

| Index Field | protocolSection.identificationModule.orgStudyIdInfo.**type** |
| --- | --- |
| Data Type |  |
| Description | Type of organization's unique protocol ID |

| Index Field | protocolSection.identificationModule.orgStudyIdInfo.**link** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OrgStudyIdLink "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OrgStudyIdLink)) |
| Description | URL link based on OrgStudyId and OrgStudyIdType input in PRS, include system-generated links to NIH RePORTER, specifically (associated with the types of federal funding identified as OrgStudyIdType) |

## SecondaryIdInfo

| Index Field | protocolSection.identificationModule.**secondaryIdInfos** |
| --- | --- |
| Data Type | SecondaryIdInfo\[\] |

| Index Field | protocolSection.identificationModule.secondaryIdInfos.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryId)) |
| Definition | [Secondary IDs](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIds "Secondary IDs") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIds) |

| Index Field | protocolSection.identificationModule.secondaryIdInfos.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Secondary ID Type](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIdType "Secondary ID Type") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIdType) |

| Index Field | protocolSection.identificationModule.secondaryIdInfos.**domain** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryIdDomain "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryIdDomain)) |
| Definition | [Description](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIdDescription "Description") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryIdDescription) |

| Index Field | protocolSection.identificationModule.secondaryIdInfos.**link** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryIdLink "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryIdLink)) |
| Description | URL link based on SecondaryId and SecondaryIdType, including system-generated links to NIH RePORTER, specifically (associated with the types of federal funding identified as SecondaryIdType) |

| Index Field | protocolSection.identificationModule.**numSecondaryIds** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSecondaryIds "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSecondaryIds)) |
| Description | Number of Secondary ID for an NCT |

| Index Field | protocolSection.identificationModule.**briefTitle** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BriefTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BriefTitle)) |
| Definition | [Brief Title](https://clinicaltrials.gov/policy/protocol-definitions#BriefTitle "Brief Title") (https://clinicaltrials.gov/policy/protocol-definitions#BriefTitle) |

| Index Field | protocolSection.identificationModule.**officialTitle** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OfficialTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OfficialTitle)) |
| Definition | [Official Title](https://clinicaltrials.gov/policy/protocol-definitions#OfficialTitle "Official Title") (https://clinicaltrials.gov/policy/protocol-definitions#OfficialTitle) |

| Index Field | protocolSection.identificationModule.**acronym** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=Acronym "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=Acronym)) |
| Definition | [Acronym](https://clinicaltrials.gov/policy/protocol-definitions#Acronym "Acronym") (https://clinicaltrials.gov/policy/protocol-definitions#Acronym) |

## Organization

| Index Field | protocolSection.identificationModule.**organization** |
| --- | --- |
| Data Type | Organization |

| Index Field | protocolSection.identificationModule.organization.**class** |
| --- | --- |
| Data Type |  |
| Description | Organization type |

| Index Field | protocolSection.**statusModule** |
| --- | --- |
| Data Type | StatusModule |
| Definition | [Study Status](https://clinicaltrials.gov/policy/protocol-definitions#status "Study Status") (https://clinicaltrials.gov/policy/protocol-definitions#status) |

| Index Field | protocolSection.statusModule.**statusVerifiedDate** |
| --- | --- |
| Data Type | PartialDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StatusVerifiedDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StatusVerifiedDate)) |
| Definition | [Record Verification Date](https://clinicaltrials.gov/policy/protocol-definitions#VerificationDate "Record Verification Date") (https://clinicaltrials.gov/policy/protocol-definitions#VerificationDate) |

| Index Field | protocolSection.statusModule.**overallStatus** |
| --- | --- |
| Data Type |  |
| Definition | [Overall Recruitment Status](https://clinicaltrials.gov/policy/protocol-definitions#OverallStatus "Overall Recruitment Status") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStatus) |

| Index Field | protocolSection.statusModule.**lastKnownStatus** |
| --- | --- |
| Data Type |  |
| Description | A study on ClinicalTrials.gov whose last known status was recruiting; not yet recruiting; or active, not recruiting but that has passed its completion date, and the status has not been last verified within the past 2 years. |

| Index Field | protocolSection.statusModule.**delayedPosting** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DelayedPosting "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DelayedPosting)) |
| Definition | [Post Prior to U.S. FDA Approval or Clearance](https://clinicaltrials.gov/policy/protocol-definitions#PostPriorFDAAApproval "Post Prior to U.S. FDA Approval or Clearance") (https://clinicaltrials.gov/policy/protocol-definitions#PostPriorFDAAApproval) |

| Index Field | protocolSection.statusModule.**whyStopped** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=WhyStopped "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=WhyStopped)) |
| Definition | [Why Study Stopped](https://clinicaltrials.gov/policy/protocol-definitions#WhyStudyStopped "Why Study Stopped") (https://clinicaltrials.gov/policy/protocol-definitions#WhyStudyStopped) |

| Index Field | protocolSection.statusModule.expandedAccessInfo.**nctId** |
| --- | --- |
| Data Type | nct ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpandedAccessNCTId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpandedAccessNCTId)) |
| Definition | [Expanded Access Record NCT Number](https://clinicaltrials.gov/policy/protocol-definitions#EANCTNumber "Expanded Access Record NCT Number") (https://clinicaltrials.gov/policy/protocol-definitions#EANCTNumber) |

| Index Field | protocolSection.statusModule.expandedAccessInfo.**statusForNctId** |
| --- | --- |
| Data Type |  |
| Description | recruitment status of the EA study that's linked to INT/OBS |

## StartDateStruct

| Index Field | protocolSection.statusModule.**startDateStruct** |
| --- | --- |
| Data Type | PartialDateStruct |

| Index Field | protocolSection.statusModule.startDateStruct.**date** |
| --- | --- |
| Data Type | PartialDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StartDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StartDate)) |
| Definition | [Study Start Date](https://clinicaltrials.gov/policy/protocol-definitions#StartDate "Study Start Date") (https://clinicaltrials.gov/policy/protocol-definitions#StartDate) |

| Index Field | protocolSection.statusModule.startDateStruct.**type** |
| --- | --- |
| Data Type |  |
| Description | Date Type |

## PrimaryCompletionDateStruct

| Index Field | protocolSection.statusModule.**primaryCompletionDateStruct** |
| --- | --- |
| Data Type | PartialDateStruct |

| Index Field | protocolSection.statusModule.primaryCompletionDateStruct.**date** |
| --- | --- |
| Data Type | PartialDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryCompletionDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryCompletionDate)) |
| Definition | [Primary Completion Date](https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate "Primary Completion Date") (https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate) |

| Index Field | protocolSection.statusModule.primaryCompletionDateStruct.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Primary Completion Date](https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate "Primary Completion Date") (https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate) |

## CompletionDateStruct

| Index Field | protocolSection.statusModule.**completionDateStruct** |
| --- | --- |
| Data Type | PartialDateStruct |

| Index Field | protocolSection.statusModule.completionDateStruct.**date** |
| --- | --- |
| Data Type | PartialDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CompletionDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CompletionDate)) |
| Definition | [Study Completion Date](https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate "Study Completion Date") (https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate) |

| Index Field | protocolSection.statusModule.completionDateStruct.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Study Completion Date](https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate "Study Completion Date") (https://clinicaltrials.gov/policy/protocol-definitions#LastFollowUpDate) |

| Index Field | protocolSection.statusModule.**studyFirstSubmitDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitDate)) |
| Description | The date on which the study sponsor or investigator first submitted a study record to ClinicalTrials.gov. There is typically a delay of a few days between the first submitted date and the record's availability on ClinicalTrials.gov (the first posted date). |

| Index Field | protocolSection.statusModule.**studyFirstSubmitYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitYear)) |

| Index Field | protocolSection.statusModule.**studyFirstSubmitQcDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitQCDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstSubmitQCDate)) |
| Description | The date on which the study sponsor or investigator first submits a study record that is consistent with National Library of Medicine (NLM) quality control (QC) review criteria. The sponsor or investigator may need to revise and submit a study record one or more times before NLM's QC review criteria are met. It is the responsibility of the sponsor or investigator to ensure that the study record is consistent with the NLM QC review criteria. |

| Index Field | protocolSection.statusModule.studyFirstPostDateStruct.**studyFirstPostYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstPostYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyFirstPostYear)) |

## ResultsWaived

| Index Field | protocolSection.statusModule.**resultsWaived** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsWaived "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsWaived)) |

| Index Field | protocolSection.statusModule.**resultsFirstSubmitDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitDate)) |
| Description | The date on which the study sponsor or investigator first submits a study record with summary results information. There is typically a delay between the results first submitted date and when summary results information becomes available on ClinicalTrials.gov (the results first posted date). |

| Index Field | protocolSection.statusModule.**resultsFirstSubmitYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitYear)) |

| Index Field | protocolSection.statusModule.**resultsFirstSubmitQcDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitQCDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstSubmitQCDate)) |
| Description | The date on which the study sponsor or investigator first submits a study record with summary results information that is consistent with National Library of Medicine (NLM) quality control (QC) review criteria. The sponsor or investigator may need to revise and submit results information one or more times before NLM's QC review criteria are met. It is the responsibility of the sponsor or investigator to ensure that the study record is consistent with the NLM QC review criteria. |

| Index Field | protocolSection.statusModule.resultsFirstPostDateStruct.**resultsFirstPostYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstPostYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResultsFirstPostYear)) |

| Index Field | protocolSection.statusModule.**dispFirstSubmitDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitDate)) |
| Description | The date on which the study sponsor or investigator first submitted a certification or an extension request to delay submission of results. A sponsor or investigator who submits a certification can delay results submission up to 2 years after this date, unless certain events occur sooner. There is typically a delay between the date the certification or extension request was submitted and the date the information is first available on ClinicalTrials.gov (certification/extension first posted). |

| Index Field | protocolSection.statusModule.**dispFirstSubmitYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitYear)) |

| Index Field | protocolSection.statusModule.**dispFirstSubmitQcDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitQCDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstSubmitQCDate)) |
| Description | Certification/extension first submitted that met QC criteria |

| Index Field | protocolSection.statusModule.dispFirstPostDateStruct.**dispFirstPostYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstPostYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DispFirstPostYear)) |

| Index Field | protocolSection.statusModule.**lastUpdateSubmitDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdateSubmitDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdateSubmitDate)) |
| Description | The most recent date on which the study sponsor or investigator submitted changes to a study record to ClinicalTrials.gov. There is typically a delay of a few days between the last update submitted date and when the date changes are posted on ClinicalTrials.gov (the last update posted date). |

| Index Field | protocolSection.statusModule.**lastUpdateSubmitYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdateSubmitYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdateSubmitYear)) |

| Index Field | protocolSection.statusModule.lastUpdatePostDateStruct.**lastUpdatePostYear** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdatePostYear "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LastUpdatePostYear)) |
| Description | Study record last update posted year on public site |

| Index Field | protocolSection.sponsorCollaboratorsModule.**responsibleParty** |
| --- | --- |
| Data Type | ResponsibleParty |
| Definition | [Investigator Information](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorInfo "Investigator Information") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorInfo) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Responsible Party, by Official Title](https://clinicaltrials.gov/policy/protocol-definitions#RespParty "Responsible Party, by Official Title") (https://clinicaltrials.gov/policy/protocol-definitions#RespParty) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**investigatorFullName** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorFullName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorFullName)) |
| Definition | [Investigator Name](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorName "Investigator Name") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorName) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**investigatorTitle** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorTitle)) |
| Definition | [Investigator Official Title](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorOfTitle "Investigator Official Title") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorOfTitle) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**investigatorAffiliation** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorAffiliation "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyInvestigatorAffiliation)) |
| Definition | [Investigator Affiliation](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorAffil "Investigator Affiliation") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorAffil) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**oldNameTitle** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyOldNameTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyOldNameTitle)) |
| Definition | [Investigator Name](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorName "Investigator Name") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorName) |

| Index Field | protocolSection.sponsorCollaboratorsModule.responsibleParty.**oldOrganization** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyOldOrganization "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ResponsiblePartyOldOrganization)) |
| Definition | [Investigator Affiliation](https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorAffil "Investigator Affiliation") (https://clinicaltrials.gov/policy/protocol-definitions#InvestigatorAffil) |

| Index Field | protocolSection.sponsorCollaboratorsModule.**collaborators** |
| --- | --- |
| Data Type | Sponsor\[\] |
| Description | Other organizations, if any, providing support. Support may include funding, design, implementation, data analysis or reporting. The responsible party is responsible for confirming all collaborators before listing them |

| Index Field | protocolSection.sponsorCollaboratorsModule.collaborators.**name** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CollaboratorName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CollaboratorName)) |
| Definition | [Collaborators](https://clinicaltrials.gov/policy/protocol-definitions#Collaborators "Collaborators") (https://clinicaltrials.gov/policy/protocol-definitions#Collaborators) |

| Index Field | protocolSection.sponsorCollaboratorsModule.collaborators.**class** |
| --- | --- |
| Data Type |  |
| Description | Type of collaborator |

| Index Field | protocolSection.sponsorCollaboratorsModule.**numCollaborators** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumCollaborators "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumCollaborators)) |
| Description | number of collaborators |

| Index Field | protocolSection.**oversightModule** |
| --- | --- |
| Data Type | OversightModule |
| Definition | [Oversight](https://clinicaltrials.gov/policy/protocol-definitions#oversight "Oversight") (https://clinicaltrials.gov/policy/protocol-definitions#oversight) |

| Index Field | protocolSection.oversightModule.**oversightHasDmc** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OversightHasDMC "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OversightHasDMC)) |
| Definition | [Data Monitoring Committee](https://clinicaltrials.gov/policy/protocol-definitions#hasDMC "Data Monitoring Committee") (https://clinicaltrials.gov/policy/protocol-definitions#hasDMC) |

| Index Field | protocolSection.oversightModule.**isFdaRegulatedDrug** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsFDARegulatedDrug "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsFDARegulatedDrug)) |
| Definition | [Studies a U.S. FDA-regulated Drug Product](https://clinicaltrials.gov/policy/protocol-definitions#FDADrugProduct "Studies a U.S. FDA-regulated Drug Product") (https://clinicaltrials.gov/policy/protocol-definitions#FDADrugProduct) |

| Index Field | protocolSection.oversightModule.**isFdaRegulatedDevice** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsFDARegulatedDevice "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsFDARegulatedDevice)) |
| Definition | [Studies a U.S. FDA-regulated Device Product](https://clinicaltrials.gov/policy/protocol-definitions#FDAReg "Studies a U.S. FDA-regulated Device Product") (https://clinicaltrials.gov/policy/protocol-definitions#FDAReg) |

| Index Field | protocolSection.oversightModule.**isUnapprovedDevice** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsUnapprovedDevice "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsUnapprovedDevice)) |
| Definition | [Device Product Not Approved or Cleared by U.S. FDA](https://clinicaltrials.gov/policy/protocol-definitions#deviceNotCleared "Device Product Not Approved or Cleared by U.S. FDA") (https://clinicaltrials.gov/policy/protocol-definitions#deviceNotCleared) |

| Index Field | protocolSection.oversightModule.**isPpsd** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsPPSD "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsPPSD)) |
| Definition | [Pediatric Postmarket Surveillance of a Device Product](https://clinicaltrials.gov/policy/protocol-definitions#PediatricPostmarket "Pediatric Postmarket Surveillance of a Device Product") (https://clinicaltrials.gov/policy/protocol-definitions#PediatricPostmarket) |

| Index Field | protocolSection.oversightModule.**isUsExport** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsUSExport "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IsUSExport)) |
| Definition | [Product Manufactured in and Exported from the U.S.](https://clinicaltrials.gov/policy/protocol-definitions#ProductFromUS "Product Manufactured in and Exported from the U.S.")(https://clinicaltrials.gov/policy/protocol-definitions#ProductFromUS) |

## FDAAA801Violation

| Index Field | protocolSection.oversightModule.**fdaaa801Violation** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FDAAA801Violation "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FDAAA801Violation)) |

| Index Field | protocolSection.**descriptionModule** |
| --- | --- |
| Data Type | DescriptionModule |
| Definition | [Study Description](https://clinicaltrials.gov/policy/protocol-definitions#description "Study Description") (https://clinicaltrials.gov/policy/protocol-definitions#description) |

| Index Field | protocolSection.descriptionModule.**briefSummary** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BriefSummary "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BriefSummary)) |
| Definition | [Brief Summary](https://clinicaltrials.gov/policy/protocol-definitions#BriefSummary "Brief Summary") (https://clinicaltrials.gov/policy/protocol-definitions#BriefSummary) |

| Index Field | protocolSection.descriptionModule.**detailedDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DetailedDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DetailedDescription)) |
| Definition | [Detailed Description](https://clinicaltrials.gov/policy/protocol-definitions#DetailedDescription "Detailed Description") (https://clinicaltrials.gov/policy/protocol-definitions#DetailedDescription) |

| Index Field | protocolSection.**conditionsModule** |
| --- | --- |
| Data Type | ConditionsModule |
| Definition | [Conditions and Keywords](https://clinicaltrials.gov/policy/protocol-definitions#Conditions "Conditions and Keywords") (https://clinicaltrials.gov/policy/protocol-definitions#Conditions) |

| Index Field | protocolSection.conditionsModule.**conditions** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=Condition "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=Condition)) |
| Definition | [Primary Disease or Condition Being Studied in the Trial, or the Focus of the Study](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryCondition "Primary Disease or Condition Being Studied in the Trial, or the Focus of the Study") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryCondition) |

| Index Field | protocolSection.conditionsModule.**numConditions** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditions "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditions)) |

| Index Field | protocolSection.conditionsModule.**keywords** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=Keyword "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=Keyword)) |
| Definition | [Keywords](https://clinicaltrials.gov/policy/protocol-definitions#Keywords "Keywords") (https://clinicaltrials.gov/policy/protocol-definitions#Keywords) |

| Index Field | protocolSection.conditionsModule.**numKeywords** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumKeywords "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumKeywords)) |

| Index Field | protocolSection.**designModule** |
| --- | --- |
| Data Type | DesignModule |
| Definition | [Study Design](https://clinicaltrials.gov/policy/protocol-definitions#StudyDesign "Study Design") (https://clinicaltrials.gov/policy/protocol-definitions#StudyDesign) |

| Index Field | protocolSection.designModule.**studyType** |
| --- | --- |
| Data Type |  |
| Definition | [Study Type](https://clinicaltrials.gov/policy/protocol-definitions#StudyType "Study Type") (https://clinicaltrials.gov/policy/protocol-definitions#StudyType) |

| Index Field | protocolSection.designModule.**nPtrsToThisExpAccNctId** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NPtrsToThisExpAccNCTId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NPtrsToThisExpAccNCTId)) |
| Description | Number of studies that reference this EA study |

| Index Field | protocolSection.designModule.**expandedAccessTypes** |
| --- | --- |
| Data Type | ExpandedAccessTypes |
| Description | The type(s) of expanded access for which the investigational drug product (including a biological product) is available, as specified in U.S. FDA regulations |

| Index Field | protocolSection.designModule.expandedAccessTypes.**individual** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeIndividual "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeIndividual)) |
| Description | For individual participants, including for emergency use, as specified in 21 CFR 312.310. Allows a single patient, with a serious disease or condition who cannot participate in a clinical trial, access to a drug or biological product that has not been approved by the FDA. This category also includes access in an emergency situation. |

| Index Field | protocolSection.designModule.expandedAccessTypes.**intermediate** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeIntermediate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeIntermediate)) |
| Description | For intermediate-size participant populations, as specified in 21 CFR 312.315. Allows more than one patient (but generally fewer patients than through a Treatment IND/Protocol) access to a drug or biological product that has not been approved by the FDA. This type of expanded access is used when multiple patients with the same disease or condition seek access to a specific drug or biological product that has not been approved by the FDA. |

| Index Field | protocolSection.designModule.expandedAccessTypes.**treatment** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeTreatment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ExpAccTypeTreatment)) |
| Description | Under a treatment IND or treatment protocol, as specified in 21 CFR 312.320. Allows a large, widespread population access to a drug or biological product that has not been approved by the FDA. This type of expanded access can only be provided if the product is already being developed for marketing for the same use as the expanded access use. |

| Index Field | protocolSection.designModule.**patientRegistry** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PatientRegistry "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PatientRegistry)) |
| Description | A type of observational study that collects information about patients' medical conditions and/or treatments to better understand how a condition or treatment affects patients in the real world. |

| Index Field | protocolSection.designModule.**targetDuration** |
| --- | --- |
| Data Type | NormalizedTime ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=TargetDuration "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=TargetDuration)) |
| Definition | [Target Follow-Up Duration](https://clinicaltrials.gov/policy/protocol-definitions#RoPR "Target Follow-Up Duration") (https://clinicaltrials.gov/policy/protocol-definitions#RoPR) |

| Index Field | protocolSection.designModule.**phases** |
| --- | --- |
| Data Type |  |
| Definition | [Study Phase](https://clinicaltrials.gov/policy/protocol-definitions#StudyPhase "Study Phase") (https://clinicaltrials.gov/policy/protocol-definitions#StudyPhase) |

| Index Field | protocolSection.designModule.**numPhases** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumPhases "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumPhases)) |
| Description | Indicate which phase(s) the study is in |

| Index Field | protocolSection.designModule.designInfo.**interventionModelDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DesignInterventionModelDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DesignInterventionModelDescription)) |
| Definition | [Model Description](https://clinicaltrials.gov/policy/protocol-definitions#ModelDesc "Model Description") (https://clinicaltrials.gov/policy/protocol-definitions#ModelDesc) |

| Index Field | protocolSection.designModule.designInfo.**primaryPurpose** |
| --- | --- |
| Data Type |  |
| Definition | [Primary Purpose](https://clinicaltrials.gov/policy/protocol-definitions#IntPurpose "Primary Purpose") (https://clinicaltrials.gov/policy/protocol-definitions#IntPurpose) |

| Index Field | protocolSection.designModule.designInfo.**observationalModel** |
| --- | --- |
| Data Type |  |
| Definition | [Observational Study Model](https://clinicaltrials.gov/policy/protocol-definitions#ObsStudyModel "Observational Study Model") (https://clinicaltrials.gov/policy/protocol-definitions#ObsStudyModel) |

| Index Field | protocolSection.designModule.designInfo.**timePerspective** |
| --- | --- |
| Data Type |  |
| Definition | [Time Perspective](https://clinicaltrials.gov/policy/protocol-definitions#ObsTiming "Time Perspective") (https://clinicaltrials.gov/policy/protocol-definitions#ObsTiming) |

## DesignMaskingInfo

| Index Field | protocolSection.designModule.designInfo.**maskingInfo** |
| --- | --- |
| Data Type | MaskingBlock |

| Index Field | protocolSection.designModule.designInfo.maskingInfo.**maskingDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=DesignMaskingDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=DesignMaskingDescription)) |
| Definition | [Masking Description](https://clinicaltrials.gov/policy/protocol-definitions#MaskingDesc "Masking Description") (https://clinicaltrials.gov/policy/protocol-definitions#MaskingDesc) |

## NumDesignWhoMaskeds

| Index Field | protocolSection.designModule.designInfo.maskingInfo.**numDesignWhoMaskeds** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumDesignWhoMaskeds "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumDesignWhoMaskeds)) |

## BioSpec

| Index Field | protocolSection.designModule.**bioSpec** |
| --- | --- |
| Data Type | BioSpec |

| Index Field | protocolSection.designModule.bioSpec.**retention** |
| --- | --- |
| Data Type |  |
| Definition | [Biospecimen Retention](https://clinicaltrials.gov/policy/protocol-definitions#ObsBiospecimenRetention "Biospecimen Retention") (https://clinicaltrials.gov/policy/protocol-definitions#ObsBiospecimenRetention) |

| Index Field | protocolSection.designModule.bioSpec.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BioSpecDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BioSpecDescription)) |
| Definition | [Biospecimen Description](https://clinicaltrials.gov/policy/protocol-definitions#ObsBiospecimenDescription "Biospecimen Description") (https://clinicaltrials.gov/policy/protocol-definitions#ObsBiospecimenDescription) |

## EnrollmentInfo

| Index Field | protocolSection.designModule.**enrollmentInfo** |
| --- | --- |
| Data Type | EnrollmentInfo |

| Index Field | protocolSection.designModule.enrollmentInfo.**count** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EnrollmentCount "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EnrollmentCount)) |
| Definition | [Enrollment](https://clinicaltrials.gov/policy/protocol-definitions#IntEnrollment "Enrollment") (https://clinicaltrials.gov/policy/protocol-definitions#IntEnrollment) |

## ArmsInterventionsModule

| Index Field | protocolSection.**armsInterventionsModule** |
| --- | --- |
| Data Type | ArmsInterventionsModule |
| Definition | [Arms, Groups, and Interventions](https://clinicaltrials.gov/policy/protocol-definitions#ArmsGroupsInterventions "Arms, Groups, and Interventions") (https://clinicaltrials.gov/policy/protocol-definitions#ArmsGroupsInterventions) |

| Index Field | protocolSection.armsInterventionsModule.**armGroups** |
| --- | --- |
| Data Type | ArmGroup\[\] |
| Definition | [Arm Information](https://clinicaltrials.gov/policy/protocol-definitions#Arms "Arm Information") (https://clinicaltrials.gov/policy/protocol-definitions#Arms) |

| Index Field | protocolSection.armsInterventionsModule.armGroups.**label** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupLabel "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupLabel)) |
| Definition | [Arm Title](https://clinicaltrials.gov/policy/protocol-definitions#ArmLabel "Arm Title") (https://clinicaltrials.gov/policy/protocol-definitions#ArmLabel) |

| Index Field | protocolSection.armsInterventionsModule.armGroups.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Arm Type](https://clinicaltrials.gov/policy/protocol-definitions#ArmType "Arm Type") (https://clinicaltrials.gov/policy/protocol-definitions#ArmType) |

| Index Field | protocolSection.armsInterventionsModule.armGroups.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupDescription)) |
| Definition | [Arm Description](https://clinicaltrials.gov/policy/protocol-definitions#ArmDescription "Arm Description") (https://clinicaltrials.gov/policy/protocol-definitions#ArmDescription) |

| Index Field | protocolSection.armsInterventionsModule.armGroups.**interventionNames** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupInterventionName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ArmGroupInterventionName)) |
| Definition | [Arm/Group Intervention Name(s)](https://clinicaltrials.gov/policy/protocol-definitions#InterventionName "Arm/Group Intervention Name(s)") (https://clinicaltrials.gov/policy/protocol-definitions#InterventionName) |

## NumArmGroupInterventionNames

| Index Field | protocolSection.armsInterventionsModule.armGroups.**numArmGroupInterventionNames** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumArmGroupInterventionNames "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumArmGroupInterventionNames)) |

## NumArmGroups

| Index Field | protocolSection.armsInterventionsModule.**numArmGroups** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumArmGroups "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumArmGroups)) |
| Definition | [Number of Arms](https://clinicaltrials.gov/policy/protocol-definitions#NumberOfArms "Number of Arms") (https://clinicaltrials.gov/policy/protocol-definitions#NumberOfArms) |

| Index Field | protocolSection.armsInterventionsModule.**interventions** |
| --- | --- |
| Data Type | Intervention\[\] |
| Definition | [Interventions](https://clinicaltrials.gov/policy/protocol-definitions#Interventions "Interventions") (https://clinicaltrials.gov/policy/protocol-definitions#Interventions) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Intervention Type](https://clinicaltrials.gov/policy/protocol-definitions#InterventionType "Intervention Type") (https://clinicaltrials.gov/policy/protocol-definitions#InterventionType) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**name** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionName)) |
| Definition | [Intervention Name(s)](https://clinicaltrials.gov/policy/protocol-definitions#InterventionName "Intervention Name(s)") (https://clinicaltrials.gov/policy/protocol-definitions#InterventionName) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionDescription)) |
| Definition | [Intervention Description](https://clinicaltrials.gov/policy/protocol-definitions#InterventionDescription "Intervention Description") (https://clinicaltrials.gov/policy/protocol-definitions#InterventionDescription) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**armGroupLabels** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionArmGroupLabel "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionArmGroupLabel)) |
| Description | Arm/Group and Intervention Cross Reference |

## NumInterventionArmGroupLabels

| Index Field | protocolSection.armsInterventionsModule.interventions.**numInterventionArmGroupLabels** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionArmGroupLabels "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionArmGroupLabels)) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**otherNames** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionOtherName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionOtherName)) |
| Definition | [Other Intervention Name(s)](https://clinicaltrials.gov/policy/protocol-definitions#InterventionOtherName "Other Intervention Name(s)") (https://clinicaltrials.gov/policy/protocol-definitions#InterventionOtherName) |

| Index Field | protocolSection.armsInterventionsModule.interventions.**numInterventionOtherNames** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionOtherNames "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionOtherNames)) |

| Index Field | protocolSection.armsInterventionsModule.**numInterventions** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventions "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventions)) |

| Index Field | protocolSection.**outcomesModule** |
| --- | --- |
| Data Type | OutcomesModule |
| Definition | [Outcome Measures](https://clinicaltrials.gov/policy/protocol-definitions#Outcomes "Outcome Measures") (https://clinicaltrials.gov/policy/protocol-definitions#Outcomes) |

## PrimaryOutcome

| Index Field | protocolSection.outcomesModule.**primaryOutcomes** |
| --- | --- |
| Data Type | Outcome\[\] |
| Definition | [Primary Outcome Measure Information](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMInfo "Primary Outcome Measure Information") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMInfo) |

| Index Field | protocolSection.outcomesModule.primaryOutcomes.**measure** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeMeasure "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeMeasure)) |
| Definition | [Title](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMTitle "Title") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMTitle) |

| Index Field | protocolSection.outcomesModule.primaryOutcomes.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeDescription)) |
| Definition | [Description](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMDescription "Description") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMDescription) |

| Index Field | protocolSection.outcomesModule.primaryOutcomes.**timeFrame** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PrimaryOutcomeTimeFrame)) |
| Definition | [Time Frame](https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMTimeFrame "Time Frame") (https://clinicaltrials.gov/policy/protocol-definitions#PrimaryOMTimeFrame) |

| Index Field | protocolSection.outcomesModule.**numPrimaryOutcomes** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumPrimaryOutcomes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumPrimaryOutcomes)) |

| Index Field | protocolSection.outcomesModule.**secondaryOutcomes** |
| --- | --- |
| Data Type | Outcome\[\] |
| Definition | [Secondary Outcome Measure Information](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMInfo "Secondary Outcome Measure Information") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMInfo) |

| Index Field | protocolSection.outcomesModule.secondaryOutcomes.**measure** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeMeasure "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeMeasure)) |
| Definition | [Title](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMTitle "Title") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMTitle) |

| Index Field | protocolSection.outcomesModule.secondaryOutcomes.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeDescription)) |
| Definition | [Description](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMDescription "Description") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMDescription) |

| Index Field | protocolSection.outcomesModule.secondaryOutcomes.**timeFrame** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SecondaryOutcomeTimeFrame)) |
| Definition | [Time Frame](https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMTimeFrame "Time Frame") (https://clinicaltrials.gov/policy/protocol-definitions#SecondaryOMTimeFrame) |

| Index Field | protocolSection.outcomesModule.**numSecondaryOutcomes** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSecondaryOutcomes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSecondaryOutcomes)) |

| Index Field | protocolSection.outcomesModule.**otherOutcomes** |
| --- | --- |
| Data Type | Outcome\[\] |
| Definition | [Other Pre-specified Outcome Measures](https://clinicaltrials.gov/policy/protocol-definitions#OtherOMInfo "Other Pre-specified Outcome Measures") (https://clinicaltrials.gov/policy/protocol-definitions#OtherOMInfo) |

| Index Field | protocolSection.outcomesModule.otherOutcomes.**measure** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeMeasure "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeMeasure)) |
| Definition | [Title](https://clinicaltrials.gov/policy/protocol-definitions#OtherOMTitle "Title") (https://clinicaltrials.gov/policy/protocol-definitions#OtherOMTitle) |

| Index Field | protocolSection.outcomesModule.otherOutcomes.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeDescription)) |
| Definition | [Description](https://clinicaltrials.gov/policy/protocol-definitions#OtherOMDescription "Description") (https://clinicaltrials.gov/policy/protocol-definitions#OtherOMDescription) |

| Index Field | protocolSection.outcomesModule.otherOutcomes.**timeFrame** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherOutcomeTimeFrame)) |
| Definition | [Time Frame](https://clinicaltrials.gov/policy/protocol-definitions#OtherOMTimeFrame "Time Frame") (https://clinicaltrials.gov/policy/protocol-definitions#OtherOMTimeFrame) |

| Index Field | protocolSection.outcomesModule.**numOtherOutcomes** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherOutcomes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherOutcomes)) |

| Index Field | protocolSection.outcomesModule.**numOutcomes** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomes)) |

## EligibilityModule

| Index Field | protocolSection.**eligibilityModule** |
| --- | --- |
| Data Type | EligibilityModule |
| Definition | [Eligibility](https://clinicaltrials.gov/policy/protocol-definitions#Eligibility "Eligibility") (https://clinicaltrials.gov/policy/protocol-definitions#Eligibility) |

| Index Field | protocolSection.eligibilityModule.**eligibilityCriteria** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EligibilityCriteria "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EligibilityCriteria)) |
| Definition | [Eligibility Criteria](https://clinicaltrials.gov/policy/protocol-definitions#EligibilityCriteria "Eligibility Criteria") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilityCriteria) |

| Index Field | protocolSection.eligibilityModule.**healthyVolunteers** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=HealthyVolunteers "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=HealthyVolunteers)) |
| Definition | [Accepts Healthy Volunteers](https://clinicaltrials.gov/policy/protocol-definitions#HealthyVolunteers "Accepts Healthy Volunteers") (https://clinicaltrials.gov/policy/protocol-definitions#HealthyVolunteers) |

| Index Field | protocolSection.eligibilityModule.**genderBased** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=GenderBased "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=GenderBased)) |
| Definition | [Gender Based](https://clinicaltrials.gov/policy/protocol-definitions#EligibilityGender "Gender Based") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilityGender) |

| Index Field | protocolSection.eligibilityModule.**genderDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=GenderDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=GenderDescription)) |
| Definition | [Gender Eligibility Description](https://clinicaltrials.gov/policy/protocol-definitions#GenderDescription "Gender Eligibility Description") (https://clinicaltrials.gov/policy/protocol-definitions#GenderDescription) |

| Index Field | protocolSection.eligibilityModule.**minimumAge** |
| --- | --- |
| Data Type | NormalizedTime ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=MinimumAge "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=MinimumAge)) |
| Definition | [Minimum Age](https://clinicaltrials.gov/policy/protocol-definitions#EligibilityMinAge "Minimum Age") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilityMinAge) |

| Index Field | protocolSection.eligibilityModule.**maximumAge** |
| --- | --- |
| Data Type | NormalizedTime ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=MaximumAge "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=MaximumAge)) |
| Definition | [Maximum Age](https://clinicaltrials.gov/policy/protocol-definitions#EligibilityMaxAge "Maximum Age") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilityMaxAge) |

| Index Field | protocolSection.eligibilityModule.**stdAges** |
| --- | --- |
| Data Type |  |
| Description | Ingest calculated the StdAge if there is minimumAge and/or maximimumAge entered. Redacted for Withheld studies |

| Index Field | protocolSection.eligibilityModule.**numStdAges** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumStdAges "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumStdAges)) |

| Index Field | protocolSection.eligibilityModule.**studyPopulation** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyPopulation "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=StudyPopulation)) |
| Definition | [Study Population Description (For observational studies only)](https://clinicaltrials.gov/policy/protocol-definitions#EligibilityStudyPopulation "Study Population Description (For observational studies only)") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilityStudyPopulation) |

| Index Field | protocolSection.eligibilityModule.**samplingMethod** |
| --- | --- |
| Data Type |  |
| Definition | [Sampling Method (For observational studies only)](https://clinicaltrials.gov/policy/protocol-definitions#EligibilitySamplingMethod "Sampling Method (For observational studies only)") (https://clinicaltrials.gov/policy/protocol-definitions#EligibilitySamplingMethod) |

| Index Field | protocolSection.**contactsLocationsModule** |
| --- | --- |
| Data Type | ContactsLocationsModule |
| Definition | [Contacts, Locations, and Investigator Information](https://clinicaltrials.gov/policy/protocol-definitions#Locations "Contacts, Locations, and Investigator Information") (https://clinicaltrials.gov/policy/protocol-definitions#Locations) |

| Index Field | protocolSection.contactsLocationsModule.**centralContacts** |
| --- | --- |
| Data Type | Contact\[\] |
| Definition | [Central Contact Person or Optional Central Contact Backup](https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact "Central Contact Person or Optional Central Contact Backup") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact) |

| Index Field | protocolSection.contactsLocationsModule.centralContacts.**name** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactName)) |
| Definition | [First Name & Middle Initial & Last Name or Official Title & Degree](https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact "First Name & Middle Initial & Last Name or Official Title & Degree") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact) |

| Index Field | protocolSection.contactsLocationsModule.centralContacts.**role** |
| --- | --- |
| Data Type |  |
| Description | Role for any Central Contact added |

| Index Field | protocolSection.contactsLocationsModule.centralContacts.**phone** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactPhone "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactPhone)) |
| Definition | [Phone](https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact "Phone") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact) |

| Index Field | protocolSection.contactsLocationsModule.centralContacts.**phoneExt** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactPhoneExt "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactPhoneExt)) |
| Definition | [Ext](https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact "Ext") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact) |

| Index Field | protocolSection.contactsLocationsModule.centralContacts.**email** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactEMail "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=CentralContactEMail)) |
| Definition | [Email](https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact "Email") (https://clinicaltrials.gov/policy/protocol-definitions#OverallStudyContact) |

| Index Field | protocolSection.contactsLocationsModule.**numCentralContacts** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumCentralContacts "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumCentralContacts)) |

| Index Field | protocolSection.contactsLocationsModule.**overallOfficials** |
| --- | --- |
| Data Type | Official\[\] |
| Definition | [Overall Study Officials](https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials "Overall Study Officials") (https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials) |

| Index Field | protocolSection.contactsLocationsModule.overallOfficials.**name** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OverallOfficialName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OverallOfficialName)) |
| Definition | [First Name & Middle Initial & Last Name & Degree](https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials "First Name & Middle Initial & Last Name & Degree") (https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials) |

| Index Field | protocolSection.contactsLocationsModule.overallOfficials.**affiliation** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OverallOfficialAffiliation "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OverallOfficialAffiliation)) |
| Definition | [Organizational Affiliation](https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials "Organizational Affiliation") (https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials) |

| Index Field | protocolSection.contactsLocationsModule.overallOfficials.**role** |
| --- | --- |
| Data Type |  |
| Definition | [Official's Role](https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials "Official's Role") (https://clinicaltrials.gov/policy/protocol-definitions#StudyOfficials) |

| Index Field | protocolSection.contactsLocationsModule.**numOverallOfficials** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOverallOfficials "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOverallOfficials)) |

| Index Field | protocolSection.contactsLocationsModule.**locations** ⤷ |
| --- | --- |
| Data Type | Location\[\] |
| Definition | [Facility Information](https://clinicaltrials.gov/policy/protocol-definitions#Facility "Facility Information") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**facility** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationFacility "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationFacility)) |
| Definition | [Facility Name](https://clinicaltrials.gov/policy/protocol-definitions#Facility "Facility Name") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**status** |
| --- | --- |
| Data Type |  |
| Definition | [Individual Site Status](https://clinicaltrials.gov/policy/protocol-definitions#FacilityStatus "Individual Site Status") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityStatus) |

| Index Field | protocolSection.contactsLocationsModule.locations.**city** |
| --- | --- |
| Data Type | GeoName ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCity "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCity)) |
| Definition | [City](https://clinicaltrials.gov/policy/protocol-definitions#Facility "City") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**state** |
| --- | --- |
| Data Type | GeoName ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationState "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationState)) |
| Definition | [State/Province](https://clinicaltrials.gov/policy/protocol-definitions#Facility "State/Province") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**zip** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationZip "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationZip)) |
| Definition | [ZIP/Postal Code](https://clinicaltrials.gov/policy/protocol-definitions#Facility "ZIP/Postal Code") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**country** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCountry "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCountry)) |
| Definition | [Country](https://clinicaltrials.gov/policy/protocol-definitions#Facility "Country") (https://clinicaltrials.gov/policy/protocol-definitions#Facility) |

| Index Field | protocolSection.contactsLocationsModule.locations.**contacts** |
| --- | --- |
| Data Type | Contact\[\] |
| Definition | [Facility Contact or Facility Contact Backup](https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact "Facility Contact or Facility Contact Backup") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact) |

| Index Field | protocolSection.contactsLocationsModule.locations.contacts.**name** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactName)) |
| Definition | [First Name & Middle Initial & Last Name & Degree](https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact "First Name & Middle Initial & Last Name & Degree") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact) |

| Index Field | protocolSection.contactsLocationsModule.locations.contacts.**phone** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactPhone "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactPhone)) |
| Definition | [Phone](https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact "Phone") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact) |

| Index Field | protocolSection.contactsLocationsModule.locations.contacts.**phoneExt** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactPhoneExt "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactPhoneExt)) |
| Definition | [Ext](https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact "Ext") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact) |

| Index Field | protocolSection.contactsLocationsModule.locations.contacts.**email** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactEMail "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationContactEMail)) |
| Definition | [Email](https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact "Email") (https://clinicaltrials.gov/policy/protocol-definitions#FacilityContact) |

| Index Field | protocolSection.contactsLocationsModule.locations.**numLocationContacts** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLocationContacts "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLocationContacts)) |

| Index Field | protocolSection.contactsLocationsModule.locations.**countryCode** ✗ |
| --- | --- |
| Data Type | keyword ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCountryCode "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationCountryCode)) |

| Index Field | protocolSection.contactsLocationsModule.locations.**geoPoint** |
| --- | --- |
| Data Type | GeoPoint ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationGeoPoint "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LocationGeoPoint)) |

| Index Field | protocolSection.contactsLocationsModule.**numLocations** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLocations "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLocations)) |

| Index Field | protocolSection.contactsLocationsModule.**numUniqueLocationCountries** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumUniqueLocationCountries "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumUniqueLocationCountries)) |

## ReferencesModule

| Index Field | protocolSection.**referencesModule** |
| --- | --- |
| Data Type | ReferencesModule |
| Definition | [References](https://clinicaltrials.gov/policy/protocol-definitions#References "References") (https://clinicaltrials.gov/policy/protocol-definitions#References) |

| Index Field | protocolSection.referencesModule.references.**pmid** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ReferencePMID "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ReferencePMID)) |
| Definition | [PubMed Identifier](https://clinicaltrials.gov/policy/protocol-definitions#PubMedId "PubMed Identifier") (https://clinicaltrials.gov/policy/protocol-definitions#PubMedId) |

| Index Field | protocolSection.referencesModule.references.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Results Reference](https://clinicaltrials.gov/policy/protocol-definitions#IsResultsRef "Results Reference") (https://clinicaltrials.gov/policy/protocol-definitions#IsResultsRef) |

| Index Field | protocolSection.referencesModule.references.**citation** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ReferenceCitation "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ReferenceCitation)) |
| Definition | [Citation](https://clinicaltrials.gov/policy/protocol-definitions#Citation "Citation") (https://clinicaltrials.gov/policy/protocol-definitions#Citation) |

## Retraction

| Index Field | protocolSection.referencesModule.references.**retractions** |
| --- | --- |
| Data Type | Retraction\[\] |

| Index Field | protocolSection.referencesModule.references.retractions.**pmid** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=RetractionPMID "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=RetractionPMID)) |
| Description | PMID for publication retraction |

## RetractionSource

| Index Field | protocolSection.referencesModule.references.retractions.**source** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=RetractionSource "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=RetractionSource)) |

| Index Field | protocolSection.referencesModule.references.**numRetractionsForRef** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRetractionsForRef "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRetractionsForRef)) |

| Index Field | protocolSection.referencesModule.**numReferences** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumReferences "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumReferences)) |

| Index Field | protocolSection.referencesModule.**numRetractionsAllRefs** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRetractionsAllRefs "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRetractionsAllRefs)) |

| Index Field | protocolSection.referencesModule.**seeAlsoLinks** |
| --- | --- |
| Data Type | SeeAlsoLink\[\] |
| Definition | [Links](https://clinicaltrials.gov/policy/protocol-definitions#Links "Links") (https://clinicaltrials.gov/policy/protocol-definitions#Links) |

| Index Field | protocolSection.referencesModule.seeAlsoLinks.**label** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeeAlsoLinkLabel "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeeAlsoLinkLabel)) |
| Definition | [Description](https://clinicaltrials.gov/policy/protocol-definitions#LinkDescription "Description") (https://clinicaltrials.gov/policy/protocol-definitions#LinkDescription) |

## SeeAlsoLinkURL

| Index Field | protocolSection.referencesModule.seeAlsoLinks.**url** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeeAlsoLinkURL "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeeAlsoLinkURL)) |
| Definition | [URL](https://clinicaltrials.gov/policy/protocol-definitions#URL "URL") (https://clinicaltrials.gov/policy/protocol-definitions#URL) |

| Index Field | protocolSection.referencesModule.**numSeeAlsoLinks** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeeAlsoLinks "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeeAlsoLinks)) |

| Index Field | protocolSection.referencesModule.**numAvailIpDs** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumAvailIPDs "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumAvailIPDs)) |

| Index Field | protocolSection.**ipdSharingStatementModule** |
| --- | --- |
| Data Type | IpdSharingStatementModule |
| Definition | [IPD Sharing Statement](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharing "IPD Sharing Statement") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharing) |

| Index Field | protocolSection.ipdSharingStatementModule.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingDescription)) |
| Definition | [IPD Sharing Plan Description](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingPlanDescription "IPD Sharing Plan Description") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingPlanDescription) |

| Index Field | protocolSection.ipdSharingStatementModule.**infoTypes** |
| --- | --- |
| Data Type |  |
| Definition | [IPD Sharing Supporting Information Type](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingSuppInfoType "IPD Sharing Supporting Information Type") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingSuppInfoType) |

| Index Field | protocolSection.ipdSharingStatementModule.**numIpdSharingInfoTypes** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumIPDSharingInfoTypes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumIPDSharingInfoTypes)) |
| Description | Number of IPD Types Selected |

| Index Field | protocolSection.ipdSharingStatementModule.**timeFrame** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingTimeFrame)) |
| Definition | [IPD Sharing Time Frame](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingTimeFrame "IPD Sharing Time Frame") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingTimeFrame) |

| Index Field | protocolSection.ipdSharingStatementModule.**accessCriteria** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingAccessCriteria "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingAccessCriteria)) |
| Definition | [IPD Sharing Access Criteria](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingAccess "IPD Sharing Access Criteria") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingAccess) |

| Index Field | protocolSection.ipdSharingStatementModule.**url** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingURL "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=IPDSharingURL)) |
| Definition | [IPD Sharing URL](https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingURL "IPD Sharing URL") (https://clinicaltrials.gov/policy/protocol-definitions#IPDSharingURL) |

## Results Section

| Index Field | **resultsSection** |
| --- | --- |
| Data Type | ResultsSection |
| Definition | [Study Results](https://clinicaltrials.gov/policy/results-definitions "Study Results") (https://clinicaltrials.gov/policy/results-definitions) |

| Index Field | resultsSection.**participantFlowModule** |
| --- | --- |
| Data Type | ParticipantFlowModule |
| Definition | [Participant Flow](https://clinicaltrials.gov/policy/results-definitions#Result_ParticipantFlow "Participant Flow") (https://clinicaltrials.gov/policy/results-definitions#Result\_ParticipantFlow) |

| Index Field | resultsSection.participantFlowModule.**preAssignmentDetails** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowPreAssignmentDetails "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowPreAssignmentDetails)) |
| Definition | [Pre-assignment Details](https://clinicaltrials.gov/policy/results-definitions#PreAssignDetails "Pre-assignment Details") (https://clinicaltrials.gov/policy/results-definitions#PreAssignDetails) |

| Index Field | resultsSection.participantFlowModule.**recruitmentDetails** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowRecruitmentDetails "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowRecruitmentDetails)) |
| Definition | [Recruitment Details](https://clinicaltrials.gov/policy/results-definitions#RecruitDetails "Recruitment Details") (https://clinicaltrials.gov/policy/results-definitions#RecruitDetails) |

| Index Field | resultsSection.participantFlowModule.**typeUnitsAnalyzed** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowTypeUnitsAnalyzed "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowTypeUnitsAnalyzed)) |
| Definition | [Type of Units Assigned](https://clinicaltrials.gov/policy/results-definitions#PopFlowTypeUnitsRandomized "Type of Units Assigned") (https://clinicaltrials.gov/policy/results-definitions#PopFlowTypeUnitsRandomized) |

| Index Field | resultsSection.participantFlowModule.**groups** |
| --- | --- |
| Data Type | FlowGroup\[\] |
| Definition | [Arm/Group Information](https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroup "Arm/Group Information") (https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroup) |

| Index Field | resultsSection.participantFlowModule.groups.**id** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupId)) |
| Description | Arm/Group ID generated by PRS |

| Index Field | resultsSection.participantFlowModule.groups.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupTitle)) |
| Definition | [Arm/Group Title](https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroupTitle "Arm/Group Title") (https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroupTitle) |

| Index Field | resultsSection.participantFlowModule.groups.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowGroupDescription)) |
| Definition | [Arm/Group Description](https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroupDesc "Arm/Group Description") (https://clinicaltrials.gov/policy/results-definitions#PopFlowArmGroupDesc) |

| Index Field | resultsSection.participantFlowModule.**numFlowGroups** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowGroups "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowGroups)) |
| Description | Number of Arm/Group |

| Index Field | resultsSection.participantFlowModule.**periods** |
| --- | --- |
| Data Type | FlowPeriod\[\] |
| Definition | [Period(s)](https://clinicaltrials.gov/policy/results-definitions#Period "Period(s)") (https://clinicaltrials.gov/policy/results-definitions#Period) |

| Index Field | resultsSection.participantFlowModule.periods.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowPeriodTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowPeriodTitle)) |
| Definition | [Period Title](https://clinicaltrials.gov/policy/results-definitions#PeriodTitle "Period Title") (https://clinicaltrials.gov/policy/results-definitions#PeriodTitle) |

| Index Field | resultsSection.participantFlowModule.periods.**milestones** |
| --- | --- |
| Data Type | FlowMilestone\[\] |
| Definition | [Additional Milestone(s)](https://clinicaltrials.gov/policy/results-definitions#MiscMilestone "Additional Milestone(s)") (https://clinicaltrials.gov/policy/results-definitions#MiscMilestone) |

| Index Field | resultsSection.participantFlowModule.periods.milestones.**type** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowMilestoneType "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowMilestoneType)) |
| Definition | [Started](https://clinicaltrials.gov/policy/results-definitions#Started "Started") (https://clinicaltrials.gov/policy/results-definitions#Started) |

| Index Field | resultsSection.participantFlowModule.periods.milestones.**achievements** |
| --- | --- |
| Data Type | FlowStats\[\] |
| Description | Milestone Data (per arm/group) |

| Index Field | resultsSection.participantFlowModule.periods.milestones.achievements.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementGroupId)) |
| Description | ID |

| Index Field | resultsSection.participantFlowModule.periods.milestones.achievements.**numSubjects** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementNumSubjects "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementNumSubjects)) |
| Definition | [Milestone Data](https://clinicaltrials.gov/policy/results-definitions#MilestoneData "Milestone Data") (https://clinicaltrials.gov/policy/results-definitions#MilestoneData) |

| Index Field | resultsSection.participantFlowModule.periods.milestones.achievements.**numUnits** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementNumUnits "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowAchievementNumUnits)) |
| Definition | [Milestone Data](https://clinicaltrials.gov/policy/results-definitions#MilestoneData "Milestone Data") (https://clinicaltrials.gov/policy/results-definitions#MilestoneData) |

| Index Field | resultsSection.participantFlowModule.periods.milestones.**numFlowAchievements** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowAchievements "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowAchievements)) |
| Description | Number of Arms/Groups (for each milestone) |

| Index Field | resultsSection.participantFlowModule.periods.**numFlowMilestones** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowMilestones "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowMilestones)) |
| Description | Number of milestones within a period |

| Index Field | resultsSection.participantFlowModule.periods.**dropWithdraws** |
| --- | --- |
| Data Type | DropWithdraw\[\] |
| Definition | [Reason Not Completed](https://clinicaltrials.gov/policy/results-definitions#DWReason "Reason Not Completed") (https://clinicaltrials.gov/policy/results-definitions#DWReason) |

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.**type** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowDropWithdrawType "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowDropWithdrawType)) |
| Definition | [Reason Not Completed Type](https://clinicaltrials.gov/policy/results-definitions#DWReasonType "Reason Not Completed Type") (https://clinicaltrials.gov/policy/results-definitions#DWReasonType) |

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.**comment** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowDropWithdrawComment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowDropWithdrawComment)) |
| Description | A brief description of the reason for non-completion, if "Other" Reason Not Completed Type is selected. |

## FlowReason

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.**reasons** |
| --- | --- |
| Data Type | FlowStats\[\] |
| Description | Reason for Not Completed per arm/group |

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.reasons.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonGroupId)) |
| Description | Internally generated ID for reason not completed per arm/group |

## FlowReasonComment

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.reasons.**comment** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonComment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonComment)) |
| Definition | [Reason Not Completed Data](https://clinicaltrials.gov/policy/results-definitions#DWReasonData "Reason Not Completed Data") (https://clinicaltrials.gov/policy/results-definitions#DWReasonData) |

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.reasons.**numSubjects** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonNumSubjects "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=FlowReasonNumSubjects)) |
| Definition | [Reason Not Completed Data](https://clinicaltrials.gov/policy/results-definitions#DWReasonData "Reason Not Completed Data") (https://clinicaltrials.gov/policy/results-definitions#DWReasonData) |

| Index Field | resultsSection.participantFlowModule.periods.dropWithdraws.**numFlowReasons** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowReasons "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowReasons)) |
| Description | number of arm/group in reason not completed |

| Index Field | resultsSection.participantFlowModule.periods.**numFlowDropWithdraws** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowDropWithdraws "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowDropWithdraws)) |
| Description | Number of reasons not completed |

| Index Field | resultsSection.participantFlowModule.**numFlowPeriods** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowPeriods "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumFlowPeriods)) |
| Description | Number of periods |

| Index Field | resultsSection.**baselineCharacteristicsModule** |
| --- | --- |
| Data Type | BaselineCharacteristicsModule |
| Definition | [Baseline Characteristics](https://clinicaltrials.gov/policy/results-definitions#Result_Baseline "Baseline Characteristics") (https://clinicaltrials.gov/policy/results-definitions#Result\_Baseline) |

| Index Field | resultsSection.baselineCharacteristicsModule.**populationDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselinePopulationDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselinePopulationDescription)) |
| Definition | [Baseline Analysis Population Description](https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopulationDesc "Baseline Analysis Population Description") (https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopulationDesc) |

| Index Field | resultsSection.baselineCharacteristicsModule.**typeUnitsAnalyzed** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineTypeUnitsAnalyzed "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineTypeUnitsAnalyzed)) |
| Definition | [Type of Units Analyzed](https://clinicaltrials.gov/policy/results-definitions#BaselineTypeUnitsAnalyzed "Type of Units Analyzed") (https://clinicaltrials.gov/policy/results-definitions#BaselineTypeUnitsAnalyzed) |

| Index Field | resultsSection.baselineCharacteristicsModule.**groups** |
| --- | --- |
| Data Type | MeasureGroup\[\] |
| Definition | [Arm/Group Information](https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroup "Arm/Group Information") (https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroup) |

| Index Field | resultsSection.baselineCharacteristicsModule.groups.**id** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupId)) |
| Description | Internally generated ID |

| Index Field | resultsSection.baselineCharacteristicsModule.groups.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupTitle)) |
| Definition | [Arm/Group Title](https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroupTitle "Arm/Group Title") (https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroupTitle) |

| Index Field | resultsSection.baselineCharacteristicsModule.groups.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineGroupDescription)) |
| Definition | [Arm/Group Description](https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroupDesc "Arm/Group Description") (https://clinicaltrials.gov/policy/results-definitions#BaselineArmGroupDesc) |

| Index Field | resultsSection.baselineCharacteristicsModule.**numBaselineGroups** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineGroups "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineGroups)) |
| Description | Number of Arm/Groups for Baseline |

## BaselineDenom

| Index Field | resultsSection.baselineCharacteristicsModule.**denoms** |
| --- | --- |
| Data Type | Denom\[\] |
| Description | Structure for Overall Baseline Measure Data (Row) |

| Index Field | resultsSection.baselineCharacteristicsModule.denoms.**units** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomUnits "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomUnits)) |
| Definition | [Overall Number of Units Analyzed](https://clinicaltrials.gov/policy/results-definitions#BaselineNumUnitsAnalyzed "Overall Number of Units Analyzed") (https://clinicaltrials.gov/policy/results-definitions#BaselineNumUnitsAnalyzed) |

## BaselineDenomCount

| Index Field | resultsSection.baselineCharacteristicsModule.denoms.**counts** |
| --- | --- |
| Data Type | DenomCount\[\] |
| Description | Structure for overall number per arm/group |

| Index Field | resultsSection.baselineCharacteristicsModule.denoms.counts.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomCountGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomCountGroupId)) |
| Description | Internally generated ID for each Arm/Group |

## BaselineDenomCountValue

| Index Field | resultsSection.baselineCharacteristicsModule.denoms.counts.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomCountValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineDenomCountValue)) |
| Definition | [Overall Number of Baseline Participants](https://clinicaltrials.gov/policy/results-definitions#OverallBaselineNumber "Overall Number of Baseline Participants") (https://clinicaltrials.gov/policy/results-definitions#OverallBaselineNumber) |

## NumBaselineDenoms

| Index Field | resultsSection.baselineCharacteristicsModule.**numBaselineDenoms** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineDenoms "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineDenoms)) |
| Description | Number of BaselineDenomUnits (Row) |

| Index Field | resultsSection.baselineCharacteristicsModule.**measures** |
| --- | --- |
| Data Type | BaselineMeasure\[\] |
| Definition | [Baseline Measure Information](https://clinicaltrials.gov/policy/results-definitions#BaselineMeasure "Baseline Measure Information") (https://clinicaltrials.gov/policy/results-definitions#BaselineMeasure) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureTitle)) |
| Definition | [Baseline Measure Title](https://clinicaltrials.gov/policy/results-definitions#BaselineMeasureType "Baseline Measure Title") (https://clinicaltrials.gov/policy/results-definitions#BaselineMeasureType) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDescription)) |
| Definition | [Baseline Measure Description](https://clinicaltrials.gov/policy/results-definitions#BaselineDescription "Baseline Measure Description") (https://clinicaltrials.gov/policy/results-definitions#BaselineDescription) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**populationDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurePopulationDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurePopulationDescription)) |
| Definition | [Measure Analysis Population Description](https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopDesc "Measure Analysis Population Description") (https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopDesc) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**paramType** |
| --- | --- |
| Data Type |  |
| Definition | [Measure Type](https://clinicaltrials.gov/policy/results-definitions#BaselineParamType "Measure Type") (https://clinicaltrials.gov/policy/results-definitions#BaselineParamType) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**dispersionType** |
| --- | --- |
| Data Type |  |
| Definition | [Measure of Dispersion](https://clinicaltrials.gov/policy/results-definitions#BaselineDispersType "Measure of Dispersion") (https://clinicaltrials.gov/policy/results-definitions#BaselineDispersType) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**unitOfMeasure** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureUnitOfMeasure "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureUnitOfMeasure)) |
| Definition | [Unit of Measure](https://clinicaltrials.gov/policy/results-definitions#BaselineUnitOfMeasure "Unit of Measure") (https://clinicaltrials.gov/policy/results-definitions#BaselineUnitOfMeasure) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**calculatePct** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureCalculatePct "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureCalculatePct)) |
| Description | percentage of BaselineMeasurementValue/BaselineMeasureDenomCountValue |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**denomUnitsSelected** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomUnitsSelected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomUnitsSelected)) |
| Definition | [Analysis Population Type](https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopType "Analysis Population Type") (https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPopType) |

## BaselineMeasureDenom

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**denoms** |
| --- | --- |
| Data Type | Denom\[\] |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.denoms.**units** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomUnits "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomUnits)) |
| Definition | [Number of Units Analyzed](https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisNumUnits "Number of Units Analyzed") (https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisNumUnits) |

## BaselineMeasureDenomCount

| Index Field | resultsSection.baselineCharacteristicsModule.measures.denoms.**counts** |
| --- | --- |
| Data Type | DenomCount\[\] |
| Description | number entered for unit of measure |

## BaselineMeasureDenomCountGroupId

| Index Field | resultsSection.baselineCharacteristicsModule.measures.denoms.counts.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomCountGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomCountGroupId)) |
| Description | Internally generated ID for each Arm/Group |

## BaselineMeasureDenomCountValue

| Index Field | resultsSection.baselineCharacteristicsModule.measures.denoms.counts.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomCountValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasureDenomCountValue)) |
| Definition | [Number of Baseline Participants](https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPop "Number of Baseline Participants") (https://clinicaltrials.gov/policy/results-definitions#BaselineAnalysisPop) |

## NumBaselineMeasureDenoms

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**numBaselineMeasureDenoms** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasureDenoms "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasureDenoms)) |

## BaselineClass

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**classes** |
| --- | --- |
| Data Type | MeasureClass\[\] |
| Description | Structure for a Baseline Measure ROW |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassTitle)) |
| Definition | [Category or Row Title](https://clinicaltrials.gov/policy/results-definitions#CategoryTitle "Category or Row Title") (https://clinicaltrials.gov/policy/results-definitions#CategoryTitle) |

## BaselineClassDenom

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.**denoms** |
| --- | --- |
| Data Type | Denom\[\] |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.denoms.**units** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomUnits "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomUnits)) |
| Description | Possible analysis population when data are presented in rows (e.g., if units other than participants are included in baseline, both participants and the units are listed as BaselineClassDenomUnits for the applicable baseline measure) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.denoms.**counts** |
| --- | --- |
| Data Type | DenomCount\[\] |
| Description | Population Analyzed for a Row |

## BaselineClassDenomCountGroupId

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.denoms.counts.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomCountGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomCountGroupId)) |
| Description | Internal ID per Arm/Group for a Baseline Measure |

## BaselineClassDenomCountValue

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.denoms.counts.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomCountValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineClassDenomCountValue)) |
| Description | Data per Arm/Group per Baseline Measure per Row |

## BaselineCategory

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.**categories** |
| --- | --- |
| Data Type | MeasureCategory\[\] |
| Description | Categories under a Baseline Measure (represented as rows in data table) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineCategoryTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineCategoryTitle)) |
| Definition | [Category or Row Title](https://clinicaltrials.gov/policy/results-definitions#CategoryTitle "Category or Row Title") (https://clinicaltrials.gov/policy/results-definitions#CategoryTitle) |

## BaselineMeasurement

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.**measurements** |
| --- | --- |
| Data Type | Measurement\[\] |
| Description | Data structure per Arm/Group per Category |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.measurements.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementGroupId)) |
| Description | Internal ID per Arm/Group per Category |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.measurements.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementValue)) |
| Definition | [Baseline Measure Data](https://clinicaltrials.gov/policy/results-definitions#BaselineData "Baseline Measure Data") (https://clinicaltrials.gov/policy/results-definitions#BaselineData) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.measurements.**spread** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementSpread "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementSpread)) |
| Description | Data per Arm/Group per Category. Based on Measure Type and Measure of Dispersion (e.g., Standard Deviation) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.measurements.**lowerLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementLowerLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementLowerLimit)) |
| Description | Data per Arm/Group per Category. Based on Measure Type and Measure of Dispersion (e.g., lower limit of Full Range) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.measurements.**upperLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementUpperLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=BaselineMeasurementUpperLimit)) |
| Description | Data per Arm/Group per Category. Based on Measure Type and Measure of Dispersion (e.g., upper limit of Full Range) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.categories.**numBaselineMeasurements** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasurements "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasurements)) |
| Description | Number of Baseline Arm/Groups (internally calculated) |

| Index Field | resultsSection.baselineCharacteristicsModule.measures.classes.**numBaselineCategories** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineCategories "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineCategories)) |
| Description | Number of categories per Baseline Measure |

## NumBaselineClasses

| Index Field | resultsSection.baselineCharacteristicsModule.measures.**numBaselineClasses** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineClasses "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineClasses)) |
| Description | Number of classes (rows) per Baseline Measure (internally calculated) |

| Index Field | resultsSection.baselineCharacteristicsModule.**numBaselineMeasures** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasures "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumBaselineMeasures)) |
| Description | Number of Baseline Measures (internally calculated) |

| Index Field | resultsSection.**outcomeMeasuresModule** |
| --- | --- |
| Data Type | OutcomeMeasuresModule |
| Definition | [Outcome Measures](https://clinicaltrials.gov/policy/results-definitions#Result_Outcome_Measure "Outcome Measures") (https://clinicaltrials.gov/policy/results-definitions#Result\_Outcome\_Measure) |

| Index Field | resultsSection.outcomeMeasuresModule.**outcomeMeasures** |
| --- | --- |
| Data Type | OutcomeMeasure\[\] |
| Definition | [Outcome Measure Information](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasure "Outcome Measure Information") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasure) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**type** |
| --- | --- |
| Data Type |  |
| Definition | [Outcome Measure Type](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureType "Outcome Measure Type") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTitle)) |
| Definition | [Outcome Measure Title](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureTitle "Outcome Measure Title") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureTitle) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDescription)) |
| Definition | [Outcome Measure Description](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDesc "Outcome Measure Description") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDesc) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**populationDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurePopulationDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurePopulationDescription)) |
| Definition | [Analysis Population Description](https://clinicaltrials.gov/policy/results-definitions#AnalysisPopulation "Analysis Population Description") (https://clinicaltrials.gov/policy/results-definitions#AnalysisPopulation) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**reportingStatus** |
| --- | --- |
| Data Type |  |
| Description | Whether there is Outcome Measure Data reported |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**anticipatedPostingDate** |
| --- | --- |
| Data Type | PartialDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureAnticipatedPostingDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureAnticipatedPostingDate)) |
| Definition | [Anticipated Reporting Date](https://clinicaltrials.gov/policy/results-definitions#OutcomeAnticipatedPostDate "Anticipated Reporting Date") (https://clinicaltrials.gov/policy/results-definitions#OutcomeAnticipatedPostDate) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**paramType** |
| --- | --- |
| Data Type |  |
| Definition | [Measure Type](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureParamType "Measure Type") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureParamType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**dispersionType** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDispersionType "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDispersionType)) |
| Definition | [Measure of Dispersion/Precision](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDispersType "Measure of Dispersion/Precision") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDispersType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**unitOfMeasure** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureUnitOfMeasure "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureUnitOfMeasure)) |
| Definition | [Unit of Measure](https://clinicaltrials.gov/policy/results-definitions#OutcomeUnitOfMeasure "Unit of Measure") (https://clinicaltrials.gov/policy/results-definitions#OutcomeUnitOfMeasure) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**calculatePct** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureCalculatePct "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureCalculatePct)) |
| Description | percentage of OutcomeMeasurementValue/OutcomeMeasureDenomCountValue (internally calculated) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**timeFrame** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTimeFrame)) |
| Definition | [Outcome Measure Time Frame](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureTimeFrame "Outcome Measure Time Frame") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureTimeFrame) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**typeUnitsAnalyzed** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTypeUnitsAnalyzed "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureTypeUnitsAnalyzed)) |
| Definition | [Type of Units Analyzed](https://clinicaltrials.gov/policy/results-definitions#TypeUnitsAnalyzed "Type of Units Analyzed") (https://clinicaltrials.gov/policy/results-definitions#TypeUnitsAnalyzed) |

## OutcomeMeasureDenomUnitsSelected

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**denomUnitsSelected** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDenomUnitsSelected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasureDenomUnitsSelected)) |
| Description | OutcomeMeasureTypeUnitsAnalyzed |

## OutcomeGroup

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**groups** |
| --- | --- |
| Data Type | MeasureGroup\[\] |
| Definition | [Arm/Group Information](https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroup "Arm/Group Information") (https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroup) |

## OutcomeGroupId

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.groups.**id** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupId)) |

## OutcomeGroupTitle

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.groups.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupTitle)) |
| Definition | [Arm/Group Title](https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroupTitle "Arm/Group Title") (https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroupTitle) |

## OutcomeGroupDescription

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.groups.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeGroupDescription)) |
| Definition | [Arm/Group Description](https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroupDesc "Arm/Group Description") (https://clinicaltrials.gov/policy/results-definitions#OutcomeArmGroupDesc) |

## NumOutcomeGroups

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**numOutcomeGroups** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeGroups "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeGroups)) |

## OutcomeDenom

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**denoms** |
| --- | --- |
| Data Type | Denom\[\] |

## OutcomeDenomUnits

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.denoms.**units** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomUnits "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomUnits)) |
| Definition | [Overall Number of Units Analyzed](https://clinicaltrials.gov/policy/results-definitions#NumUnitsAnalyzed "Overall Number of Units Analyzed") (https://clinicaltrials.gov/policy/results-definitions#NumUnitsAnalyzed) |

## OutcomeDenomCount

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.denoms.**counts** |
| --- | --- |
| Data Type | DenomCount\[\] |

## OutcomeDenomCountGroupId

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.denoms.counts.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomCountGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomCountGroupId)) |

## OutcomeDenomCountValue

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.denoms.counts.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomCountValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeDenomCountValue)) |
| Definition | [Overall Number of Participants Analyzed](https://clinicaltrials.gov/policy/results-definitions#SubjectsAnalyzed "Overall Number of Participants Analyzed") (https://clinicaltrials.gov/policy/results-definitions#SubjectsAnalyzed) |

## NumOutcomeDenoms

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**numOutcomeDenoms** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeDenoms "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeDenoms)) |

## OutcomeClass

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**classes** |
| --- | --- |
| Data Type | MeasureClass\[\] |

## OutcomeClassTitle

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassTitle)) |
| Definition | [Category or Row Title](https://clinicaltrials.gov/policy/results-definitions#OutcomeCategoryTitle "Category or Row Title") (https://clinicaltrials.gov/policy/results-definitions#OutcomeCategoryTitle) |

## OutcomeClassDenom

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.**denoms** |
| --- | --- |
| Data Type | Denom\[\] |

## OutcomeClassDenomCount

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.denoms.**counts** |
| --- | --- |
| Data Type | DenomCount\[\] |

## OutcomeClassDenomCountGroupId

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.denoms.counts.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassDenomCountGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassDenomCountGroupId)) |

## OutcomeClassDenomCountValue

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.denoms.counts.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassDenomCountValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeClassDenomCountValue)) |
| Definition | [Number of Participants Analyzed](https://clinicaltrials.gov/policy/results-definitions#OutcomePopulation "Number of Participants Analyzed") (https://clinicaltrials.gov/policy/results-definitions#OutcomePopulation) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.**categories** |
| --- | --- |
| Data Type | MeasureCategory\[\] |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeCategoryTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeCategoryTitle)) |
| Definition | [Category or Row Title](https://clinicaltrials.gov/policy/results-definitions#OutcomeCategoryTitle "Category or Row Title") (https://clinicaltrials.gov/policy/results-definitions#OutcomeCategoryTitle) |

## OutcomeMeasurement

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.**measurements** |
| --- | --- |
| Data Type | Measurement\[\] |
| Definition | [Outcome Measure Data Table](https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDataTable "Outcome Measure Data Table") (https://clinicaltrials.gov/policy/results-definitions#OutcomeMeasureDataTable) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.measurements.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementGroupId)) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.measurements.**value** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementValue)) |
| Definition | [Outcome Measure Data](https://clinicaltrials.gov/policy/results-definitions#OutcomeData "Outcome Measure Data") (https://clinicaltrials.gov/policy/results-definitions#OutcomeData) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.measurements.**spread** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementSpread "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementSpread)) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.measurements.**lowerLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementLowerLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementLowerLimit)) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.measurements.**upperLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementUpperLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeMeasurementUpperLimit)) |

## NumOutcomeMeasurements

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.categories.**numOutcomeMeasurements** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeMeasurements "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeMeasurements)) |

## NumOutcomeCategories

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.classes.**numOutcomeCategories** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeCategories "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeCategories)) |

## NumOutcomeClasses

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**numOutcomeClasses** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeClasses "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeClasses)) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**analyses** |
| --- | --- |
| Data Type | MeasureAnalysis\[\] |
| Description | Result(s) of scientifically appropriate tests of statistical significance of the primary and secondary outcome measures, if any. Such analyses include: pre-specified in the protocol and/or statistical analysis plan; made public by the sponsor or responsible party; conducted on a primary outcome measure in response to a request made by FDA. If a statistical analysis is reported "Comparison Group Selection" and "Type of Statistical Test" are required. In addition, one of the following data elements are required with the associated information: "P-Value," "Estimation Parameter," or "Other Statistical Analysis." |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**paramType** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisParamType "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisParamType)) |
| Definition | [Estimation Parameter](https://clinicaltrials.gov/policy/results-definitions#EstParamType "Estimation Parameter") (https://clinicaltrials.gov/policy/results-definitions#EstParamType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**paramValue** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisParamValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisParamValue)) |
| Definition | [Estimated Value](https://clinicaltrials.gov/policy/results-definitions#EstValue "Estimated Value") (https://clinicaltrials.gov/policy/results-definitions#EstValue) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**dispersionType** |
| --- | --- |
| Data Type |  |
| Definition | [Parameter Dispersion Type](https://clinicaltrials.gov/policy/results-definitions#EstDispersType "Parameter Dispersion Type") (https://clinicaltrials.gov/policy/results-definitions#EstDispersType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**dispersionValue** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisDispersionValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisDispersionValue)) |
| Definition | [Dispersion Value](https://clinicaltrials.gov/policy/results-definitions#EstDispersAmount "Dispersion Value") (https://clinicaltrials.gov/policy/results-definitions#EstDispersAmount) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**statisticalMethod** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisStatisticalMethod "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisStatisticalMethod)) |
| Definition | [Method](https://clinicaltrials.gov/policy/results-definitions#Method "Method") (https://clinicaltrials.gov/policy/results-definitions#Method) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**pValue** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisPValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisPValue)) |
| Definition | [P-Value](https://clinicaltrials.gov/policy/results-definitions#PValue "P-Value") (https://clinicaltrials.gov/policy/results-definitions#PValue) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciNumSides** |
| --- | --- |
| Data Type |  |
| Definition | [Number of Sides](https://clinicaltrials.gov/policy/results-definitions#CINumberSides "Number of Sides") (https://clinicaltrials.gov/policy/results-definitions#CINumberSides) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciPctValue** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIPctValue "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIPctValue)) |
| Definition | [Level](https://clinicaltrials.gov/policy/results-definitions#ConfLevel "Level") (https://clinicaltrials.gov/policy/results-definitions#ConfLevel) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciLowerLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCILowerLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCILowerLimit)) |
| Definition | [Lower Limit](https://clinicaltrials.gov/policy/results-definitions#LowerLimit "Lower Limit") (https://clinicaltrials.gov/policy/results-definitions#LowerLimit) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciUpperLimit** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIUpperLimit "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIUpperLimit)) |
| Definition | [Upper Limit](https://clinicaltrials.gov/policy/results-definitions#UpperLimit "Upper Limit") (https://clinicaltrials.gov/policy/results-definitions#UpperLimit) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciLowerLimitComment** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCILowerLimitComment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCILowerLimitComment)) |
| Description | Confidence Interval - lower limit comment |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**ciUpperLimitComment** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIUpperLimitComment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisCIUpperLimitComment)) |
| Description | Confidence Interval - upper limit comment (Explain why the upper limit data are not available, if "NA" is reported as upper-limit of "2-sided" confidence interval.) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**testedNonInferiority** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisTestedNonInferiority "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisTestedNonInferiority)) |
| Definition | [Type of Statistical Test](https://clinicaltrials.gov/policy/results-definitions#StatsTestType "Type of Statistical Test") (https://clinicaltrials.gov/policy/results-definitions#StatsTestType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**nonInferiorityType** |
| --- | --- |
| Data Type |  |
| Definition | [Type of Statistical Test](https://clinicaltrials.gov/policy/results-definitions#StatsTestType "Type of Statistical Test") (https://clinicaltrials.gov/policy/results-definitions#StatsTestType) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**otherAnalysisDescription** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisOtherAnalysisDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisOtherAnalysisDescription)) |
| Definition | [Other Statistical Analysis](https://clinicaltrials.gov/policy/results-definitions#OtherStats "Other Statistical Analysis") (https://clinicaltrials.gov/policy/results-definitions#OtherStats) |

## OutcomeAnalysisGroupId

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**groupIds** |
| --- | --- |
| Data Type | text\[\] ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OutcomeAnalysisGroupId)) |
| Definition | [Comparison Group Selection](https://clinicaltrials.gov/policy/results-definitions#GroupSelection "Comparison Group Selection") (https://clinicaltrials.gov/policy/results-definitions#GroupSelection) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.analyses.**numOutcomeAnalysisGroupIds** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeAnalysisGroupIds "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeAnalysisGroupIds)) |
| Description | Number of comparison groups selected for an Analysis (internal count) |

| Index Field | resultsSection.outcomeMeasuresModule.outcomeMeasures.**numOutcomeAnalyses** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeAnalyses "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeAnalyses)) |
| Description | Number of Analyses per Outcome Measure (internally calculated) |

| Index Field | resultsSection.outcomeMeasuresModule.**numOutcomeMeasures** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeMeasures "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOutcomeMeasures)) |
| Description | Number of Outcome Measures (internally calculated) |

| Index Field | resultsSection.adverseEventsModule.**frequencyThreshold** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsFrequencyThreshold "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsFrequencyThreshold)) |
| Definition | [Frequency Threshold for Reporting Other (Not Including Serious) Adverse Events](https://clinicaltrials.gov/policy/results-definitions#FrequencyThreshold "Frequency Threshold for Reporting Other (Not Including Serious) Adverse Events") (https://clinicaltrials.gov/policy/results-definitions#FrequencyThreshold) |

| Index Field | resultsSection.adverseEventsModule.**timeFrame** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsTimeFrame "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsTimeFrame)) |
| Definition | [Time Frame](https://clinicaltrials.gov/policy/results-definitions#Result_AdverseEvents_timeFrame "Time Frame") (https://clinicaltrials.gov/policy/results-definitions#Result\_AdverseEvents\_timeFrame) |

| Index Field | resultsSection.adverseEventsModule.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsDescription)) |
| Definition | [Adverse Event Reporting Description](https://clinicaltrials.gov/policy/results-definitions#Result_AdverseEvents_additDescription "Adverse Event Reporting Description") (https://clinicaltrials.gov/policy/results-definitions#Result\_AdverseEvents\_additDescription) |

| Index Field | resultsSection.adverseEventsModule.**allCauseMortalityComment** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsAllCauseMortalityComment "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventsAllCauseMortalityComment)) |

| Index Field | resultsSection.adverseEventsModule.**eventGroups** |
| --- | --- |
| Data Type | EventGroup\[\] |
| Definition | [Arm/Group Information](https://clinicaltrials.gov/policy/results-definitions#EventArmGroupInfo "Arm/Group Information") (https://clinicaltrials.gov/policy/results-definitions#EventArmGroupInfo) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**id** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupId)) |
| Description | Internal group id |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupTitle)) |
| Definition | [Arm/Group Title](https://clinicaltrials.gov/policy/results-definitions#EventArmGroupTitle "Arm/Group Title") (https://clinicaltrials.gov/policy/results-definitions#EventArmGroupTitle) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDescription)) |
| Definition | [Arm/Group Description](https://clinicaltrials.gov/policy/results-definitions#EventArmGroupDesc "Arm/Group Description") (https://clinicaltrials.gov/policy/results-definitions#EventArmGroupDesc) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**deathsNumAffected** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDeathsNumAffected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDeathsNumAffected)) |
| Definition | [Total Number Affected by All-Cause Mortality](https://clinicaltrials.gov/policy/results-definitions#Mortality_Affected "Total Number Affected by All-Cause Mortality") (https://clinicaltrials.gov/policy/results-definitions#Mortality\_Affected) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**deathsNumAtRisk** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDeathsNumAtRisk "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupDeathsNumAtRisk)) |
| Definition | [Total Number at Risk for All-Cause Mortality](https://clinicaltrials.gov/policy/results-definitions#Mortality_AtRisk "Total Number at Risk for All-Cause Mortality") (https://clinicaltrials.gov/policy/results-definitions#Mortality\_AtRisk) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**seriousNumAffected** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupSeriousNumAffected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupSeriousNumAffected)) |
| Definition | [Total Number Affected by Any Serious Adverse Event](https://clinicaltrials.gov/policy/results-definitions#SeriousTotalAffected "Total Number Affected by Any Serious Adverse Event") (https://clinicaltrials.gov/policy/results-definitions#SeriousTotalAffected) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**seriousNumAtRisk** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupSeriousNumAtRisk "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupSeriousNumAtRisk)) |
| Definition | [Total Number at Risk for Serious Adverse Events](https://clinicaltrials.gov/policy/results-definitions#SeriousAE_AtRisk "Total Number at Risk for Serious Adverse Events") (https://clinicaltrials.gov/policy/results-definitions#SeriousAE\_AtRisk) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**otherNumAffected** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupOtherNumAffected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupOtherNumAffected)) |
| Definition | [Total Number Affected by Any Other (Not Including Serious) Adverse Events Above the Frequency Threshold](https://clinicaltrials.gov/policy/results-definitions#OtherAE_Affected "Total Number Affected by Any Other (Not Including Serious) Adverse Events Above the Frequency Threshold") (https://clinicaltrials.gov/policy/results-definitions#OtherAE\_Affected) |

| Index Field | resultsSection.adverseEventsModule.eventGroups.**otherNumAtRisk** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupOtherNumAtRisk "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EventGroupOtherNumAtRisk)) |
| Definition | [Total Number at Risk for Other (Not Including Serious) Adverse Events](https://clinicaltrials.gov/policy/results-definitions#OtherAE_AtRisk "Total Number at Risk for Other (Not Including Serious) Adverse Events") (https://clinicaltrials.gov/policy/results-definitions#OtherAE\_AtRisk) |

| Index Field | resultsSection.adverseEventsModule.**numEventGroups** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumEventGroups "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumEventGroups)) |
| Description | Number of Arm/Group for Adverse Event (internal count) |

| Index Field | resultsSection.adverseEventsModule.**seriousEvents** |
| --- | --- |
| Data Type | AdverseEvent\[\] |
| Description | A table of all anticipated and unanticipated serious adverse events, grouped by organ system, with the number and frequency of such events by arm or comparison group of the clinical study. |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventTerm)) |
| Definition | [Adverse Event Term](https://clinicaltrials.gov/policy/results-definitions#AdverseEventTerm "Adverse Event Term") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventTerm) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**organSystem** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventOrganSystem "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventOrganSystem)) |
| Definition | [Organ System](https://clinicaltrials.gov/policy/results-definitions#OrganSystem "Organ System") (https://clinicaltrials.gov/policy/results-definitions#OrganSystem) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**sourceVocabulary** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventSourceVocabulary "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventSourceVocabulary)) |
| Definition | [Source Vocabulary Name](https://clinicaltrials.gov/policy/results-definitions#AdverseEventVocab "Source Vocabulary Name") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventVocab) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**assessmentType** |
| --- | --- |
| Data Type |  |
| Definition | [Collection Approach](https://clinicaltrials.gov/policy/results-definitions#AssessType "Collection Approach") (https://clinicaltrials.gov/policy/results-definitions#AssessType) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**notes** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventNotes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventNotes)) |
| Definition | [Adverse Event Term Additional Description](https://clinicaltrials.gov/policy/results-definitions#AdverseEventNotes "Adverse Event Term Additional Description") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventNotes) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**stats** |
| --- | --- |
| Data Type | EventStats\[\] |
| Description | Statistical information for each Serious Adverse Event |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.stats.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsGroupId)) |
| Description | Internal Arm/Group ID |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.stats.**numEvents** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumEvents)) |
| Definition | [Number of Events](https://clinicaltrials.gov/policy/results-definitions#OtherAEEvents "Number of Events") (https://clinicaltrials.gov/policy/results-definitions#OtherAEEvents) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.stats.**numAffected** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumAffected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumAffected)) |
| Definition | [Number of Participants Affected](https://clinicaltrials.gov/policy/results-definitions#AdverseEventAffected "Number of Participants Affected") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventAffected) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.stats.**numAtRisk** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumAtRisk "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SeriousEventStatsNumAtRisk)) |
| Definition | [Number of Participants at Risk](https://clinicaltrials.gov/policy/results-definitions#AdverseEventAtRisk "Number of Participants at Risk") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventAtRisk) |

| Index Field | resultsSection.adverseEventsModule.seriousEvents.**numSeriousEventStatss** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeriousEventStatss "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeriousEventStatss)) |
| Description | Number of Event Group (Arm/Group in an Adverse Event) |

| Index Field | resultsSection.adverseEventsModule.**numSeriousEvents** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeriousEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumSeriousEvents)) |
| Description | Number of Serious Adverse Events |

| Index Field | resultsSection.adverseEventsModule.**otherEvents** |
| --- | --- |
| Data Type | AdverseEvent\[\] |
| Description | Other (Not Including Serious) Adverse Events - similar to Serious AE |

## OtherEventTerm

| Index Field | resultsSection.adverseEventsModule.otherEvents.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventTerm)) |
| Definition | [Adverse Event Term](https://clinicaltrials.gov/policy/results-definitions#AdverseEventTerm "Adverse Event Term") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventTerm) |

## OtherEventOrganSystem

| Index Field | resultsSection.adverseEventsModule.otherEvents.**organSystem** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventOrganSystem "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventOrganSystem)) |
| Definition | [Organ System](https://clinicaltrials.gov/policy/results-definitions#OrganSystem "Organ System") (https://clinicaltrials.gov/policy/results-definitions#OrganSystem) |

## OtherEventSourceVocabulary

| Index Field | resultsSection.adverseEventsModule.otherEvents.**sourceVocabulary** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventSourceVocabulary "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventSourceVocabulary)) |
| Definition | [Source Vocabulary Name](https://clinicaltrials.gov/policy/results-definitions#AdverseEventVocab "Source Vocabulary Name") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventVocab) |

## OtherEventAssessmentType

| Index Field | resultsSection.adverseEventsModule.otherEvents.**assessmentType** |
| --- | --- |
| Data Type |  |
| Definition | [Collection Approach](https://clinicaltrials.gov/policy/results-definitions#AssessType "Collection Approach") (https://clinicaltrials.gov/policy/results-definitions#AssessType) |

## OtherEventNotes

| Index Field | resultsSection.adverseEventsModule.otherEvents.**notes** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventNotes "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventNotes)) |
| Definition | [Adverse Event Term Additional Description](https://clinicaltrials.gov/policy/results-definitions#AdverseEventNotes "Adverse Event Term Additional Description") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventNotes) |

## OtherEventStats

| Index Field | resultsSection.adverseEventsModule.otherEvents.**stats** |
| --- | --- |
| Data Type | EventStats\[\] |

## OtherEventStatsGroupId

| Index Field | resultsSection.adverseEventsModule.otherEvents.stats.**groupId** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsGroupId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsGroupId)) |

## OtherEventStatsNumEvents

| Index Field | resultsSection.adverseEventsModule.otherEvents.stats.**numEvents** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumEvents)) |
| Definition | [Number of Events](https://clinicaltrials.gov/policy/results-definitions#OtherAEEvents "Number of Events") (https://clinicaltrials.gov/policy/results-definitions#OtherAEEvents) |

## OtherEventStatsNumAffected

| Index Field | resultsSection.adverseEventsModule.otherEvents.stats.**numAffected** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumAffected "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumAffected)) |
| Definition | [Number of Participants Affected](https://clinicaltrials.gov/policy/results-definitions#AdverseEventAffected "Number of Participants Affected") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventAffected) |

## OtherEventStatsNumAtRisk

| Index Field | resultsSection.adverseEventsModule.otherEvents.stats.**numAtRisk** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumAtRisk "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=OtherEventStatsNumAtRisk)) |
| Definition | [Number of Participants at Risk](https://clinicaltrials.gov/policy/results-definitions#AdverseEventAtRisk "Number of Participants at Risk") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventAtRisk) |

## NumOtherEventStatss

| Index Field | resultsSection.adverseEventsModule.otherEvents.**numOtherEventStatss** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherEventStatss "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherEventStatss)) |

## NumOtherEvents

| Index Field | resultsSection.adverseEventsModule.**numOtherEvents** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumOtherEvents)) |

## NumEvents

| Index Field | resultsSection.adverseEventsModule.**numEvents** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumEvents)) |

| Index Field | resultsSection.**moreInfoModule** |
| --- | --- |
| Data Type | MoreInfoModule |

| Index Field | resultsSection.moreInfoModule.**limitationsAndCaveats** |
| --- | --- |
| Data Type | LimitationsAndCaveats |
| Definition | [Limitations and Caveats](https://clinicaltrials.gov/policy/results-definitions#Result_LimitationsAndCaveats_description "Limitations and Caveats") (https://clinicaltrials.gov/policy/results-definitions#Result\_LimitationsAndCaveats\_description) |

| Index Field | resultsSection.moreInfoModule.limitationsAndCaveats.**description** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LimitationsAndCaveatsDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LimitationsAndCaveatsDescription)) |
| Definition | [Overall Limitations and Caveats](https://clinicaltrials.gov/policy/results-definitions#AdverseEventDataLimitCaveats "Overall Limitations and Caveats") (https://clinicaltrials.gov/policy/results-definitions#AdverseEventDataLimitCaveats) |

| Index Field | resultsSection.moreInfoModule.**certainAgreement** |
| --- | --- |
| Data Type | CertainAgreement |
| Definition | [Certain Agreements](https://clinicaltrials.gov/policy/results-definitions#Result_CertainAgreement "Certain Agreements") (https://clinicaltrials.gov/policy/results-definitions#Result\_CertainAgreement) |

| Index Field | resultsSection.moreInfoModule.certainAgreement.**restrictionType** |
| --- | --- |
| Data Type |  |
| Definition | [PI Disclosure Restriction Type](https://clinicaltrials.gov/policy/results-definitions#RestrictionType "PI Disclosure Restriction Type") (https://clinicaltrials.gov/policy/results-definitions#RestrictionType) |

| Index Field | resultsSection.moreInfoModule.certainAgreement.**restrictiveAgreement** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=AgreementRestrictiveAgreement "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=AgreementRestrictiveAgreement)) |
| Definition | [Results Disclosure Restriction on PI(s)?](https://clinicaltrials.gov/policy/results-definitions#PIAgreement "Results Disclosure Restriction on PI(s)?")(https://clinicaltrials.gov/policy/results-definitions#PIAgreement) |

| Index Field | resultsSection.moreInfoModule.certainAgreement.**otherDetails** |
| --- | --- |
| Data Type | markup ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=AgreementOtherDetails "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=AgreementOtherDetails)) |
| Definition | [Other Disclosure Restriction Description](https://clinicaltrials.gov/policy/results-definitions#OtherRestrictionType "Other Disclosure Restriction Description") (https://clinicaltrials.gov/policy/results-definitions#OtherRestrictionType) |

| Index Field | resultsSection.moreInfoModule.**pointOfContact** |
| --- | --- |
| Data Type | PointOfContact |
| Definition | [Results Point of Contact](https://clinicaltrials.gov/policy/results-definitions#Result_PointOfContact "Results Point of Contact") (https://clinicaltrials.gov/policy/results-definitions#Result\_PointOfContact) |

| Index Field | resultsSection.moreInfoModule.pointOfContact.**title** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactTitle "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactTitle)) |
| Definition | [Name or Official Title](https://clinicaltrials.gov/policy/results-definitions#NameOfficialTitle "Name or Official Title") (https://clinicaltrials.gov/policy/results-definitions#NameOfficialTitle) |

| Index Field | resultsSection.moreInfoModule.pointOfContact.**organization** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactOrganization "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactOrganization)) |
| Definition | [Organization Name](https://clinicaltrials.gov/policy/results-definitions#OrgName "Organization Name") (https://clinicaltrials.gov/policy/results-definitions#OrgName) |

| Index Field | resultsSection.moreInfoModule.pointOfContact.**email** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactEMail "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactEMail)) |
| Definition | [Email](https://clinicaltrials.gov/policy/results-definitions#Email "Email") (https://clinicaltrials.gov/policy/results-definitions#Email) |

| Index Field | resultsSection.moreInfoModule.pointOfContact.**phone** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactPhone "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactPhone)) |
| Definition | [Phone](https://clinicaltrials.gov/policy/results-definitions#Phone "Phone") (https://clinicaltrials.gov/policy/results-definitions#Phone) |

| Index Field | resultsSection.moreInfoModule.pointOfContact.**phoneExt** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactPhoneExt "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=PointOfContactPhoneExt)) |
| Definition | [Extension (Ext.)](https://clinicaltrials.gov/policy/results-definitions#PhoneExt "Extension (Ext.)") (https://clinicaltrials.gov/policy/results-definitions#PhoneExt) |

## Annotation Section

| Index Field | **annotationSection** |
| --- | --- |
| Data Type | AnnotationSection |
| Description | Internally generated Annotation section |

| Index Field | annotationSection.**annotationModule** |
| --- | --- |
| Data Type | AnnotationModule |

| Index Field | annotationSection.annotationModule.**unpostedAnnotation** |
| --- | --- |
| Data Type | UnpostedAnnotation |
| Description | Tracking information for study results submission/QA review process (Results Submitted but not yet Published) |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.**unpostedResponsibleParty** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedResponsibleParty "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedResponsibleParty)) |
| Description | Information provider (Responsible Party) |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.**unpostedEvents** |
| --- | --- |
| Data Type | UnpostedEvent\[\] |
| Description | A Results Release, UnRelease or Reset event |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.unpostedEvents.**type** |
| --- | --- |
| Data Type |  |
| Description | Study Results Submission Type |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.unpostedEvents.**date** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedEventDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedEventDate)) |
| Description | Study Results Submission Date |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.unpostedEvents.**dateUnknown** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedEventDateUnknown "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=UnpostedEventDateUnknown)) |

| Index Field | annotationSection.annotationModule.unpostedAnnotation.**numUnpostedEvents** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumUnpostedEvents "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumUnpostedEvents)) |
| Description | Number of events for the Results submission/review cycle |

| Index Field | annotationSection.annotationModule.**violationAnnotation** |
| --- | --- |
| Data Type | ViolationAnnotation |
| Description | FDAAA 801 Violations - entered by PRS admins |

| Index Field | annotationSection.annotationModule.violationAnnotation.**violationEvents** |
| --- | --- |
| Data Type | ViolationEvent\[\] |
| Description | PRS admin can enter one of the following types and descriptions, or other text |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**type** |
| --- | --- |
| Data Type |  |
| Description | • Violation Identified by FDA • Correction Confirmed by FDA • Penalty Imposed by FDA |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**description** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventDescription "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventDescription)) |
| Description | • Failure to Submit. The entry for this clinical trial was not complete at the time of submission, as required by law. This may or may not have any bearing on the accuracy of the information in the entry. • Submission of False Information. The entry for this clinical trial was found to be false or misleading and therefore not in compliance with the law. • Non-submission. The entry for this clinical trial did not contain information on the primary and secondary outcomes at the time of submission, as required by law. This may or may not have any bearing on the accuracy of the information in the entry. • The responsible party has corrected the violation. • A $XX,XXX penalty was imposed against the responsible party for the violation. |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**creationDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventCreationDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventCreationDate)) |
| Description | Date the violation entered in PRS |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**issuedDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventIssuedDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventIssuedDate)) |
| Description | Date the FDA issued the violation |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**releaseDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventReleaseDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventReleaseDate)) |
| Description | Date the study record was submitted |

| Index Field | annotationSection.annotationModule.violationAnnotation.violationEvents.**postedDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventPostedDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ViolationEventPostedDate)) |
| Description | Date the violation is available on clinicaltrials.gov |

## Document Section

| Index Field | **documentSection** |
| --- | --- |
| Data Type | DocumentSection |

| Index Field | documentSection.**largeDocumentModule** |
| --- | --- |
| Data Type | LargeDocumentModule |
| Definition | [A.1 Document Upload Information](https://clinicaltrials.gov/policy/results-definitions#DocumentUpload "A.1 Document Upload Information") (https://clinicaltrials.gov/policy/results-definitions#DocumentUpload) |

| Index Field | documentSection.largeDocumentModule.**noSap** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocNoSAP "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocNoSAP)) |
| Description | Indicate that No Statistical Analysis Plan (SAP) exists for this study. |

| Index Field | documentSection.largeDocumentModule.**largeDocs** |
| --- | --- |
| Data Type | LargeDoc\[\] |
| Description | PDF/A document by data provider |

| Index Field | documentSection.largeDocumentModule.largeDocs.**typeAbbrev** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocTypeAbbrev "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocTypeAbbrev)) |
| Definition | [Document Type](https://clinicaltrials.gov/policy/results-definitions#DocUploadDocType "Document Type") (https://clinicaltrials.gov/policy/results-definitions#DocUploadDocType) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**hasProtocol** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasProtocol "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasProtocol)) |
| Description | Indicate if document includes Study Protocol (Yes/No) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**hasSap** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasSAP "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasSAP)) |
| Description | Indicate is document includes Statistical Analysis Plan (Yes/No) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**hasIcf** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasICF "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocHasICF)) |
| Description | Indicate if document includes Informed Consent Form (Yes/No) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**label** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocLabel "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocLabel)) |
| Definition | [Subtitle](https://clinicaltrials.gov/policy/results-definitions#DocUploadSubtitle "Subtitle") (https://clinicaltrials.gov/policy/results-definitions#DocUploadSubtitle) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**date** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocDate)) |
| Definition | [Document Date](https://clinicaltrials.gov/policy/results-definitions#DocUploadDocDate "Document Date") (https://clinicaltrials.gov/policy/results-definitions#DocUploadDocDate) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**filename** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocFilename "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocFilename)) |
| Description | Document file name (by data provider) |

| Index Field | documentSection.largeDocumentModule.largeDocs.**size** |
| --- | --- |
| Data Type | long ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocSize "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=LargeDocSize)) |
| Description | Document file size |

| Index Field | documentSection.largeDocumentModule.**numLargeDocs** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLargeDocs "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumLargeDocs)) |
| Description | Number of uploaded documents for a study record (internally calculated) |

## Derived Section

| Index Field | **derivedSection** |
| --- | --- |
| Data Type | DerivedSection |
| Description | Internally Generated |

| Index Field | derivedSection.**miscInfoModule** |
| --- | --- |
| Data Type | MiscInfoModule |

| Index Field | derivedSection.miscInfoModule.**versionHolder** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=VersionHolder "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=VersionHolder)) |
| Description | The most recent date where Ingest ran successfully |

| Index Field | derivedSection.miscInfoModule.**removedCountries** |
| --- | --- |
| Data Type | text\[\] ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=RemovedCountry "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=RemovedCountry)) |
| Description | Country for which all locations have been removed from the study |

| Index Field | derivedSection.miscInfoModule.**numRemovedCountries** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRemovedCountries "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumRemovedCountries)) |
| Description | Number of removed countries |

| Index Field | derivedSection.miscInfoModule.**submissionTracking** |
| --- | --- |
| Data Type | SubmissionTracking |
| Description | Results submission tracking |

| Index Field | derivedSection.miscInfoModule.submissionTracking.**estimatedResultsFirstSubmitDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=EstimatedResultsFirstSubmitDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=EstimatedResultsFirstSubmitDate)) |
| Description | Results First Submitted Date but not yet Posted (e.g., still under QC review). ResultsFirstSubmitDate at this point is kept empty until Results is published on the public site |

| Index Field | derivedSection.miscInfoModule.submissionTracking.**firstMcpInfo** |
| --- | --- |
| Data Type | FirstMcpInfo |

| Index Field | derivedSection.miscInfoModule.submissionTracking.**submissionInfos** |
| --- | --- |
| Data Type | SubmissionInfo\[\] |
| Description | Results submission cycle information of a study |

| Index Field | derivedSection.miscInfoModule.submissionTracking.submissionInfos.**releaseDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionReleaseDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionReleaseDate)) |
| Description | Results released by DP to NLM |

| Index Field | derivedSection.miscInfoModule.submissionTracking.submissionInfos.**unreleaseDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionUnreleaseDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionUnreleaseDate)) |
| Description | Results unrelease (canceled release) by DP |

## SubmissionUnreleaseDateUnknown

| Index Field | derivedSection.miscInfoModule.submissionTracking.submissionInfos.**unreleaseDateUnknown** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionUnreleaseDateUnknown "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionUnreleaseDateUnknown)) |

| Index Field | derivedSection.miscInfoModule.submissionTracking.submissionInfos.**resetDate** |
| --- | --- |
| Data Type | NormalizedDate ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionResetDate "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionResetDate)) |
| Description | NLM QC reviewer reset/unlock study back to DP |

| Index Field | derivedSection.miscInfoModule.submissionTracking.submissionInfos.**mcpReleaseN** |
| --- | --- |
| Data Type | integer ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionMCPReleaseN "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=SubmissionMCPReleaseN)) |
| Description | Number of Major Comment Postings of a study |

| Index Field | derivedSection.**conditionBrowseModule** |
| --- | --- |
| Data Type | BrowseModule |
| Description | Support for "Search By Topic" |

| Index Field | derivedSection.conditionBrowseModule.meshes.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionMeshId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionMeshId)) |
| Description | MeSH ID |

| Index Field | derivedSection.conditionBrowseModule.meshes.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionMeshTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionMeshTerm)) |
| Description | MeSH Heading |

| Index Field | derivedSection.conditionBrowseModule.ancestors.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionAncestorId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionAncestorId)) |
| Description | MeSH ID |

| Index Field | derivedSection.conditionBrowseModule.ancestors.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionAncestorTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionAncestorTerm)) |
| Description | MeSH Heading |

| Index Field | derivedSection.conditionBrowseModule.**browseLeaves** |
| --- | --- |
| Data Type | BrowseLeaf\[\] |
| Description | Leaf browsing topics for Condition field |

| Index Field | derivedSection.conditionBrowseModule.browseLeaves.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafId)) |

| Index Field | derivedSection.conditionBrowseModule.browseLeaves.**name** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafName)) |

| Index Field | derivedSection.conditionBrowseModule.browseLeaves.**asFound** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafAsFound "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseLeafAsFound)) |
| Description | Normalized Condition term used to find the topic |

| Index Field | derivedSection.conditionBrowseModule.browseLeaves.**relevance** |
| --- | --- |
| Data Type |  |

| Index Field | derivedSection.conditionBrowseModule.**numConditionBrowseLeafs** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditionBrowseLeafs "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditionBrowseLeafs)) |

| Index Field | derivedSection.conditionBrowseModule.**browseBranches** |
| --- | --- |
| Data Type | BrowseBranch\[\] |
| Description | Branch browsing topics for Condition field |

| Index Field | derivedSection.conditionBrowseModule.browseBranches.**abbrev** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseBranchAbbrev "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseBranchAbbrev)) |

| Index Field | derivedSection.conditionBrowseModule.browseBranches.**name** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseBranchName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=ConditionBrowseBranchName)) |

| Index Field | derivedSection.conditionBrowseModule.**numConditionBrowseBranches** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditionBrowseBranches "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumConditionBrowseBranches)) |

| Index Field | derivedSection.**interventionBrowseModule** |
| --- | --- |
| Data Type | BrowseModule |
| Description | Support for "Search By Topic" |

| Index Field | derivedSection.interventionBrowseModule.meshes.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionMeshId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionMeshId)) |
| Description | MeSH ID |

| Index Field | derivedSection.interventionBrowseModule.meshes.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionMeshTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionMeshTerm)) |
| Description | MeSH Heading |

| Index Field | derivedSection.interventionBrowseModule.ancestors.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionAncestorId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionAncestorId)) |
| Description | MeSH ID |

| Index Field | derivedSection.interventionBrowseModule.ancestors.**term** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionAncestorTerm "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionAncestorTerm)) |
| Description | MeSH Heading |

| Index Field | derivedSection.interventionBrowseModule.**browseLeaves** |
| --- | --- |
| Data Type | BrowseLeaf\[\] |
| Description | Leaf browsing topics for Intervention field |

| Index Field | derivedSection.interventionBrowseModule.browseLeaves.**id** |
| --- | --- |
| Data Type | text ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafId "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafId)) |

| Index Field | derivedSection.interventionBrowseModule.browseLeaves.**name** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafName)) |

| Index Field | derivedSection.interventionBrowseModule.browseLeaves.**asFound** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafAsFound "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseLeafAsFound)) |
| Description | Normalized Intervention term used to find the topic |

| Index Field | derivedSection.interventionBrowseModule.browseLeaves.**relevance** |
| --- | --- |
| Data Type |  |

| Index Field | derivedSection.interventionBrowseModule.**numInterventionBrowseLeafs** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionBrowseLeafs "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionBrowseLeafs)) |

| Index Field | derivedSection.interventionBrowseModule.**browseBranches** |
| --- | --- |
| Data Type | BrowseBranch\[\] |
| Description | Branch browsing topics for Intervention field |

| Index Field | derivedSection.interventionBrowseModule.browseBranches.**abbrev** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseBranchAbbrev "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseBranchAbbrev)) |

| Index Field | derivedSection.interventionBrowseModule.browseBranches.**name** |
| --- | --- |
| Data Type | text ✓ ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseBranchName "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=InterventionBrowseBranchName)) |

| Index Field | derivedSection.interventionBrowseModule.**numInterventionBrowseBranches** ✗ |
| --- | --- |
| Data Type | short ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionBrowseBranches "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=NumInterventionBrowseBranches)) |

## Has Results

| Index Field | **hasResults** |
| --- | --- |
| Data Type | boolean ([stats](https://clinicaltrials.gov/api/v2/stats/field/values?fields=HasResults "stats") (https://clinicaltrials.gov/api/v2/stats/field/values?fields=HasResults)) |
| Description | Flag that indicates if a study has posted results on public site |

## Enumeration types

| Type | Value - Source Value |
| --- | --- |
| Status |  |
| StudyType |  |
| Phase |  |
| Sex |  |
| StandardAge |  |
| SamplingMethod |  |
| IpdSharing |  |
| IpdSharingInfoType |  |
| OrgStudyIdType |  |
| SecondaryIdType |  |
| AgencyClass |  |
| ExpandedAccessStatus |  |
| DateType |  |
| ResponsiblePartyType |  |
| DesignAllocation |  |
| InterventionalAssignment |  |
| PrimaryPurpose |  |
| ObservationalModel |  |
| DesignTimePerspective |  |
| BioSpecRetention |  |
| EnrollmentType |  |
| ArmGroupType |  |
| InterventionType |  |
| ContactRole |  |
| OfficialRole |  |
| RecruitmentStatus |  |
| ReferenceType |  |
| MeasureParam |  |
| MeasureDispersionType |  |
| OutcomeMeasureType |  |
| ReportingStatus |  |
| EventAssessment |  |
| AgreementRestrictionType |  |
| BrowseLeafRelevance |  |
| DesignMasking |  |
| WhoMasked |  |
| AnalysisDispersionType |  |
| ConfidenceIntervalNumSides |  |
| NonInferiorityType |  |
| UnpostedEventType |  |
| ViolationEventType |  |

## Built-in types

```
/** Date in format: \`yyyy-MM-dd\` */
              type NormalizedDate = string;

              /** Date in one of the formats: \`yyyy\`, \`yyyy-MM\`, or \`yyyy-MM-dd\` */
              type PartialDate = string;

              /** DateTime in format: \`yyyy-MM-dd'T'HH:mm\` */
              type DateTimeMinutes = string;

              type NormalizedTime = string;

              interface GeoPoint {
              lat: number;
              lon: number;
              }
```

Last updated on **April 01, 2024**

[Back to Top](https://clinicaltrials.gov/data-api/about-api/#)