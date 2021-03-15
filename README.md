# Live Version

https://labs.brunoamaral.eu

# Gregory
Gregory aggregates searches in JSON and outputs to a Hugo static site

# Install

```bash 
git clone git@github.com:brunoamaral/gregory.git;
cd gregory;
hugo mod get -u;
hugo;
```
# Node-RED Flow

`data/articles.json` and `data/trials.json` are generated from a Node-Red flow available in the `flows.json` file.

# Build script

Example on how to build the website:

```python
#!/Library/Frameworks/Python.framework/Versions/3.7/bin/python3
import os
import shutil
import urllib.request
import git
import subprocess

# set variables
path = "/PATH-TO/gregory"
server = "https://SERVER-RUNNING-NODE-RED.COM/"
website_path = "/PATH/TO/WEBSITE" 
# Workflow starts
os.chdir(path)
g = git.cmd.Git(path)
g.pull()

# Get articles
url = server + 'articles/all'
file_name = path + '/data/articles.json'
urllib.request.urlretrieve(url, file_name)

# Get trials
url = server + 'trials/all'
file_name = path + '/data/trials.json'
urllib.request.urlretrieve(url, file_name)

args = ("/usr/local/bin/hugo", "-d", website_path,"--cacheDir", path)
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)

popen.wait()
output = popen.stdout.read()
print(output)
```
