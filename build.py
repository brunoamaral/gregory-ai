#!/usr/bin/python3
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from shutil import which
from slugify import slugify
from zipfile import ZipFile
import git  
import html
import json 
import jwt
import numpy as np
import os
import pandas as pd
import pathlib
import sqlalchemy
import subprocess
import time
load_dotenv()

# Set Variables
GREGORY_DIR = os.getenv('GREGORY_DIR')

# Set the API Server
SERVER = os.getenv('SERVER')
WEBSITE_PATH = os.getenv('WEBSITE_PATH')

now = datetime.now()
datetime_string = now.strftime("%d-%m-%Y_%Hh%Mm%Ss")

# Variables to sign metabase embeds
METABASE_SITE_URL = os.getenv('METABASE_SITE_URL')
METABASE_SECRET_KEY = os.getenv('METABASE_SECRET_KEY')

# Workflow starts

print('''
####
## PULL FROM GITHUB
####
''')
os.chdir(GREGORY_DIR)
## Optional
g = git.cmd.Git(GREGORY_DIR)
output = g.pull()

print(output)

print('''
####
## GET DATA
####
''')

## GET ENV
# db_host = os.getenv('DB_HOST')
# It's localhost because we are running outside the container
db_host = 'localhost'
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')

postgres_connection_url = 'postgresql://' + postgres_user + ':' + postgres_password + '@' + db_host + ':5432/' + postgres_db
engine = sqlalchemy.create_engine(postgres_connection_url)

# try:
# 	conn = psycopg2.connect("dbname='"+ postgres_db +"' user='" + postgres_user + "' host='" + db_host + "' password='" + postgres_password + "'")
# except:
# 	print("I am unable to connect to the database")

query_articles = 'SELECT "public"."articles"."article_id" AS "article_id", "public"."articles"."title" AS "title", "public"."articles"."summary" AS "summary", "public"."articles"."link" AS "link", "public"."articles"."published_date" AS "published_date", "public"."articles"."source" AS "source", "public"."articles"."relevant" AS "relevant", "public"."articles"."ml_prediction_gnb" AS "ml_prediction_gnb", "public"."articles"."ml_prediction_lr" AS "ml_prediction_lr", "public"."articles"."discovery_date" AS "discovery_date", "public"."articles"."noun_phrases" AS "noun_phrases", "public"."articles"."doi" AS "doi", "Sources"."name" AS "Sources__name", "Sources"."link" AS "Sources__link" FROM "public"."articles" LEFT JOIN "public"."sources" "Sources" ON "public"."articles"."source" = "Sources"."source_id" ORDER BY article_id;'

query_trials = 'SELECT "public"."trials"."trial_id" AS "trial_id", "public"."trials"."discovery_date" AS "discovery_date", "public"."trials"."title" AS "title", "public"."trials"."summary" AS "summary", "public"."trials"."link" AS "link", "public"."trials"."published_date" AS "published_date", "public"."trials"."source" AS "source", "public"."trials"."relevant" AS "relevant", "Sources"."source_id" AS "Sources__source_id", "Sources"."name" AS "Sources__name", "Sources"."link" AS "Sources__link" FROM "public"."trials" LEFT JOIN "public"."sources" "Sources" ON "public"."trials"."source" = "Sources"."source_id" ORDER BY trial_id DESC;'

print('''
####
## SAVE EXCEL AND JSON VERSIONS
####
''')

## ARTICLES
articles = pd.read_sql_query(query_articles, engine)
articles['published_date'] = articles['published_date'].dt.tz_localize(None)
articles['discovery_date'] = articles['discovery_date'].dt.tz_localize(None)

articles.link = articles.link.apply(html.unescape)
articles.summary = articles.summary.replace(np.nan, '', regex=True)
articles.summary = articles.summary.apply(html.unescape)
articles.to_excel('content/developers/articles_'+ datetime_string + '.xlsx')
articles.to_json('content/developers/articles_'+ datetime_string + '.json')

## TRIALS
trials = pd.read_sql_query(query_trials, engine)
trials['published_date'] = trials['published_date'].dt.tz_localize(None)
trials['discovery_date'] = trials['discovery_date'].dt.tz_localize(None)

trials = trials.replace(np.nan, '', regex=True)
trials.link = trials.link.apply(html.unescape)
trials.summary = trials.summary.apply(html.unescape)
trials.to_excel('content/developers/trials_' + datetime_string + '.xlsx')
trials.to_json('content/developers/trials_' + datetime_string + '.json')



print('''
####
## CREATE ARTICLES
####
''')

# Make sure directory exists or create it
articlesDir = GREGORY_DIR + "/content/articles/"
articlesDirExists = pathlib.Path(articlesDir)

if articlesDirExists.exists() == False:
	articlesDirExists.mkdir(parents=True, exist_ok=True)

for index, row in articles.iterrows():

	title = row["title"].replace("'", "\\'").replace("\"",'\\"')

	url = None
	if row["noun_phrases"] == None:
		row["noun_phrases"] = ''
		url = "url: /articles/" + str(row["article_id"]) + "/"
	else:
		url = list(map(lambda x: x.lower(), row["noun_phrases"]))
		url = slugify('-'.join(url))[:250]
		url = "\nurl: /articles/" + str(row["article_id"]) + "/" + str(url) + "/" + \
			"\naliases: " + \
			"\n  - /articles/" + str(row["article_id"]) + "/"
	

	# Write a file for each record
	markdownDir = pathlib.Path(articlesDir+str(row["article_id"]))
	markdownDir.mkdir(parents=True, exist_ok=True)

	with open(str(markdownDir)+"/index.md", "w+") as f:
		articledata = "---\narticle_id: " + \
			str(row["article_id"]) + \
			"\ndiscovery_date: " + str(row["discovery_date"]) + \
			"\ndate: " + str(row["discovery_date"]) +\
			"\ntitle: \"" + title + "\"" +\
			"\nsummary: |" + \
			'\n  ' + row["summary"].replace("\n", "\n  ") +\
			"\nlink: \'" + row["link"] + "\'" +\
			"\npublished_date: " + str(row["published_date"]) + \
			"\narticle_source: " + str(row["Sources__name"]) + \
			"\nrelevant: " + str(row["relevant"]).lower() + \
			"\nnounphrases: " + str(row["noun_phrases"]) + \
			"\nml_prediction_gnb: " + str(row["ml_prediction_gnb"]).lower() + \
			"\nml_prediction_lr: " + str(row["ml_prediction_lr"]).lower() + \
			"\noptions:" + \
			"\n  unlisted: false" + \
			"\n" + url + \
			"\n---\n" + \
			html.unescape(row["summary"])
		# add content to file

		f.write(articledata)
		f.close()

print('''
####
## CREATE TRIALS
####
''')

# Make sure directory exists or create it
trialsDir = GREGORY_DIR + "/content/trials/"
trialsDirExists = pathlib.Path(trialsDir)

if trialsDirExists.exists() == False:
	trialsDirExists.mkdir(parents=True, exist_ok=True)


# Open trials.json

for index, row in trials.iterrows():
	title = row["title"].replace("'", "\\'").replace("\"",'\\"')

	# Write a file for each record
	markdownDir = pathlib.Path(trialsDir+str(row["trial_id"]))
	markdownDir.mkdir(parents=True, exist_ok=True)

	with open(str(markdownDir)+"/index.md", "w+") as f:

		trialdata = "---\ntrial_id: " + \
			str(row["trial_id"]) + \
			"\ndiscovery_date: " + str(row["discovery_date"]) + \
			"\ndate: " + str(row["discovery_date"]) +\
			"\ntitle: \"" + title + "\"" +\
			"\nsummary: |" + \
			'\n  ' + str(row["summary"]).replace("\n", "\n  ") +\
			"\nlink: \'" + row["link"] + "\'" +\
			"\npublished_date: " + str(row["published_date"]) + \
			"\ntrial_source: " + str(row["Sources__name"]) + \
			"\nrelevant: " + str(row["relevant"]).lower() + \
			"\noptions:" + \
			"\n  unlisted: false" + \
			"\n---\n" + \
			html.unescape(str(row["summary"]))
		# add content to file

		f.write(trialdata)
		f.close()


print('''
####
## CREATE ZIP FILES
####

### Articles''')

zipArticles = ZipFile('content/developers/articles.zip', 'w')
# Add multiple files to the zip
print('- content/developers/articles_' + datetime_string + '.xlsx')
print('- content/developers/articles_' + datetime_string + '.json')
print('- content/developers/README.md\n')

zipArticles.write('content/developers/articles_' + datetime_string + '.xlsx')
zipArticles.write('content/developers/articles_' + datetime_string + '.json')
zipArticles.write('content/developers/README.md')

# close the Zip File
zipArticles.close()

print('### Clinical Trials')

zipTrials = ZipFile('content/developers/trials.zip', 'w')
# Add multiple files to the zip
print('- content/developers/trials_' + datetime_string + '.xlsx')
print('- content/developers/trials_' + datetime_string + '.json')
print('- content/developers/README.md\n')
zipTrials.write('content/developers/trials_' + datetime_string + '.xlsx')
zipTrials.write('content/developers/trials_' + datetime_string + '.json')
zipTrials.write('content/developers/README.md')

# close the Zip File
zipTrials.close()

print('\n# delete temporary files')
excel_file = Path('content/developers/articles_' + datetime_string + '.xlsx')
json_file = Path('content/developers/articles_' + datetime_string + '.json')
Path.unlink(excel_file)
Path.unlink(json_file)

excel_file = Path('content/developers/trials_' + datetime_string + '.xlsx')
json_file = Path('content/developers/trials_' + datetime_string + '.json')
Path.unlink(excel_file)
Path.unlink(json_file)

print('''
####
## GENERATE EMBED KEYS FOR METABASE
####
''')




# Opening JSON file
f = open('data/dashboards.json')
 
# returns JSON object as
# a dictionary
dashboards = json.load(f)
 
# Iterating through the json list
metabase_json = {}
for i in dashboards:
	print("Generating key for dashboard: "+ str(i))
	payload = { "resource": {"dashboard": i}, "params": { }, "exp": round(time.time()) + (60 * 180)}
	token = jwt.encode(payload, METABASE_SECRET_KEY, algorithm='HS256')
	iframeUrl = METABASE_SITE_URL + 'embed/dashboard/' + token + '#bordered=true&titled=true'
	entry = "dashboard_" + str(i) 
	metabase_json[str(entry)] = iframeUrl

f.close()

embedsJson = GREGORY_DIR + 'data/embeds.json';

with open(embedsJson, "w") as f:
	f.write(json.dumps(metabase_json))
	f.close()

print('''
####
## BUILD THE WEBSITE
####
''')

hugo_path = os.getenv('HUGO_PATH')

if hugo_path == None:
	hugo_path = str(which('hugo'))

args = (hugo_path, "-d", WEBSITE_PATH,"--cacheDir", GREGORY_DIR)
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)
