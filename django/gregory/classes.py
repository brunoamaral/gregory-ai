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

	def refresh(self):
		from db_maintenance.unpaywall import unpaywall_utils
		from crossref.restful import Works, Etiquette
		import os
		import pytz
		from datetime import datetime
		timezone = pytz.timezone('UTC')
		from sitesettings.models import CustomSetting
		site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		client_website = 'https://' + site.site.domain + '/'
		my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = None
		if self.doi != None:
			work = works.doi(self.doi)
		else:
			return 'No DOI provided'
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
			if unpaywall_utils.checkIfDOIIsOpenAccess(self.doi, site.admin_email):
				self.access = 'open'
			else:
				self.access = 'restricted'
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

