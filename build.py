#!/usr/bin/python3
from pathlib import Path
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
file_name = path + '/content/developers/articles.json'
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
file_name = path + '/content/developers/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()

print('''
####
## SAVE EXCEL VERSIONS
####
''')
articles_json = pd.read_json('data/articles.json')
articles_json.link = articles_json.link.apply(html.unescape)
articles_json.summary = articles_json.summary.apply(html.unescape)
articles_json.to_excel('content/developers/articles.xlsx')
trials_json = pd.read_json('data/trials.json')
trials_json.link = trials_json.link.apply(html.unescape)
trials_json.summary = trials_json.summary.apply(html.unescape)
trials_json.to_excel('content/developers/trials.xlsx')

print('''
####
## GET ARTICLES
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
            "\noptions:" + \
            "\n  unlisted: false" + \
            "\n---\n" + \
            html.unescape(article["summary"])
        # add content to file

        f.write(articledata)
        f.close()

print('''
####
## GET TRIALS
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
