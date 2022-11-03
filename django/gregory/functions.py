
class SciencePaper:
	SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
	CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
	from datetime import datetime
	from sitesettings.models import CustomSetting
	from db_maintenance.unpaywall import unpaywall_utils
	def __init__(self, doi, SITE=SITE, CLIENT_WEBSITE=CLIENT_WEBSITE):
		from crossref.restful import Works, Etiquette
		my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
		works = Works(etiquette=my_etiquette)
		work = works.doi(doi)
	def get_access_info(doi,self):
			article_access = None
			if bool(doi):
					if unpaywall_utils.checkIfDOIIsOpenAccess(doi, self.SITE.admin_email):
						article_access = 'open'
					else:
						article_access = 'restricted'
			print(article_access)
	def __str__(self):
		return f"{self.doi}"

	def get_publisher_and_journal(work):
		'''
		returns publisher and container-title as a tuple
		'''
		work = works.doi(doi)
		publisher = None
		journal = None
		if work:
			print(work['publisher'])
			publisher = work['publisher']
			try:
				journal = work['container-title'][0]
			except IndexError:
				pass
		return (publisher,journal)

	def get_abstract(work):
		abstract = None
		try:
				abstract = w['abstract']
		except:
				pass
		return abstract



def get_published_date(doi):
	timezone = pytz.timezone('UTC')
	w = works.doi(doi)
	published_date = None
	if w != None and 'issued' in w:
		issued = w['issued']['date-parts'][0]
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
			published_date = datetime( year=year, month=month, day=day, tzinfo=timezone)
		except:
				pass
	return published_date

def get_doi(title):
	doi = None
	if title != '':
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
				doi = w['DOI']
				return doi
			i += 1
			if i == 5:
				return None

def get_authors(doi):
	authors = []
	work = works.query
	if 'author' in work and work['author'] is not None:
		authors = work['author']
	for author in authors:
		if 'given' in author and 'family' in author:
			given_name = None
			if 'given' in author:
				given_name = author['given']
			family_name = None
			if 'family' in author:
				family_name = author['family']
			orcid = None
			if 'ORCID' in author:
				orcid = author['ORCID']

			# get or create author
			author_obj = Authors.objects.get_or_create(given_name=given_name,family_name=family_name,ORCID=orcid)
			author_obj = author_obj[0]
			## add to database
			if author_obj.author_id is not None:
				# make relationship
				science_paper.authors.add(author_obj)
