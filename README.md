# Live Version

https://labs.brunoamaral.eu

# gregory
Gregory aggregates searches in JSON and outputs to a Hugo static site

# Install

```bash 
git clone git@github.com:brunoamaral/gregory.git;
cd gregory;
hugo mod get -u;
hugo;
```
# Node-RED Flow

`data/articles.json` and `data/trials.json` is generated from a Node-Red flow like the one below.

```json
[{"id":"6b24f780.dc8238","type":"feedparse","z":"e677d300.d1d95","name":"Clinical Trials.gov","url":"https://clinicaltrials.gov/ct2/results/rss.xml?rcv_d=14&lup_d=&sel_rss=new14&term=biontech&count=10000","interval":"20","x":100,"y":180,"wires":[["ffb7b92d.9a01e8","fece258b.590898"]]},{"id":"937a615c.38fb1","type":"feedparse","z":"e677d300.d1d95","name":"PubMed : MS ","url":"https://pubmed.ncbi.nlm.nih.gov/rss/search/10guX6I3SqrbUeeLKSTD6FCRM44ewnrN2MKKTQLLPMHB4xNsZU/?limit=15&utm_campaign=pubmed-2&fc=20210216052009","interval":"20","x":90,"y":320,"wires":[["a4d2d38e.94de7","56a4dc66.256874"]]},{"id":"65401623.9abfe8","type":"change","z":"e677d300.d1d95","name":"Set Headers","rules":[{"t":"set","p":"headers","pt":"msg","to":"{}","tot":"json"},{"t":"set","p":"headers.content-type","pt":"msg","to":"application/json","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":1770,"y":420,"wires":[["f7d3e35a.082c2"]]},{"id":"d08c25f.3ad7e58","type":"comment","z":"e677d300.d1d95","name":"db model","info":"`\n{\n\t\"discovery_date\": 2021-02-16T17:57:16Z, # date \"+%FT%TZ\"`\"\n    \"title\": \"\",\n    \"summary\": \"\",\n    \"link\": \"\"\n    \"published_date\": ,\n    \"category\": \"\",\n\t\t\"source\":\"\"\n}\n`","x":1060,"y":420,"wires":[]},{"id":"4c001d43.2cc414","type":"template","z":"e677d300.d1d95","name":"","field":"topic","fieldType":"msg","format":"sql","syntax":"mustache","template":"INSERT INTO articles (discovery_date,title,summary,link,published_date,source,relevant)\nVALUES (strftime(\"%Y-%m-%dT%H:%M:%S\", \"NOW\"),\"{{article.title}}\",\"{{article.description}}\",\"{{topic}}\",\"{{payload\"}}\",\"{{article.source}}\",NULL)","output":"str","x":840,"y":320,"wires":[["e8bd17ee.3328d8"]]},{"id":"bb71d047.ff6f9","type":"moment","z":"e677d300.d1d95","name":"format pubdate","topic":"","input":"article.pubdate","inputType":"msg","inTz":"Europe/Lisbon","adjAmount":0,"adjType":"days","adjDir":"add","format":"","locale":"en-US","output":"payload","outputType":"msg","outTz":"Europe/Lisbon","x":680,"y":320,"wires":[["4c001d43.2cc414"]]},{"id":"703141cb.40cd2","type":"sqlite","z":"e677d300.d1d95","mydb":"9ccc1658.e12858","sqlquery":"msg.topic","sql":"","name":"","x":1190,"y":360,"wires":[["7763df39.5e76d","c6c74bb2.398ab8","a55db16a.04ca7","5ef6e421.b18edc"]]},{"id":"75731cda.feee34","type":"feedparse","z":"e677d300.d1d95","name":"Reuters","url":"https://www.reutersagency.com/feed/?best-topics=health","interval":"10","x":70,"y":360,"wires":[["d3e84102.20011"]]},{"id":"d3e84102.20011","type":"switch","z":"e677d300.d1d95","name":"filter if title contains MS","property":"article.title","propertyType":"msg","rules":[{"t":"regex","v":".*(multiple sclerosis ).*","vt":"str","case":true}],"checkall":"true","repair":false,"outputs":1,"x":250,"y":360,"wires":[["acb25bab.c29098"]]},{"id":"acb25bab.c29098","type":"change","z":"e677d300.d1d95","name":"set source reuters","rules":[{"t":"set","p":"article.source","pt":"msg","to":"reuters","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":490,"y":360,"wires":[["bb71d047.ff6f9"]]},{"id":"56a4dc66.256874","type":"change","z":"e677d300.d1d95","name":"set source pubmed","rules":[{"t":"set","p":"article.source","pt":"msg","to":"pubmed","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":330,"y":320,"wires":[["bb71d047.ff6f9"]]},{"id":"a55db16a.04ca7","type":"json","z":"e677d300.d1d95","name":"","property":"payload","action":"","pretty":false,"x":1630,"y":420,"wires":[["65401623.9abfe8"]]},{"id":"dd8e58f7.7eb6e8","type":"link in","z":"e677d300.d1d95","name":"","links":["7b3f48c4.9c0f88","e8bd17ee.3328d8","2a6fbc39.af1ee4","29b465d7.a3aa5a","e80c0064.85234","17411654.54199a","958a1d17.8c224","6c3c1a63.858e04"],"x":1035,"y":360,"wires":[["703141cb.40cd2"]]},{"id":"e8bd17ee.3328d8","type":"link out","z":"e677d300.d1d95","name":"","links":["dd8e58f7.7eb6e8"],"x":935,"y":320,"wires":[]},{"id":"b0abb1e9.a6e2b","type":"inject","z":"e677d300.d1d95","name":"","props":[{"p":"payload"},{"p":"topic","vt":"str"}],"repeat":"","crontab":"","once":false,"onceDelay":0.1,"topic":"INSERT INTO articles (discovery_date,title,summary,link,published_date,source,relevant) VALUES (strftime(\"%Y-%m-%dT%H:%M:%S\", \"NOW\"),\"{{article.title}}\",\"{{article.description}}\",\"{{topic}}\",\"{{payload}}\",\"{{article.source}}\",NULL)","payload":"","payloadType":"date","x":840,"y":380,"wires":[["703141cb.40cd2"]]},{"id":"5ef6e421.b18edc","type":"debug","z":"e677d300.d1d95","name":"","active":true,"tosidebar":true,"console":false,"tostatus":false,"complete":"true","targetType":"full","statusVal":"","statusType":"auto","x":1010,"y":60,"wires":[]},{"id":"ffb7b92d.9a01e8","type":"change","z":"e677d300.d1d95","name":"set source pubmed","rules":[{"t":"set","p":"article.source","pt":"msg","to":"ClinicalTrials.gov","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":330,"y":60,"wires":[["bc0c284f.e4f5d8"]]},{"id":"974801fc.d87d5","type":"template","z":"e677d300.d1d95","name":"","field":"topic","fieldType":"msg","format":"sql","syntax":"mustache","template":"INSERT INTO trials (discovery_date,title,summary,link,published_date,source,relevant)\nVALUES (strftime(\"%Y-%m-%dT%H:%M:%S\", \"NOW\"),\"{{article.title}}\",\"{{article.description}}\",\"{{topic}}\",\"{{payload}}\",\"{{article.source}}\",NULL)","output":"str","x":720,"y":60,"wires":[["6c3c1a63.858e04"]]},{"id":"bc0c284f.e4f5d8","type":"moment","z":"e677d300.d1d95","name":"format pubdate","topic":"","input":"article.date","inputType":"msg","inTz":"Europe/Lisbon","adjAmount":0,"adjType":"days","adjDir":"add","format":"","locale":"en-US","output":"payload","outputType":"msg","outTz":"Europe/Lisbon","x":520,"y":60,"wires":[["974801fc.d87d5"]]},{"id":"6c3c1a63.858e04","type":"link out","z":"e677d300.d1d95","name":"","links":["dd8e58f7.7eb6e8"],"x":835,"y":60,"wires":[]},{"id":"f7d3e35a.082c2","type":"http response","z":"e677d300.d1d95","name":"","statusCode":"","headers":{},"x":1910,"y":420,"wires":[]},{"id":"c8107088.37ef9","type":"http in","z":"e677d300.d1d95","name":"","url":"/articles/all","method":"get","upload":false,"swaggerDoc":"","x":200,"y":880,"wires":[["ebc24695.52d7d8"]]},{"id":"ebc24695.52d7d8","type":"change","z":"e677d300.d1d95","name":"","rules":[{"t":"set","p":"topic","pt":"msg","to":"SELECT  * FROM articles ORDER BY article_id DESC;","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":400,"y":880,"wires":[["e80c0064.85234"]]},{"id":"5838a1b9.df1ca","type":"http in","z":"e677d300.d1d95","name":"","url":"/trials/all","method":"get","upload":false,"swaggerDoc":"","x":210,"y":1000,"wires":[["e1cb590b.e4fe88"]]},{"id":"e1cb590b.e4fe88","type":"change","z":"e677d300.d1d95","name":"","rules":[{"t":"set","p":"topic","pt":"msg","to":"SELECT  * FROM trials ORDER BY trial_id DESC;","tot":"str"}],"action":"","property":"","from":"","to":"","reg":false,"x":400,"y":1000,"wires":[["e80c0064.85234"]]},{"id":"9ccc1658.e12858","type":"sqlitedb","db":"/data/gregory.db","mode":"RWC"}]
```

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
website_path = "./public" # /var/www/labs.brunoamaral.eu/
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
