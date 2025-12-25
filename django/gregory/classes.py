class SciencePaper:
	def __init__(self, doi=None, title=None,link=None,access=None,publisher=None,journal=None,published_date=None,abstract=None,authors=None):
		self.doi=doi
		self.title=title
		self.link=link
		self.access=access
		self.publisher=publisher
		self.journal=journal
		self.published_date=published_date
		self.abstract=abstract
		self.authors=authors
	def __str__(self):
		return f"{self.doi}, {self.title}"
	def __repr__(self):
		return f"{self.doi}, \"{self.title}\""
	def clean_abstract(self=None,abstract=None):
		from bs4 import BeautifulSoup
		import html
		if abstract == None and self.abstract != None:
			abstract = self.abstract
		if abstract != None:
			abstract = html.unescape(abstract)
			soup = BeautifulSoup(abstract,'html.parser')
			for tag in soup():
				for attribute in ["class", "id", "name", "style"]:
					del tag[attribute]
			return str(soup)

	def clean_url(self=None):
		from gregory.functions import remove_utm
		if self.link != None:
			self.link = remove_utm(self.link)
		else:
			print('no url found')


	def refresh(self):
		from gregory.unpaywall import unpaywall_utils
		from crossref.restful import Works, Etiquette
		import os
		import pytz
		from datetime import datetime
		from requests.exceptions import HTTPError, RequestException
		import json
		timezone = pytz.timezone('UTC')
		from sitesettings.models import CustomSetting
		site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		client_website = 'https://' + site.site.domain + '/'
		my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = None
		
		if self.doi != None:
			try:
				work = works.doi(self.doi)
			except HTTPError as e:
				if e.response.status_code == 404:
					print(f"DOI not found in CrossRef: {self.doi}")
					return 'DOI not found'
				else:
					print(f"CrossRef HTTP error for DOI {self.doi}: {e}")
					return f'CrossRef HTTP error: {e}'
			except json.JSONDecodeError as e:
				print(f"Error decoding JSON from CrossRef response for DOI: {self.doi}")
				return 'JSON decode error'
			except RequestException as e:
				print(f"CrossRef request error for DOI {self.doi}: {e}")
				return f'CrossRef request error: {e}'
			except Exception as e:
				print(f"Unexpected error querying CrossRef for DOI {self.doi}: {e}")
				return f'Unexpected error: {e}'
		else:
			return 'No DOI provided'
			
		# Only proceed if we successfully got work data
		if work is None:
			return 'No data retrieved from CrossRef'
			
		if self.link == None:
			try: 
				self.link = work['link'][0]['URL']
			except: 
				pass
		if self.title == None:
			try:
				self.title = work['title'][0]
			except:
				pass
		if self.doi != None and self.access == None:
			if site.admin_email == None:
				print("No site admin email found")
			else:
				try:
					if unpaywall_utils.checkIfDOIIsOpenAccess(self.doi, site.admin_email):
						self.access = 'open'
					else:
						self.access = 'restricted'
				except Exception as e:
					print(f"Error checking Unpaywall for DOI {self.doi}: {e}")
					self.access = 'unknown'
			
		if self.publisher == None:
			if work != None and 'publisher' in work:
				if isinstance(work['publisher'],list):
					self.publisher = work['publisher'][0]
				else:
					self.publisher = work['publisher']
		if self.journal == None:
			if work != None and 'container-title' in work:
				if isinstance(work['container-title'], list):
					if len(work['container-title']) > 0:
						self.journal = work['container-title'][0]
					else:
						self.journal = ''
				else:
					self.journal = work['container-title']
		if self.published_date == None:
			if work != None and 'issued' in work:
				issued = work['issued']['date-parts'][0]
			year,month,day = None,1,1
			try:
				year = issued[0]
			except:
				pass
			try:
				month=issued[1]
			except:
				pass
			try:
				day=issued[2]
			except:
				pass
			try:
				self.published_date = datetime( year=year, month=month, day=day, tzinfo=timezone)
			except:
					pass
		if self.abstract == None:
			try:
				self.abstract = work['abstract']
			except:
				pass
		if self.authors == None:
			try:
				self.authors = work['author']
			except:
				pass

	def find_doi(self,title=None):
		if title == None:
			return 'Missing required title field'
		import re
		import os
		from sitesettings.models import CustomSetting
		from crossref.restful import Works, Etiquette
		self.doi = None
		self.title = title
		site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		client_website = 'https://' + site.site.domain + '/'
		my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = None
		if title != None:
			i = 0
			work = works.query(bibliographic=title).sort('relevance')
			for w in work:
				if 'title' in w:
					crossref_title = ''
					article_title = re.sub(r'[^A-Za-z0-9 ]+', '', title)
					article_title = re.sub(r' ','',article_title ).lower()
					crossref_title = re.sub(r'[^A-Za-z0-9 ]+', '', w['title'][0])
					crossref_title = re.sub(r' ','',crossref_title).lower()
					if crossref_title == article_title:
						self.doi = w['DOI']
						return self.doi
					i += 1
					if i == 5:
						return None


class ClinicalTrial:
	def __init__(self, title=None, summary=None, link=None, published_date=None, identifiers=None, extra_fields=None):
		self.title = title
		self.summary = summary
		self.link = link
		self.published_date = published_date
		self.identifiers = identifiers
		self.extra_fields = extra_fields or {}
	def __str__(self):
		return f"{self.title}, {self.identifiers}"
	def __repr__(self):
		return f"{self.title}, \"{self.identifiers}\""

	def clean_summary(self=None,summary=None):
		from bs4 import BeautifulSoup
		import html
		if summary == None and self.summary != None:
			summary = self.summary
		if summary != None:
			summary = html.unescape(summary)
			soup = BeautifulSoup(summary,'html.parser')
			for tag in soup():
				for attribute in ["class", "id", "name", "style"]:
					del tag[attribute]
			return str(soup)

	def clean_url(self=None):
		from gregory.functions import remove_utm
		if self.link != None:
			self.link = remove_utm(self.link)
		else:
			print('no url found')


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
			print(f"HTTP Error: {e}")
			raise
		except requests.exceptions.RequestException as e:
			print(f"Request Error: {e}")
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
			params['fields'] = '|'.join(fields)
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
		page_token: str = None
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
			params['query.term'] = query_term
		if query_cond:
			params['query.cond'] = query_cond
		if query_intr:
			params['query.intr'] = query_intr
		if query_titles:
			params['query.titles'] = query_titles
		if query_outc:
			params['query.outc'] = query_outc
		if query_spons:
			params['query.spons'] = query_spons
		if query_lead:
			params['query.lead'] = query_lead
		if query_id:
			params['query.id'] = query_id
		if query_patient:
			params['query.patient'] = query_patient
		
		# Filter parameters
		if filter_overall_status:
			params['filter.overallStatus'] = '|'.join(filter_overall_status)
		if filter_geo:
			params['filter.geo'] = filter_geo
		if filter_ids:
			params['filter.ids'] = '|'.join(filter_ids)
		if filter_advanced:
			params['filter.advanced'] = filter_advanced
		
		# Other parameters
		if aggFilters:
			params['aggFilters'] = aggFilters
		if geo_dist:
			params['geoDecay'] = geo_dist
		if post_filter:
			import json
			params['postFilter'] = json.dumps(post_filter)
		if fields:
			params['fields'] = '|'.join(fields)
		if sort:
			params['sort'] = ','.join(sort)
		if count_total:
			params['countTotal'] = 'true'
		
		params['pageSize'] = min(page_size, 1000)  # API max is 1000
		
		if page_token:
			params['pageToken'] = page_token
		
		return self._make_request("studies", params)
	
	def search_all(
		self,
		max_results: int = None,
		**search_kwargs
	):
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
			
			studies = results.get('studies', [])
			if not studies:
				break
			
			for study in studies:
				yield study
				count += 1
				if max_results and count >= max_results:
					return
			
			page_token = results.get('nextPageToken')
			if not page_token:
				break
	
	def parse_study_to_clinical_trial(self, study_data: dict) -> 'ClinicalTrial':
		"""
		Convert a ClinicalTrials.gov API study response to a ClinicalTrial object.
		
		Args:
			study_data: Raw study data from the API
		
		Returns:
			ClinicalTrial: Parsed trial object
		"""
		from datetime import datetime
		import pytz
		
		protocol = study_data.get('protocolSection', {})
		identification = protocol.get('identificationModule', {})
		status_module = protocol.get('statusModule', {})
		description = protocol.get('descriptionModule', {})
		design_module = protocol.get('designModule', {})
		conditions_module = protocol.get('conditionsModule', {})
		eligibility_module = protocol.get('eligibilityModule', {})
		outcomes_module = protocol.get('outcomesModule', {})
		contacts_module = protocol.get('contactsLocationsModule', {})
		sponsor_module = protocol.get('sponsorCollaboratorsModule', {})
		
		# Extract NCT ID
		nct_id = identification.get('nctId')
		
		# Extract title (prefer official, fallback to brief)
		title = identification.get('officialTitle') or identification.get('briefTitle', '')
		
		# Build link
		link = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None
		
		# Extract summary/brief summary
		summary = description.get('briefSummary', '')
		detailed_description = description.get('detailedDescription', '')
		
		# Parse dates
		published_date = None
		start_date_struct = status_module.get('startDateStruct', {})
		if start_date_struct.get('date'):
			try:
				date_str = start_date_struct['date']
				# Handle partial dates (YYYY or YYYY-MM or YYYY-MM-DD)
				if len(date_str) == 4:
					published_date = datetime(int(date_str), 1, 1, tzinfo=pytz.UTC)
				elif len(date_str) == 7:
					parts = date_str.split('-')
					published_date = datetime(int(parts[0]), int(parts[1]), 1, tzinfo=pytz.UTC)
				else:
					published_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=pytz.UTC)
			except (ValueError, TypeError):
				pass
		
		# Build identifiers
		identifiers = {
			'nct': nct_id,
			'org_study_id': identification.get('orgStudyIdInfo', {}).get('id'),
		}
		
		# Extract secondary IDs
		secondary_ids = []
		for sec_id_info in identification.get('secondaryIdInfos', []):
			if sec_id_info.get('id'):
				secondary_ids.append(sec_id_info['id'])
		
		# Extract conditions
		conditions = conditions_module.get('conditions', [])
		
		# Extract phase
		phases = design_module.get('phases', [])
		phase = ', '.join(phases) if phases else None
		
		# Extract study type
		study_type = design_module.get('studyType')
		
		# Extract recruitment status
		recruitment_status = status_module.get('overallStatus')
		
		# Extract eligibility criteria
		eligibility_criteria = eligibility_module.get('eligibilityCriteria', '')
		min_age = eligibility_module.get('minimumAge')
		max_age = eligibility_module.get('maximumAge')
		gender = eligibility_module.get('sex')
		
		# Extract primary outcome measures
		primary_outcomes = []
		for outcome in outcomes_module.get('primaryOutcomes', []):
			measure = outcome.get('measure', '')
			description_text = outcome.get('description', '')
			time_frame = outcome.get('timeFrame', '')
			primary_outcomes.append(f"{measure}: {description_text} ({time_frame})")
		
		# Extract secondary outcome measures
		secondary_outcomes = []
		for outcome in outcomes_module.get('secondaryOutcomes', []):
			measure = outcome.get('measure', '')
			description_text = outcome.get('description', '')
			time_frame = outcome.get('timeFrame', '')
			secondary_outcomes.append(f"{measure}: {description_text} ({time_frame})")
		
		# Extract sponsor information
		lead_sponsor = sponsor_module.get('leadSponsor', {})
		primary_sponsor = lead_sponsor.get('name')
		
		# Extract collaborators
		collaborators = []
		for collab in sponsor_module.get('collaborators', []):
			if collab.get('name'):
				collaborators.append(collab['name'])
		
		# Extract intervention information
		arms_module = protocol.get('armsInterventionsModule', {})
		interventions = []
		for intervention in arms_module.get('interventions', []):
			int_type = intervention.get('type', '')
			int_name = intervention.get('name', '')
			int_desc = intervention.get('description', '')
			interventions.append(f"{int_type}: {int_name} - {int_desc}")
		
		# Extract locations/countries
		locations = contacts_module.get('locations', [])
		countries = list(set(loc.get('country', '') for loc in locations if loc.get('country')))
		
		# Extract contact information
		central_contacts = contacts_module.get('centralContacts', [])
		contact_info = {}
		if central_contacts:
			first_contact = central_contacts[0]
			contact_info = {
				'name': first_contact.get('name'),
				'email': first_contact.get('email'),
				'phone': first_contact.get('phone'),
			}
		
		# Build extra_fields for ClinicalTrial object
		extra_fields = {
			'scientific_title': identification.get('officialTitle'),
			'recruitment_status': recruitment_status,
			'date_registration': self._parse_date(status_module.get('studyFirstSubmitDate')),
			'study_type': study_type,
			'phase': phase,
			'countries': ', '.join(countries) if countries else None,
			'inclusion_criteria': eligibility_criteria,
			'exclusion_criteria': None,  # API combines inclusion/exclusion in eligibilityCriteria
			'intervention': '\n'.join(interventions) if interventions else None,
			'secondary_id': ', '.join(secondary_ids) if secondary_ids else None,
			'condition': ', '.join(conditions) if conditions else None,
			'primary_outcome': '\n'.join(primary_outcomes) if primary_outcomes else None,
			'secondary_outcome': '\n'.join(secondary_outcomes) if secondary_outcomes else None,
			'primary_sponsor': primary_sponsor,
			'inclusion_agemin': min_age,
			'inclusion_agemax': max_age,
			'inclusion_gender': gender,
			'target_size': str(design_module.get('enrollmentInfo', {}).get('count', '')) or None,
			'contact_firstname': contact_info.get('name', '').split()[0] if contact_info.get('name') else None,
			'contact_lastname': ' '.join(contact_info.get('name', '').split()[1:]) if contact_info.get('name') else None,
			'contact_email': contact_info.get('email'),
			'contact_tel': contact_info.get('phone'),
			'source_register': 'ClinicalTrials.gov',
			'detailed_description': detailed_description,
		}
		
		return ClinicalTrial(
			title=title,
			summary=summary,
			link=link,
			published_date=published_date,
			identifiers=identifiers,
			extra_fields=extra_fields
		)
	
	def _parse_date(self, date_str: str):
		"""Parse a date string from the API into a date object."""
		from datetime import datetime
		if not date_str:
			return None
		try:
			# Handle YYYY-MM-DD format
			return datetime.strptime(date_str, '%Y-%m-%d').date()
		except ValueError:
			try:
				# Handle YYYY-MM format
				return datetime.strptime(date_str, '%Y-%m').date()
			except ValueError:
				try:
					# Handle YYYY format
					return datetime.strptime(date_str, '%Y').date()
				except ValueError:
					return None
