from sitesettings.models import *
from crossref.restful import Works, Etiquette
from datetime import datetime


SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
works = Works(etiquette=my_etiquette)

def get_access_info(doi):
		article_access = None
		if bool(doi):
				if unpaywall_utils.checkIfDOIIsOpenAccess(doi, SITE.admin_email):
					article_access = 'open'
				else:
					article_access = 'restricted'
		return article_access


def get_publisher_and_journal(doi):
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

def get_abstract(doi):
	abstract = None
	w = works.doi(doi)
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