import logging


class SciencePaper:
	def __init__(
		self,
		doi=None,
		title=None,
		link=None,
		access=None,
		publisher=None,
		journal=None,
		published_date=None,
		abstract=None,
		authors=None,
		retracted=None,
		pdf_link=None,
	):
		self.doi = doi
		self.title = title
		self.link = link
		self.access = access
		self.publisher = publisher
		self.journal = journal
		self.published_date = published_date
		self.abstract = abstract
		self.authors = authors
		self.retracted = retracted
		self.pdf_link = pdf_link

	def __str__(self):
		return f"{self.doi}, {self.title}"

	def __repr__(self):
		return f'{self.doi}, "{self.title}"'

	def clean_abstract(self=None, abstract=None):
		from bs4 import BeautifulSoup
		import html

		if abstract == None and self.abstract != None:
			abstract = self.abstract
		if abstract != None:
			abstract = html.unescape(abstract)
			soup = BeautifulSoup(abstract, "html.parser")
			for tag in soup():
				for attribute in ["class", "id", "name", "style"]:
					del tag[attribute]
			return str(soup)

	def clean_url(self=None):
		from gregory.functions import remove_utm

		if self.link != None:
			self.link = remove_utm(self.link)
		else:
			logging.warning("no url found")

	def refresh(self):
		from gregory.unpaywall import unpaywall_utils
		from crossref.restful import Works, Etiquette
		import os
		import pytz
		from datetime import datetime
		from requests.exceptions import HTTPError, RequestException
		import json

		timezone = pytz.timezone("UTC")
		from sitesettings.models import CustomSetting

		site = CustomSetting.objects.get(site__domain=os.environ.get("DOMAIN_NAME"))
		client_website = "https://" + site.site.domain + "/"
		my_etiquette = Etiquette(site.title, "v8", client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = None

		if self.doi != None:
			try:
				work = works.doi(self.doi)
			except HTTPError as e:
				if e.response.status_code == 404:
					logging.warning(f"DOI not found in CrossRef: {self.doi}")
					return "DOI not found"
				else:
					logging.error(f"CrossRef HTTP error for DOI {self.doi}: {e}")
					return f"CrossRef HTTP error: {e}"
			except json.JSONDecodeError as e:
				logging.error(
					f"Error decoding JSON from CrossRef response for DOI: {self.doi}"
				)
				return "JSON decode error"
			except RequestException as e:
				logging.error(f"CrossRef request error for DOI {self.doi}: {e}")
				return f"CrossRef request error: {e}"
			except Exception as e:
				logging.error(
					f"Unexpected error querying CrossRef for DOI {self.doi}: {e}"
				)
				return f"Unexpected error: {e}"
		else:
			return "No DOI provided"

		# Only proceed if we successfully got work data
		if work is None:
			return "No data retrieved from CrossRef"

		if self.link == None:
			try:
				self.link = work["link"][0]["URL"]
			except (KeyError, IndexError, TypeError):
				logging.warning(f"No link found for DOI {self.doi}")
				pass
		if self.title == None:
			try:
				self.title = work["title"][0]
			except (KeyError, IndexError, TypeError):
				logging.warning(f"No title found for DOI {self.doi}")
				pass
		if self.doi != None and (self.access == None or self.pdf_link == None):
			if site.admin_email == None:
				logging.warning("No site admin email found")
			else:
				try:
					unpaywall_data = unpaywall_utils.getDataByDOI(self.doi, site.admin_email)
					if unpaywall_data:
						if self.access == None:
							self.access = "open" if unpaywall_data.get("is_oa") else "restricted"
						if self.pdf_link == None:
							oa_loc = unpaywall_data.get("best_oa_location") or {}
							self.pdf_link = oa_loc.get("url_for_pdf") or oa_loc.get("url")
					elif self.access == None:
						self.access = "unknown"
				except Exception as e:
					logging.error(f"Error checking Unpaywall for DOI {self.doi}: {e}")
					if self.access == None:
						self.access = "unknown"

		if self.publisher == None:
			if work != None and "publisher" in work:
				if isinstance(work["publisher"], list):
					self.publisher = work["publisher"][0]
				else:
					self.publisher = work["publisher"]
		if self.journal == None:
			if work != None and "container-title" in work:
				if isinstance(work["container-title"], list):
					if len(work["container-title"]) > 0:
						self.journal = work["container-title"][0]
					else:
						self.journal = ""
				else:
					self.journal = work["container-title"]
		if self.published_date == None:
			if work != None and "issued" in work:
				issued = work["issued"]["date-parts"][0]
			year, month, day = None, 1, 1
			try:
				year = issued[0]
			except (IndexError, TypeError):
				logging.warning(f"No year found in issued date for DOI {self.doi}")
				pass
			try:
				month = issued[1]
			except (IndexError, TypeError):
				logging.warning(f"No month found in issued date for DOI {self.doi}")
				pass
			try:
				day = issued[2]
			except (IndexError, TypeError):
				logging.warning(f"No day found in issued date for DOI {self.doi}")
				pass
			try:
				self.published_date = datetime(
					year=year, month=month, day=day, tzinfo=timezone
				)
			except (TypeError, ValueError):
				logging.warning(f"Invalid date found for DOI {self.doi}")
				pass
		if self.abstract == None:
			try:
				self.abstract = work["abstract"]
			except (KeyError, IndexError, TypeError):
				logging.warning(f"No abstract found for DOI {self.doi}")
				pass
		if self.authors == None:
			try:
				self.authors = work["author"]
			except (KeyError, IndexError, TypeError):
				logging.warning(f"No authors found for DOI {self.doi}")
				pass
		if self.retracted == None:
			try:
				updates = work.get("updated-by") or []
				self.retracted = (
					any(
						isinstance(update, dict) and update.get("type") == "retraction"
						for update in updates
					)
					or None
				)
			except (KeyError, IndexError, TypeError):
				logging.warning(f"No retraction status found for DOI {self.doi}")
				pass

	def find_doi(self, title=None):
		if title == None:
			return "Missing required title field"
		import re
		import os
		from sitesettings.models import CustomSetting
		from crossref.restful import Works, Etiquette

		self.doi = None
		self.title = title
		site = CustomSetting.objects.get(site__domain=os.environ.get("DOMAIN_NAME"))
		client_website = "https://" + site.site.domain + "/"
		my_etiquette = Etiquette(site.title, "v8", client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = None
		if title != None:
			i = 0
			work = works.query(bibliographic=title).sort("relevance")
			for w in work:
				if "title" in w:
					crossref_title = ""
					article_title = re.sub(r"[^A-Za-z0-9 ]+", "", title)
					article_title = re.sub(r" ", "", article_title).lower()
					crossref_title = re.sub(r"[^A-Za-z0-9 ]+", "", w["title"][0])
					crossref_title = re.sub(r" ", "", crossref_title).lower()
					if crossref_title == article_title:
						self.doi = w["DOI"]
						return self.doi
					i += 1
					if i == 5:
						return None

	@staticmethod
	def is_crossref_failed(refresh_result) -> bool:
		"""Return True if a CrossRef refresh() call returned an error string."""
		return isinstance(refresh_result, str) and any(
			keyword in refresh_result.lower()
			for keyword in ["error", "not found", "json decode"]
		)


class ClinicalTrial:
	def __init__(
		self,
		title=None,
		summary=None,
		link=None,
		published_date=None,
		identifiers=None,
		extra_fields=None,
	):
		self.title = title
		self.summary = summary
		self.link = link
		self.published_date = published_date
		self.identifiers = identifiers
		self.extra_fields = extra_fields or {}

	def __str__(self):
		return f"{self.title}, {self.identifiers}"

	def __repr__(self):
		return f'{self.title}, "{self.identifiers}"'

	def clean_summary(self=None, summary=None):
		from bs4 import BeautifulSoup
		import html

		if summary == None and self.summary != None:
			summary = self.summary
		if summary != None:
			summary = html.unescape(summary)
			soup = BeautifulSoup(summary, "html.parser")
			for tag in soup():
				for attribute in ["class", "id", "name", "style"]:
					del tag[attribute]
			return str(soup)

	def clean_url(self=None):
		from gregory.functions import remove_utm

		if self.link != None:
			self.link = remove_utm(self.link)
		else:
			logging.warning("no url found")


class ClinicalTrialsGovAPI:
	"""
	Client for ClinicalTrials.gov REST API v2.

	API Documentation: https://clinicaltrials.gov/data-api/api

	Usage:
		api = ClinicalTrialsGovAPI()

		# Search by condition
		studies = api.search(query_cond='multiple sclerosis', page_size=10)

		# Search by general terms
		studies = api.search(query_term='rituximab', page_size=20)

		# Get a single study by NCT ID
		study = api.get_study('NCT01234567')
	"""

	BASE_URL = "https://clinicaltrials.gov/api/v2"

	def __init__(self):
		import requests

		self.session = requests.Session()
		# Set a reasonable timeout
		self.timeout = 30

	def _make_request(self, endpoint, params=None):
		"""Make a request to the ClinicalTrials.gov API."""
		import requests

		url = f"{self.BASE_URL}/{endpoint}"
		try:
			response = self.session.get(url, params=params, timeout=self.timeout)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.HTTPError as e:
			logging.error(f"HTTP Error: {e}")
			raise
		except requests.exceptions.RequestException as e:
			logging.error(f"Request Error: {e}")
			raise

	def get_version(self):
		"""Get the API version and data timestamp."""
		return self._make_request("version")

	def get_study(self, nct_id: str, fields: list = None):
		"""
		Get a single study by NCT ID.

		Args:
			nct_id: The NCT identifier (e.g., 'NCT01234567')
			fields: Optional list of fields to return. If None, returns all fields.

		Returns:
			dict: Study data
		"""
		params = {}
		if fields:
			params["fields"] = "|".join(fields)
		return self._make_request(f"studies/{nct_id}", params)

	def search(
		self,
		query_term: str = None,
		query_cond: str = None,
		query_intr: str = None,
		query_titles: str = None,
		query_outc: str = None,
		query_spons: str = None,
		query_lead: str = None,
		query_id: str = None,
		query_patient: str = None,
		filter_overall_status: list = None,
		filter_geo: str = None,
		filter_ids: list = None,
		filter_advanced: str = None,
		aggFilters: str = None,
		geo_dist: str = None,
		post_filter: dict = None,
		fields: list = None,
		sort: list = None,
		count_total: bool = True,
		page_size: int = 10,
		page_token: str = None,
	):
		"""
		Search for studies using the ClinicalTrials.gov API.

		Search Parameters (query.*):
			query_term: Basic search across weighted fields (query.term)
			query_cond: Condition or disease search (query.cond)
			query_intr: Intervention/treatment search (query.intr)
			query_titles: Title/acronym search (query.titles)
			query_outc: Outcome measure search (query.outc)
			query_spons: Sponsor/collaborator search (query.spons)
			query_lead: Lead sponsor search (query.lead)
			query_id: Study ID search (query.id)
			query_patient: Patient-friendly search (query.patient)

		Filter Parameters (filter.*):
			filter_overall_status: List of recruitment statuses
			filter_geo: Geographic filter (e.g., "distance(39.0035707,-77.1013313,50mi)")
			filter_ids: List of NCT IDs to filter by
			filter_advanced: Advanced filter expression

		Other Parameters:
			aggFilters: Aggregation filters
			geo_dist: Geographic distance for location-based search
			post_filter: Post-query filters
			fields: List of fields to return
			sort: Sort order (e.g., ['LastUpdatePostDate:desc'])
			count_total: Whether to include total count
			page_size: Number of results per page (max 1000)
			page_token: Token for pagination

		Returns:
			dict: Search results with 'studies' list and pagination info
		"""
		params = {}

		# Query parameters
		if query_term:
			params["query.term"] = query_term
		if query_cond:
			params["query.cond"] = query_cond
		if query_intr:
			params["query.intr"] = query_intr
		if query_titles:
			params["query.titles"] = query_titles
		if query_outc:
			params["query.outc"] = query_outc
		if query_spons:
			params["query.spons"] = query_spons
		if query_lead:
			params["query.lead"] = query_lead
		if query_id:
			params["query.id"] = query_id
		if query_patient:
			params["query.patient"] = query_patient

		# Filter parameters
		if filter_overall_status:
			params["filter.overallStatus"] = "|".join(filter_overall_status)
		if filter_geo:
			params["filter.geo"] = filter_geo
		if filter_ids:
			params["filter.ids"] = "|".join(filter_ids)
		if filter_advanced:
			params["filter.advanced"] = filter_advanced

		# Other parameters
		if aggFilters:
			params["aggFilters"] = aggFilters
		if geo_dist:
			params["geoDecay"] = geo_dist
		if post_filter:
			import json

			params["postFilter"] = json.dumps(post_filter)
		if fields:
			params["fields"] = "|".join(fields)
		if sort:
			params["sort"] = ",".join(sort)
		if count_total:
			params["countTotal"] = "true"

		params["pageSize"] = min(page_size, 1000)  # API max is 1000

		if page_token:
			params["pageToken"] = page_token

		return self._make_request("studies", params)

	@staticmethod
	def extract_countries(study_data: dict) -> str | None:
		"""Extract the sorted, deduped list of site countries from a CTGov study's
		``contactsLocationsModule``, joined with ", " — the same string stored in
		``Trials.countries`` / ``Trials.countries_by_source["ctgov"]``.

		Shared between ``parse_study_to_clinical_trial`` and the
		``backfill_trial_countries`` management command so the extraction logic (and its
		join delimiter) lives in exactly one place. Returns None when the study has no
		site locations with a country.
		"""
		protocol = study_data.get("protocolSection", {})
		contacts_module = protocol.get("contactsLocationsModule", {})
		locations = contacts_module.get("locations", [])
		countries = sorted(
			set(loc.get("country", "") for loc in locations if loc.get("country"))
		)
		return ", ".join(countries) if countries else None

	@staticmethod
	def extract_sponsor_fields(study_data: dict) -> dict:
		"""Extract primary_sponsor / lead_sponsor_class / secondary_sponsor from a CTGov
		study's ``sponsorCollaboratorsModule``.

		Shared between ``parse_study_to_clinical_trial`` and the
		``backfill_trial_sponsors_from_ctgov`` management command — same rationale as
		``extract_countries`` above — so the two can never disagree on how a sponsor is
		read out of the raw API response.
		"""
		sponsor_module = study_data.get("protocolSection", {}).get(
			"sponsorCollaboratorsModule", {}
		)
		lead_sponsor = sponsor_module.get("leadSponsor", {})
		collaborators = [
			collab["name"]
			for collab in sponsor_module.get("collaborators", [])
			if collab.get("name")
		]
		clean_collabs = [name.strip() for name in collaborators if name.strip()]
		return {
			"primary_sponsor": lead_sponsor.get("name"),
			"lead_sponsor_class": lead_sponsor.get("class"),
			"secondary_sponsor": "; ".join(clean_collabs) if clean_collabs else None,
		}

	def search_all(self, max_results: int = None, **search_kwargs):
		"""
		Search and iterate through all pages of results.

		Args:
			max_results: Maximum number of results to return (None for all)
			**search_kwargs: All parameters accepted by search()

		Yields:
			dict: Individual study records
		"""
		count = 0
		page_token = None

		while True:
			results = self.search(page_token=page_token, **search_kwargs)

			studies = results.get("studies", [])
			if not studies:
				break

			for study in studies:
				yield study
				count += 1
				if max_results and count >= max_results:
					return

			page_token = results.get("nextPageToken")
			if not page_token:
				break

	def parse_study_to_clinical_trial(self, study_data: dict) -> "ClinicalTrial":
		"""
		Convert a ClinicalTrials.gov API study response to a ClinicalTrial object.

		Args:
			study_data: Raw study data from the API

		Returns:
			ClinicalTrial: Parsed trial object
		"""
		from datetime import datetime
		import pytz

		protocol = study_data.get("protocolSection", {})
		identification = protocol.get("identificationModule", {})
		status_module = protocol.get("statusModule", {})
		description = protocol.get("descriptionModule", {})
		design_module = protocol.get("designModule", {})
		conditions_module = protocol.get("conditionsModule", {})
		eligibility_module = protocol.get("eligibilityModule", {})
		outcomes_module = protocol.get("outcomesModule", {})
		contacts_module = protocol.get("contactsLocationsModule", {})
		sponsor_module = protocol.get("sponsorCollaboratorsModule", {})

		# Extract NCT ID
		nct_id = identification.get("nctId")

		# Extract title (prefer official, fallback to brief)
		title = identification.get("officialTitle") or identification.get(
			"briefTitle", ""
		)

		# Extract acronym (Trials.acronym is capped at 200 chars)
		acronym = (identification.get("acronym") or "").strip()[:200] or None

		# Build link
		link = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None

		# Extract summary/brief summary
		summary = description.get("briefSummary", "")
		detailed_description = description.get("detailedDescription", "")

		# Parse dates
		published_date = None
		start_date_struct = status_module.get("startDateStruct", {})
		if start_date_struct.get("date"):
			try:
				date_str = start_date_struct["date"]
				# Handle partial dates (YYYY or YYYY-MM or YYYY-MM-DD)
				if len(date_str) == 4:
					published_date = datetime(int(date_str), 1, 1, tzinfo=pytz.UTC)
				elif len(date_str) == 7:
					parts = date_str.split("-")
					published_date = datetime(
						int(parts[0]), int(parts[1]), 1, tzinfo=pytz.UTC
					)
				else:
					published_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
						tzinfo=pytz.UTC
					)
			except (ValueError, TypeError):
				pass

		# Build identifiers
		identifiers = {
			"nct": nct_id,
			"org_study_id": identification.get("orgStudyIdInfo", {}).get("id"),
		}

		# Extract secondary IDs
		secondary_ids = []
		for sec_id_info in identification.get("secondaryIdInfos", []):
			if sec_id_info.get("id"):
				secondary_ids.append(sec_id_info["id"])

		# Extract conditions
		conditions = conditions_module.get("conditions", [])

		# Extract phase
		phases = design_module.get("phases", [])
		phase = ", ".join(phases) if phases else None

		# Extract study type
		study_type = design_module.get("studyType")

		# Extract recruitment status
		recruitment_status = status_module.get("overallStatus")

		# Extract eligibility criteria
		eligibility_criteria = eligibility_module.get("eligibilityCriteria", "")
		min_age = eligibility_module.get("minimumAge")
		max_age = eligibility_module.get("maximumAge")
		gender = eligibility_module.get("sex")

		# Extract primary outcome measures
		primary_outcomes = []
		for outcome in outcomes_module.get("primaryOutcomes", []):
			measure = outcome.get("measure", "")
			description_text = outcome.get("description", "")
			time_frame = outcome.get("timeFrame", "")
			primary_outcomes.append(f"{measure}: {description_text} ({time_frame})")

		# Extract secondary outcome measures
		secondary_outcomes = []
		for outcome in outcomes_module.get("secondaryOutcomes", []):
			measure = outcome.get("measure", "")
			description_text = outcome.get("description", "")
			time_frame = outcome.get("timeFrame", "")
			secondary_outcomes.append(f"{measure}: {description_text} ({time_frame})")

		# Extract sponsor information (shared with backfill_trial_sponsors_from_ctgov —
		# see extract_sponsor_fields)
		sponsor_fields = self.extract_sponsor_fields(study_data)
		primary_sponsor = sponsor_fields["primary_sponsor"]
		lead_sponsor_class = sponsor_fields["lead_sponsor_class"]
		secondary_sponsor = sponsor_fields["secondary_sponsor"]

		# Extract intervention information
		arms_module = protocol.get("armsInterventionsModule", {})
		interventions = []
		for intervention in arms_module.get("interventions", []):
			int_type = intervention.get("type", "")
			int_name = intervention.get("name", "")
			int_desc = intervention.get("description", "")
			interventions.append(f"{int_type}: {int_name} - {int_desc}")

		# Extract locations/countries
		countries_str = self.extract_countries(study_data)

		# Extract contact information
		central_contacts = contacts_module.get("centralContacts", [])
		contact_info = {}
		if central_contacts:
			first_contact = central_contacts[0]
			contact_info = {
				"name": first_contact.get("name"),
				"email": first_contact.get("email"),
				"phone": first_contact.get("phone"),
			}

		# Extract results availability and dates
		has_results = bool(study_data.get("hasResults", False))
		results_url_link = (
			f"https://clinicaltrials.gov/study/{nct_id}?tab=results"
			if (has_results and nct_id)
			else None
		)
		results_date_completed = self._parse_date(
			status_module.get("resultsFirstPostDateStruct", {}).get("date")
		)

		# study_design — compose human-readable string from designInfo
		design_info = design_module.get("designInfo", {})
		study_design = None
		if design_info:
			parts = []
			if design_info.get("allocation"):
				parts.append(f"Allocation: {design_info['allocation']}")
			if design_info.get("interventionModel"):
				parts.append(f"Intervention model: {design_info['interventionModel']}")
			masking_info = design_info.get("maskingInfo", {})
			if masking_info.get("masking"):
				who_masked = masking_info.get("whoMasked", [])
				masking_str = masking_info["masking"]
				if who_masked:
					masking_str += f" ({', '.join(sorted(who_masked))})"
				parts.append(f"Masking: {masking_str}")
			if design_info.get("primaryPurpose"):
				parts.append(f"Primary purpose: {design_info['primaryPurpose']}")
			if design_info.get("observationalModel"):
				parts.append(
					f"Observational model: {design_info['observationalModel']}"
				)
			if design_info.get("timePerspective"):
				parts.append(f"Time perspective: {design_info['timePerspective']}")
			study_design = ". ".join(parts) if parts else None

		# results_ipd_plan / results_ipd_description
		ipd_module = protocol.get("ipdSharingStatementModule", {})
		results_ipd_plan = ((ipd_module.get("ipdSharing") or "").strip()[:10]) or None
		_ipd_desc = (ipd_module.get("description") or "").strip()
		results_ipd_description = _ipd_desc or None

		# last_refreshed_on
		last_refreshed_on = self._parse_date(
			status_module.get("lastUpdatePostDateStruct", {}).get("date")
		)

		# date_enrollement — same start date already used for published_date
		date_enrollement = self._parse_date(start_date_struct.get("date"))

		# contact_affiliation — first overall official's affiliation
		overall_officials = contacts_module.get("overallOfficials", [])
		_raw_affiliation = (
			overall_officials[0].get("affiliation", "") if overall_officials else ""
		)
		contact_affiliation = _raw_affiliation.strip() or None

		# Build extra_fields for ClinicalTrial object
		extra_fields = {
			"scientific_title": identification.get("officialTitle"),
			"acronym": acronym,
			"recruitment_status": recruitment_status,
			"date_registration": self._parse_date(
				status_module.get("studyFirstSubmitDate")
			),
			"study_type": study_type,
			"phase": phase,
			"countries": countries_str,
			"inclusion_criteria": eligibility_criteria,
			"exclusion_criteria": None,  # API combines inclusion/exclusion in eligibilityCriteria
			"intervention": "\n".join(interventions) if interventions else None,
			"secondary_id": ", ".join(secondary_ids) if secondary_ids else None,
			"condition": ", ".join(conditions) if conditions else None,
			"primary_outcome": "\n".join(primary_outcomes)
			if primary_outcomes
			else None,
			"secondary_outcome": "\n".join(secondary_outcomes)
			if secondary_outcomes
			else None,
			"primary_sponsor": primary_sponsor,
			"lead_sponsor_class": lead_sponsor_class,
			"inclusion_agemin": min_age,
			"inclusion_agemax": max_age,
			"inclusion_gender": gender,
			"target_size": str(design_module.get("enrollmentInfo", {}).get("count", ""))
			or None,
			"contact_firstname": contact_info.get("name", "").split()[0]
			if contact_info.get("name")
			else None,
			"contact_lastname": " ".join(contact_info.get("name", "").split()[1:])
			if contact_info.get("name")
			else None,
			"contact_email": contact_info.get("email"),
			"contact_tel": contact_info.get("phone"),
			"source_register": "ClinicalTrials.gov",
			"ctg_detailed_description": detailed_description,
			"results_posted": has_results,
			"results_url_link": results_url_link,
			"results_date_completed": results_date_completed,
			"study_design": study_design,
			"results_ipd_plan": results_ipd_plan,
			"results_ipd_description": results_ipd_description,
			"secondary_sponsor": secondary_sponsor,
			"last_refreshed_on": last_refreshed_on,
			"date_enrollement": date_enrollement,
			"contact_affiliation": contact_affiliation,
		}

		return ClinicalTrial(
			title=title,
			summary=summary,
			link=link,
			published_date=published_date,
			identifiers=identifiers,
			extra_fields=extra_fields,
		)

	def _parse_date(self, date_str: str):
		"""Parse a date string from the API into a date object."""
		from datetime import datetime

		if not date_str:
			return None
		try:
			# Handle YYYY-MM-DD format
			return datetime.strptime(date_str, "%Y-%m-%d").date()
		except ValueError:
			try:
				# Handle YYYY-MM format
				return datetime.strptime(date_str, "%Y-%m").date()
			except ValueError:
				try:
					# Handle YYYY format
					return datetime.strptime(date_str, "%Y").date()
				except ValueError:
					return None


class EUTrialParser:
	"""Parse euclinicaltrials.eu (EU CTIS) RSS feed entries into structured trial data.

	Keeps all EU-specific extraction in one place, mirroring how ClinicalTrialsGovAPI
	handles ClinicalTrials.gov data.
	"""

	SOURCE_REGISTER = "EU CTIS"

	def extract_identifiers(self, link: str, guid: str) -> dict:
		"""Extract registry identifiers from an RSS entry's link and guid.

		eudract / euct come from the link query string; nct comes from the guid
		when the entry points at ClinicalTrials.gov.
		"""
		import re

		eudract = re.search(
			r"(?:eudract_number%3A|EUDRACT=)(\d{4}-\d{6}-\d{2}-\d{2})",
			link,
			re.IGNORECASE,
		)
		euct = re.search(r"(?:EUCT=)(\d{4}-\d{6}-\d{2}-\d{2})", link, re.IGNORECASE)
		nct = guid if "clinicaltrials.gov" in link else None
		return {
			"eudract": eudract.group(1) if eudract else None,
			"nct": nct,
			"euct": euct.group(1) if euct else None,
		}

	def parse_summary(self, summary_html: str) -> dict:
		"""Extract EU CTIS fields from the RSS summary HTML.

		Returns a dict of extra_fields, including source_register and the EU-specific
		columns. recruitment_status is derived from the "Overall trial status" line.
		"""
		import re
		from dateutil.parser import parse

		def _extract(pattern):
			match = re.search(pattern, summary_html, re.IGNORECASE)
			if not match:
				return None
			# Strip any leading colon/whitespace left over from the label
			return match.group(1).lstrip(": ").strip()

		therapeutic_areas = _extract(r"Therapeutic Areas[^>]*>([^<]+)")
		country_status = _extract(r"Status in each country[^>]*>([^<]+)")
		trial_region = _extract(r"Trial region[^>]*>([^<]+)")
		# Only commit to a boolean when the feed explicitly states it. If the line is
		# absent, leave results_posted as None so the non-destructive update guard skips
		# it and doesn't blank a value set by another source (e.g. ClinicalTrials.gov).
		results_posted_str = _extract(r"Results posted[^>]*>([^<]+)")
		results_posted = (
			(results_posted_str.lower() == "yes") if results_posted_str else None
		)
		medical_conditions = _extract(r"Medical conditions[^>]*>([^<]+)")
		overall_status = _extract(r"Overall trial status[^>]*>([^<]+)")
		primary_end_point = _extract(r"Primary end point[^>]*>([^<]+)")
		secondary_end_point = _extract(r"Secondary end point[^>]*>([^<]+)")
		overall_decision_date_str = _extract(r"Overall decision date[^>]*>([^<]+)")
		countries_decision_date_str = _extract(r"Countries decision date[^>]*>([^<]+)")
		sponsor = _extract(r"Sponsor[^>]*>([^<]+)")
		sponsor_type = _extract(r"Sponsor type[^>]*>([^<]+)")
		phase = _extract(r"Trial phase[^>]*>([^<]+)")
		gender = _extract(r"Gender of participants[^>]*>([^<]+)")
		target_size = _extract(r"Planned number of participants[^>]*>([^<]+)")
		intervention = _extract(r"Trial product[^>]*>([^<]+)")

		# "Age of participants" looks like "18-64 years"; split into min/max.
		age_min = age_max = None
		age_raw = _extract(r"Age of participants[^>]*>([^<]+)")
		if age_raw:
			age_match = re.search(r"(\d+)\s*-\s*(\d+)", age_raw)
			if age_match:
				age_min, age_max = age_match.group(1), age_match.group(2)

		# "Last updated date" is day-first (DD/MM/YYYY).
		last_refreshed_on = None
		last_updated_str = _extract(r"Last updated date[^>]*>([^<]+)")
		if last_updated_str:
			try:
				last_refreshed_on = parse(last_updated_str, dayfirst=True).date()
			except (ValueError, TypeError):
				pass

		# EU CTIS feed dates are day-first (DD/MM/YYYY); without dayfirst=True,
		# dateutil misreads e.g. 08/12/2025 (8 Dec) as 12 Aug.
		overall_decision_date = None
		if overall_decision_date_str:
			try:
				overall_decision_date = parse(
					overall_decision_date_str, dayfirst=True
				).date()
			except (ValueError, TypeError):
				pass

		countries_decision_date = {}
		if countries_decision_date_str:
			for chunk in re.split(r"[;,]", countries_decision_date_str):
				chunk_parts = chunk.strip().split(":")
				if len(chunk_parts) == 2:
					country_code = chunk_parts[0].strip()
					date_val = chunk_parts[1].strip()
					try:
						countries_decision_date[country_code] = str(
							parse(date_val, dayfirst=True).date()
						)
					except (ValueError, TypeError):
						countries_decision_date[country_code] = date_val

		return {
			"source_register": self.SOURCE_REGISTER,
			"condition": medical_conditions,
			"recruitment_status": overall_status,
			"primary_sponsor": sponsor,
			"primary_outcome": primary_end_point,
			"secondary_outcome": secondary_end_point,
			"therapeutic_areas": therapeutic_areas,
			"country_status": country_status,
			"trial_region": trial_region,
			"results_posted": results_posted,
			"overall_decision_date": overall_decision_date,
			"countries_decision_date": countries_decision_date
			if countries_decision_date
			else None,
			"sponsor_type": sponsor_type,
			"phase": phase,
			"intervention": intervention,
			"inclusion_agemin": age_min,
			"inclusion_agemax": age_max,
			"inclusion_gender": gender,
			"target_size": target_size,
			"last_refreshed_on": last_refreshed_on,
		}


class CTISPublicAPIError(Exception):
	"""Raised when the CTIS public API returns a response we can't parse: non-JSON
	body, or valid JSON missing the expected 'data' envelope key. Callers (the
	feedreader command) catch this per-source, log it, and skip the source rather
	than aborting the whole run."""


class CTISPublicAPI:
	"""
	Client for the undocumented CTIS public search API (euclinicaltrials.eu).

	No official spec is published by EMA; this was reverse-engineered from the network
	calls the public search portal (https://euclinicaltrials.eu/ctis-public/search)
	makes in a browser. See docs/ctis-public-api-schema.md for the full observed
	request/response contract, verified live 2026-07-18. If the API drifts, re-derive
	the contract from the portal's network calls.

	Usage:
		api = CTISPublicAPI()
		for record in api.iter_search({"medicalCondition": "Multiple Sclerosis"}):
			clinical_trial = api.parse_ctis_search_record(record)
	"""

	BASE_URL = "https://euclinicaltrials.eu/ctis-public-api"
	SOURCE_REGISTER = "EU CTIS"

	def __init__(self):
		import requests

		self.session = requests.Session()
		self.timeout = 30

	def search(
		self,
		criteria: dict,
		page: int = 1,
		size: int = 50,
		sort_property: str = "lastPublicationUpdate",
		direction: str = "DESC",
	) -> dict:
		"""POST one page of /ctis-public-api/search.

		Returns the parsed JSON envelope: {"pagination": {...}, "data": [...]}.
		Raises CTISPublicAPIError if the response isn't JSON or is missing the
		'data' key; raises requests' own exceptions on transport/HTTP failures.
		"""
		import requests

		payload = {
			"searchCriteria": criteria or {},
			"pagination": {"page": page, "size": size},
			"sort": {"property": sort_property, "direction": direction},
		}
		url = f"{self.BASE_URL}/search"
		try:
			response = self.session.post(url, json=payload, timeout=self.timeout)
			response.raise_for_status()
		except requests.exceptions.HTTPError as e:
			logging.error(f"HTTP Error: {e}")
			raise
		except requests.exceptions.RequestException as e:
			logging.error(f"Request Error: {e}")
			raise

		try:
			result = response.json()
		except ValueError as e:
			raise CTISPublicAPIError(f"Non-JSON response from CTIS search: {e}") from e

		if not isinstance(result, dict) or "data" not in result:
			raise CTISPublicAPIError(
				f"Unexpected CTIS search response shape (missing 'data' key): {str(result)[:200]!r}"
			)

		return result

	def retrieve(self, ct_number: str) -> dict:
		"""GET /ctis-public-api/retrieve/{ctNumber} — the full single-trial dossier
		(~85 KB of nested JSON; see docs/ctis-public-api-schema.md for the shape).

		This is a separate, much heavier request than search() — one GET per trial,
		not paginated. Callers are responsible for keeping the call volume bounded
		(e.g. one per record already returned by a /search run).

		Raises CTISPublicAPIError if the response isn't JSON or isn't an object;
		raises requests' own exceptions on transport/HTTP failures.
		"""
		import requests

		url = f"{self.BASE_URL}/retrieve/{ct_number}"
		try:
			response = self.session.get(url, timeout=self.timeout)
			response.raise_for_status()
		except requests.exceptions.HTTPError as e:
			logging.error(f"HTTP Error: {e}")
			raise
		except requests.exceptions.RequestException as e:
			logging.error(f"Request Error: {e}")
			raise

		try:
			result = response.json()
		except ValueError as e:
			raise CTISPublicAPIError(f"Non-JSON response from CTIS retrieve: {e}") from e

		if not isinstance(result, dict):
			raise CTISPublicAPIError(
				f"Unexpected CTIS retrieve response shape (not an object): {str(result)[:200]!r}"
			)

		return result

	@staticmethod
	def record_is_stale(record: dict, since) -> bool:
		"""True if *record* is outside the incremental window: both lastUpdated and
		lastPublicationUpdate are older than *since* (or absent/unparsable).

		Shared by iter_search (to decide whether to keep paging) and by callers that
		want to skip expensive per-record work — e.g. feedreader_trials_ctis skips
		the /retrieve backup GET for stale records instead of firing one for every
		record a page happens to include.
		"""
		from dateutil.parser import parse as parse_date

		for date_field in ("lastUpdated", "lastPublicationUpdate"):
			raw = record.get(date_field)
			if not raw:
				continue
			try:
				parsed = parse_date(raw, dayfirst=True).date()
			except (ValueError, TypeError):
				continue
			if parsed >= since:
				return False
		return True

	def iter_search(self, criteria: dict, since=None, size: int = 50, sleep: float = 0.5):
		"""Iterate every record across all pages of /search for *criteria*.

		Pages are fetched sorted by lastPublicationUpdate DESC (the default), so the
		newest updates come first. In incremental mode (*since* is a date), paging
		continues until an entire page is stale on BOTH lastUpdated and
		lastPublicationUpdate — the observed sort is not strictly monotonic (see
		docs/ctis-public-api-schema.md), so a single stale record within a page must
		never stop the walk early; only a wholly-stale page does. Individual stale
		records are still yielded (so cheap DB non-destructive-updates still run for
		them) — callers doing expensive per-record work should check
		record_is_stale() themselves rather than assume every yielded record is fresh.
		"""
		import time

		page = 1
		while True:
			result = self.search(criteria, page=page, size=size)
			records = result.get("data") or []
			if not records:
				return

			page_is_stale = since is not None
			for record in records:
				if since is not None and not self.record_is_stale(record, since):
					page_is_stale = False
				yield record

			if page_is_stale:
				return

			pagination = result.get("pagination") or {}
			if not pagination.get("nextPage"):
				return
			page += 1
			if sleep:
				time.sleep(sleep)

	def parse_ctis_search_record(self, record: dict) -> "ClinicalTrial":
		"""Map one CTIS /search API record into a ClinicalTrial.

		Writes the SAME raw field values/formats EUTrialParser.parse_summary (the RSS
		path) writes into extra_fields, so every downstream normalizer
		(recruitment_status_normalized, TrialCountry sync via normalize_countries, …)
		behaves identically regardless of which channel a trial arrived through. See
		the mapping table in docs/ctis-public-api-schema.md.
		"""
		import re
		from datetime import datetime
		import pytz
		from dateutil.parser import parse as parse_date
		from gregory.utils.ctis_codes import (
			CTIS_PUBLIC_STATUS_LABELS,
			CTIS_TRIAL_REGION_LABELS,
		)

		ct_number = record.get("ctNumber")
		title = record.get("ctTitle")

		# Mirrors EUTrialParser.extract_identifiers exactly (same "euct" key, same bare
		# ctNumber value, no prefix) so a CTIS-API-created row and an RSS-created row
		# for the same trial carry identical identifiers and can be matched/merged.
		identifiers = {"eudract": None, "nct": None, "euct": ct_number}

		link = (
			f"https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT={ct_number}"
			if ct_number
			else None
		)

		def _or_none(value):
			return value if value else None

		def _day_first(value):
			if not value:
				return None
			try:
				return parse_date(value, dayfirst=True).date()
			except (ValueError, TypeError):
				return None

		recruitment_status = None
		status_code = record.get("ctStatus")
		if isinstance(status_code, int):
			label = CTIS_PUBLIC_STATUS_LABELS.get(status_code)
			if label:
				recruitment_status = label
			else:
				logging.warning(
					f"CTIS search: unknown ctStatus code {status_code} for {ct_number}; "
					"leaving recruitment_status unset"
				)

		country_status_parts = []
		for entry in record.get("trialCountries") or []:
			name, sep, code_str = entry.partition(":")
			if not sep:
				continue
			try:
				code = int(code_str)
			except ValueError:
				continue
			label = CTIS_PUBLIC_STATUS_LABELS.get(code)
			if not label:
				logging.warning(
					f"CTIS search: unknown country status code {code} ({name.strip()}) "
					f"for {ct_number}; omitting from country_status"
				)
				continue
			country_status_parts.append(f"{name.strip()}:{label}")
		country_status = ", ".join(country_status_parts) if country_status_parts else None

		# Per-country decision dates: "IT: 24/06/2026, ES: 26/06/2026" -> already
		# ISO alpha-2 keys, same day-first parsing as EUTrialParser.
		countries_decision_date = {}
		decision_date_str = record.get("decisionDate")
		if decision_date_str:
			for chunk in re.split(r"[;,]", decision_date_str):
				chunk_parts = chunk.strip().split(":")
				if len(chunk_parts) == 2:
					country_code = chunk_parts[0].strip()
					date_val = chunk_parts[1].strip()
					parsed = _day_first(date_val)
					countries_decision_date[country_code] = str(parsed) if parsed else date_val

		overall_decision_date = _day_first(record.get("decisionDateOverall"))

		trial_region = None
		region_code = record.get("trialRegion")
		if isinstance(region_code, int):
			label = CTIS_TRIAL_REGION_LABELS.get(region_code)
			if label:
				trial_region = label
			else:
				logging.warning(
					f"CTIS search: unmapped trialRegion code {region_code} for {ct_number}; "
					"leaving trial_region unset"
				)

		results_posted_str = record.get("resultsFirstReceived")
		results_posted = (
			(results_posted_str.lower() == "yes") if results_posted_str else None
		)

		# sponsorType is comma-duplicated for multi-sponsor trials ("Hospital/…,
		# Hospital/…"); dedupe while preserving order, keep distinct values ", "-joined.
		sponsor_type = None
		sponsor_type_raw = record.get("sponsorType")
		if sponsor_type_raw:
			seen = []
			for part in sponsor_type_raw.split(","):
				part = part.strip()
				if part and part not in seen:
					seen.append(part)
			sponsor_type = ", ".join(seen) if seen else None

		therapeutic_areas_list = record.get("therapeuticAreas") or []
		therapeutic_areas = (
			", ".join(therapeutic_areas_list) if therapeutic_areas_list else None
		)

		# "Age of participants" is "18-64 years"; split into min/max strings (matches
		# Trials.inclusion_agemin/agemax being CharField, and EUTrialParser's split).
		age_min = age_max = None
		age_raw = record.get("ageGroup")
		if age_raw:
			age_match = re.search(r"(\d+)\s*-\s*(\d+)", age_raw)
			if age_match:
				age_min, age_max = age_match.group(1), age_match.group(2)

		last_updated = _day_first(record.get("lastUpdated"))
		last_pub_update = _day_first(record.get("lastPublicationUpdate"))
		candidate_dates = [d for d in (last_updated, last_pub_update) if d]
		last_refreshed_on = max(candidate_dates) if candidate_dates else None

		published_date = None
		if last_pub_update:
			published_date = datetime(
				last_pub_update.year, last_pub_update.month, last_pub_update.day, tzinfo=pytz.UTC
			)

		extra_fields = {
			"source_register": self.SOURCE_REGISTER,
			"condition": _or_none(record.get("conditions")),
			"recruitment_status": recruitment_status,
			"primary_sponsor": _or_none(record.get("sponsor")),
			"primary_outcome": _or_none(record.get("primaryEndPoint")),
			"secondary_outcome": _or_none(record.get("endPoint")),
			"therapeutic_areas": therapeutic_areas,
			"country_status": country_status,
			"trial_region": trial_region,
			"results_posted": results_posted,
			"overall_decision_date": overall_decision_date,
			"countries_decision_date": countries_decision_date or None,
			"sponsor_type": sponsor_type,
			"phase": _or_none(record.get("trialPhase")),
			"intervention": _or_none(record.get("product")),
			"inclusion_agemin": age_min,
			"inclusion_agemax": age_max,
			"inclusion_gender": _or_none(record.get("gender")),
			"target_size": _or_none(record.get("totalNumberEnrolled")),
			"last_refreshed_on": last_refreshed_on,
		}

		# summary is composed only as a fill-once fallback for trials that have none —
		# the command must never overwrite an existing summary with this. Mirrors the
		# RSS description's "<b>Label</b>: value<br/>" labeled-line format.
		summary = self._compose_summary(record, extra_fields)

		return ClinicalTrial(
			title=title,
			summary=summary,
			link=link,
			published_date=published_date,
			identifiers=identifiers,
			extra_fields=extra_fields,
		)

	@staticmethod
	def _compose_summary(record: dict, extra_fields: dict) -> str | None:
		"""Deterministically compose a labeled-line summary from mapped fields, for
		trials that don't have one yet. Mirrors the label text used in the RSS feed
		description so a human reading either channel's summary sees the same shape."""
		from datetime import date

		lines = []

		def add(label, value):
			if value:
				lines.append(f"<b>{label}</b>: {value}<br/>")

		add("Trial number", record.get("ctNumber"))
		add("Overall trial status", extra_fields.get("recruitment_status"))
		add("Trial title", record.get("ctTitle"))
		add("Medical conditions", extra_fields.get("condition"))
		add("Status in each country", extra_fields.get("country_status"))
		add("Trial phase", extra_fields.get("phase"))
		add("Therapeutic Areas", extra_fields.get("therapeutic_areas"))
		add("Primary end point", extra_fields.get("primary_outcome"))
		add("Secondary end point", extra_fields.get("secondary_outcome"))
		add("Age of participants", record.get("ageGroup"))
		add("Gender of participants", extra_fields.get("inclusion_gender"))
		add("Trial region", extra_fields.get("trial_region"))
		add("Planned number of participants", extra_fields.get("target_size"))
		add("Sponsor", extra_fields.get("primary_sponsor"))
		add("Sponsor type", extra_fields.get("sponsor_type"))
		add("Trial product", extra_fields.get("intervention"))

		# results_posted is a bool (or None); "No" is a real, meaningful value that
		# add()'s truthiness check would otherwise silently drop.
		results_posted = extra_fields.get("results_posted")
		if results_posted is not None:
			add("Results posted", "Yes" if results_posted else "No")

		# Day-first (DD/MM/YYYY) to match the RSS description's date format exactly.
		overall_decision_date = extra_fields.get("overall_decision_date")
		if overall_decision_date:
			add("Overall decision date", overall_decision_date.strftime("%d/%m/%Y"))

		countries_decision_date = extra_fields.get("countries_decision_date")
		if countries_decision_date:
			parts = []
			for country_code, iso_date in countries_decision_date.items():
				try:
					parsed = date.fromisoformat(iso_date)
					parts.append(f"{country_code}: {parsed.strftime('%d/%m/%Y')}")
				except (ValueError, TypeError):
					parts.append(f"{country_code}: {iso_date}")
			add("Countries decision date", ", ".join(parts))

		last_refreshed_on = extra_fields.get("last_refreshed_on")
		if last_refreshed_on:
			add("Last updated date", last_refreshed_on.strftime("%d/%m/%Y"))

		return "".join(lines) or None
