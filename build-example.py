#!/Library/Frameworks/Python.framework/Versions/3.7/bin/python3
import os
import shutil
import urllib.request
import subprocess

# The server were Gregory lives uses Cloudflare. This setting allows us to bypass the Browser Integrity Check
opener = urllib.request.build_opener()
opener.addheaders = [('User-agent', 'Mozilla/5.0')]
urllib.request.install_opener(opener)

# set variables
path = "/Users/USERNAME/gregory"
server = "HTTPS://SERVER.COM"
website_path = "./public" 

# Workflow starts
os.chdir(path)
## Optional
# import git
# g = git.cmd.Git(path)
# g.pull()

# Get articles
url = server + 'api/articles/all'
file_name = path + '/data/articles.json'
urllib.request.urlretrieve(url, file_name)
file_name = path + '/content/api/articles.json'
urllib.request.urlretrieve(url, file_name)

# Get trials
url = server + 'api/trials/all'
file_name = path + '/data/trials.json'
urllib.request.urlretrieve(url, file_name)
file_name = path + '/content/api/trials.json'
urllib.request.urlretrieve(url, file_name)

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