#!/usr/bin/python3
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
import git  
import html
import json 
import os
import pandas as pd
import pathlib
import requests
import spacy 
import subprocess


# Set Variables
path = "/home/gregory/gregory"

# Set the API Server
## If you are running docker-compose.yaml, this is http://localhost:18080/
server = "https://api.gregory-ms.com/"
website_path = "/var/www/gregory-ms.com/"

now = datetime.now()
datetime_string = now.strftime("%d-%m-%Y_%Hh%Mm%Ss")

# Workflow starts

print('''
####
## PULL FROM GITHUB
####
''')
os.chdir(path)
## Optional
g = git.cmd.Git(path)
output = g.pull()

print(output)

print('''
####
## GET JSON DATA
####
''')

# Get Articles
url = server + 'articles/all'
res = requests.get(url)
file_name = path + '/data/articles.json'
with open(file_name, "w") as f:
    f.write(res.text)
file_name = path + '/content/developers/articles_' + datetime_string + '.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()
# Get Trials
url = server + 'trials/all'
res = requests.get(url)
file_name = path + '/data/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()
file_name = path + '/content/developers/trials_' + datetime_string + '.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()

print('''
####
## SAVE EXCEL VERSIONS
####
''')

## ARTICLES
articles_json = pd.read_json('data/articles.json')
articles_json.link = articles_json.link.apply(html.unescape)
articles_json.summary = articles_json.summary.apply(html.unescape)
articles_json.to_excel('content/developers/articles_'+ datetime_string + '.xlsx')

## TRIALS
trials_json = pd.read_json('data/trials.json')
trials_json.link = trials_json.link.apply(html.unescape)
trials_json.summary = trials_json.summary.apply(html.unescape)
trials_json.to_excel('content/developers/trials_' + datetime_string + '.xlsx')


print('''
####
## CREATE ZIP FILES
####

### Articles
''')

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

print('''
####
## CREATE ARTICLES
####
''')

# Make sure directory exists or create it
articlesDir = path + "/content/articles/"
articlesDirExists = pathlib.Path(articlesDir)

if articlesDirExists.exists() == False:
    articlesDirExists.mkdir(parents=True, exist_ok=True)

# Open articles.json
articles = path + '/data/articles.json'
with open(articles,"r") as a:
    data = a.read()

jsonArticles = json.loads(data)

# Set which nlp module to use
## en_core_web is more precise but uses more resources
# nlp = spacy.load('en_core_web_trf')
nlp = spacy.load('en_core_web_sm')
print("Looking for noun phrases")

for article in jsonArticles:

    # Process whole documents
    text = article["title"]
    doc=nlp(text)
    # Analyze syntax
    noun_phrases = [chunk.text for chunk in doc.noun_chunks]
    # print("Noun phrases:", [chunk.text for chunk in doc.noun_chunks])
    # print("verbs:", [token.lemma_ for token in doc if token.pos_ == "VERB"])
    # Find named entities, phrases and concepts
    # for entity in doc.ents:
    #     print(entity.text, entity. label)


    # Write a file for each record
    markdownDir = pathlib.Path(articlesDir+str(article["article_id"]))
    markdownDir.mkdir(parents=True, exist_ok=True)

    with open(str(markdownDir)+"/index.md", "w+") as f:
        articledata = "---\narticle_id: " + \
            str(article["article_id"]) + \
            "\ndiscovery_date: " + str(article["discovery_date"]) + \
            "\ndate: " + str(article["discovery_date"]) + "Z" +\
            "\ntitle: \'" + article["title"] + "\'" +\
            "\nsummary: |" + \
            '\n  ' + article["summary"].replace("\n", "\n  ") +\
            "\nlink: \'" + article["link"] + "\'" +\
            "\npublished_date: " + str(article["published_date"]) + \
            "\narticle_source: " + article["source"] + \
            "\nrelevant: " + str(article["relevant"]) + \
            "\nnounphrases: " + str(noun_phrases) + \
            "\nml_prediction_gnb: " + str(article["ml_prediction_gnb"]) + \
            "\nml_prediction_lr: " + str(article["ml_prediction_lr"]) + \
            "\noptions:" + \
            "\n  unlisted: false" + \
            "\n---\n" + \
            html.unescape(article["summary"])
        # add content to file

        f.write(articledata)
        f.close()

print('''
####
## CREATE TRIALS
####
''')

# Make sure directory exists or create it
trialsDir = path + "/content/trials/"
trialsDirExists = pathlib.Path(trialsDir)

if trialsDirExists.exists() == False:
    trialsDirExists.mkdir(parents=True, exist_ok=True)


# Open trials.json
trials = path + '/data/trials.json'
with open(trials,"r") as a:
    data = a.read()

jsonTrials = json.loads(data)

for trial in jsonTrials:

    # Process whole documents
    text = trial["title"]

    # Write a file for each record
    markdownDir = pathlib.Path(trialsDir+str(trial["trial_id"]))
    markdownDir.mkdir(parents=True, exist_ok=True)

    with open(str(markdownDir)+"/index.md", "w+") as f:
        trialdata = "---\ntrial_id: " + \
            str(trial["trial_id"]) + \
            "\ndiscovery_date: " + str(trial["discovery_date"]) + \
            "\ndate: " + str(trial["discovery_date"]) + "Z" +\
            "\ntitle: \'" + trial["title"] + "\'" +\
            "\nsummary: |" + \
            '\n  ' + trial["summary"].replace("\n", "\n  ") +\
            "\nlink: \'" + trial["link"] + "\'" +\
            "\npublished_date: " + str(trial["published_date"]) + \
            "\ntrial_source: " + trial["source"] + \
            "\nrelevant: " + str(trial["relevant"]) + \
            "\noptions:" + \
            "\n  unlisted: false" + \
            "\n---\n" + \
            html.unescape(trial["summary"])
        # add content to file

        f.write(trialdata)
        f.close()

print('''
####
## BUILD THE WEBSITE
####
''')
args = ("/usr/local/bin/hugo", "-d", website_path,"--cacheDir", path)
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

print('''
####
## UPDATE THE SEARCH INDEX 
####
''')

from algoliasearch.search_client import SearchClient
algolia_id = os.getenv('algolia_id')
algolia_key = os.getenv('algolia_key')

client = SearchClient.create(algolia_id, algolia_key)
index = client.init_index('gregory')

index = client.init_index('gregory')
batch = json.load(open(website_path + '/index.json'))
index.save_objects(batch, {'autoGenerateObjectIDIfNotExist': True})


print('''
####
## CLEAN UP FILES
####
''')

os.remove('content/developers/articles_' + datetime_string + '.xlsx')
os.remove('content/developers/articles_' + datetime_string + '.json')

os.remove('content/developers/trials_' + datetime_string + '.xlsx')
os.remove('content/developers/trials_' + datetime_string + '.json')