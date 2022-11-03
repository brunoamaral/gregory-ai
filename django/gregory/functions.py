from crossref.restful import Works, Etiquette
from sitesettings.models import CustomSetting
import re
import os
def get_doi(title):
	doi = None
	if title != '':
		i = 0
	site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
	client_website = 'https://' + site.site.domain + '/'
	my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
	works = Works(etiquette=my_etiquette)
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
