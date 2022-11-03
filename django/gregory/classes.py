class SciencePaper:
	def __init__(self, doi, title=None):
		from datetime import datetime
		from sitesettings.models import CustomSetting
		from db_maintenance.unpaywall import unpaywall_utils
		from crossref.restful import Works, Etiquette
		import os
		import pytz
		timezone = pytz.timezone('UTC')
		site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		client_website = 'https://' + site.site.domain + '/'
		my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
		works = Works(etiquette=my_etiquette)
		work = works.doi(doi)
		self.doi = doi
		self.link = work['link'][0]['URL']
		article_access = None
		if title == None:
			self.title = work['title'][0]
		else:
			self.title = title
		if bool(doi):
			if unpaywall_utils.checkIfDOIIsOpenAccess(doi, site.admin_email):
				article_access = 'open'
			else:
				article_access = 'restricted'
		self.access = article_access
		self.publisher = work['publisher']
		self.journal = None
		try:
				self.journal = work['container-title'][0]
		except:
				pass
		self.published_date = None
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
		self.abstract = None
		try:
				self.abstract = work['abstract']
		except:
				pass
		self.authors = []
		if 'author' in work and work['author'] is not None:
			self.authors = work['author']
	def __str__(self):
		return f"{self.doi}, {self.title}"
	def __repr__(self):
		return f"{self.doi}, \"{self.title}\""