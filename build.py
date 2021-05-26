#!/usr/bin/python3
import os
import requests
import shutil
import subprocess
# set variables
path = "/home/gregory/gregory"
server = "HTTPS://johnny.1q83.me/"
website_path = "/var/www/labs.brunoamaral.eu/"
# Workflow starts
os.chdir(path)
## Optional
import git
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
# Get trials
url = server + 'api/trials/all'
res = requests.get(url)
file_name = path + '/data/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
file_name = path + '/content/api/trials.json'
with open(file_name, "w") as f:
    f.write(res.text)
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
args = ("/usr/local/bin/hugo", "-d", website_path,"--cacheDir", path)
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)