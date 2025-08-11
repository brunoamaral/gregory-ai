from crossref.restful import Works, Etiquette
from sitesettings.models import CustomSetting
import re
import os
from joblib import load
from .utils.model_utils import DenseTransformer
from datetime import date
import pandas as pd
import html
from .utils.text_utils import cleanHTML
from .utils.text_utils import cleanText
from joblib import load
from .models import Articles
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

def remove_utm(url):
	u = urlparse(url)
	query = parse_qs(u.query, keep_blank_values=True)
	query.pop('utm_source', None)
	query.pop('utm_medium', None)
	query.pop('utm_campaign', None)
	query.pop('utm_content', None)
	u = u._replace(query=urlencode(query, True))
	return urlunparse(u)


def normalize_orcid(value):
	"""Normalize ORCID values by removing URL prefixes and trailing slashes.

	Examples:
	- https://orcid.org/0000-0002-1825-0097 -> 0000-0002-1825-0097
	- http://www.orcid.org/0000-0002-1825-0097/ -> 0000-0002-1825-0097
	- 0000-0002-1825-0097 -> 0000-0002-1825-0097

	Returns None when input is falsy after cleanup.
	"""
	if not value:
		return None
	# Ensure string and strip whitespace
	s = str(value).strip()
	# Remove protocol and domain variants (case-insensitive)
	s = re.sub(r'^(https?://)?(www\.)?orcid\.org/', '', s, flags=re.IGNORECASE)
	# Remove any remaining leading/trailing slashes and whitespace
	s = s.strip().strip('/')
	# ORCID check digit can be 'X' (uppercase); standardize to uppercase for consistent storage
	s = s.upper()
	return s or None


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

