#!/usr/bin/python3
import os
import requests
import subprocess
import json 
import pathlib
from pathlib import Path
import git  

# set variables
path = "/home/gregory/gregory"
server = "HTTPS://johnny.1q83.me/"
website_path = "/var/www/labs.brunoamaral.eu/"
# Workflow starts
os.chdir(path)
## Optional
g = git.cmd.Git(path)
g.pull()
# Get articles
url = server + 'api/articles/all'
res = requests.get(url)
file_name = path + '/data/articles.json'
with open(file_name, "w") as f:
    f.write(res.text)
file_name = path + '/content/api/articles.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()
# Get trials
url = server + 'api/trials/all'
res = requests.get(url)
file_name = path + '/data/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()
file_name = path + '/content/api/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
    f.close()
## Save excel versions
import pandas as pd
import html
articles_json = pd.read_json('data/articles.json')
articles_json.link = articles_json.link.apply(html.unescape)
articles_json.summary = articles_json.summary.apply(html.unescape)
articles_json.to_excel('content/api/articles.xlsx')
trials_json = pd.read_json('data/trials.json')
trials_json.link = trials_json.link.apply(html.unescape)
trials_json.summary = trials_json.summary.apply(html.unescape)
trials_json.to_excel('content/api/trials.xlsx')

# Make sure directory exists or create it
articlesDir = path + "/content/article/"
articlesDirExists = pathlib.Path(articlesDir)

if articlesDirExists.exists() == False:
    articlesDirExists.mkdir(parents=True, exist_ok=True)

# open articles.json
articles = path + '/data/articles.json'
with open(articles,"r") as a:
    data = a.read()

jsonArticles = json.loads(data)

for article in jsonArticles:
    # for each record, write a file
    markdownDir = pathlib.Path(articlesDir+str(article["article_id"]))
    markdownDir.mkdir(parents=True, exist_ok=True)

    with open(str(markdownDir)+"/index.md", "w+") as f:
        articledata = "---\narticle_id: " + \
            str(article["article_id"]) + \
            "\ndiscovery_date: " + str(article["discovery_date"]) + \
            "\ntitle: \'" + article["title"] + "\'" \
            +"\nsummary: \'" + article["summary"] + "\'" \
            +"\nlink: \'" + article["link"] + "\'" \
            +"\npublished_date: " + str(article["published_date"]) + \
            "\nsource: " + article["source"] + \
            "\nrelevant: " + \
            str(article["relevant"]) + \
            "\n---\n" + \
            html.unescape(article["summary"])
        # add content to file

        f.write(articledata)
        f.close()




args = ("/usr/local/bin/hugo", "-d", website_path,"--cacheDir", path)
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)
