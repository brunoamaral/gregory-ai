#!/usr/bin/python3
from dotenv import load_dotenv
from pathlib import Path
from shutil import which
import git
import os
import psycopg
import requests
from subprocess import Popen,PIPE
import sys
import time
import shutil

load_dotenv()
cwd = os.getcwd()
github = "git@github.com:brunoamaral/gregory.git"

print('''
####
## Check for .env file
####
''')
env_file = Path(".env")

if env_file.is_file():
	print("\N{check mark} Found .env file")
else:
	print('''
####
## Configure Gregory MS
####

Did not find a .env file, we need to set some configuration variables. If in doubt, you can input blank and configure the .env file later.

Some variables are optional: EMAIL_*, HUGO_PATH, METABASE_*
	''')

	configs = {
	"DB_HOST" : 'db',
	"DOMAIN_NAME" : os.getenv('DOMAIN_NAME'),
	"EMAIL_DOMAIN" : os.getenv('EMAIL_DOMAIN'),
	"EMAIL_HOST_PASSWORD" : os.getenv('EMAIL_HOST_PASSWORD'), 
	"EMAIL_HOST_USER" : os.getenv('EMAIL_HOST_USER'), 
	"EMAIL_HOST" : os.getenv('EMAIL_HOST'), 
	"EMAIL_MAILGUN_API_URL" : os.getenv('EMAIL_MAILGUN_API_URL'),
	"EMAIL_MAILGUN_API" : os.getenv('EMAIL_MAILGUN_API'),
	"EMAIL_PORT" : 587, 
	"EMAIL_USE_TLS" : 'true', 
	"GREGORY_DIR" : os.getenv('GREGORY_DIR'),
	"HUGO_PATH" : os.getenv('HUGO_PATH'),
	"METABASE_SECRET_KEY" : os.getenv('METABASE_SECRET_KEY'),
	"METABASE_SITE_URL" : os.getenv('METABASE_SITE_URL'),
	"POSTGRES_DB" : os.getenv('POSTGRES_DB'),
	"POSTGRES_PASSWORD" : os.getenv('POSTGRES_PASSWORD'),
	"POSTGRES_USER" : os.getenv('POSTGRES_USER'),
	"SECRET_KEY" : os.getenv('SECRET_KEY'),
	"WEBSITE_PATH" : os.getenv('WEBSITE_PATH'),
	}

	for key,value in configs.items():
		if value == None:
			value = input('please enter value for ' + key + ': ')
			configs[key] = value
	with open('.env','a') as file:
		for key,value in configs.items():
			line = key + '=' + str(value) + '\n'
			file.write(line)
		file.close()
	print('Settings written to .env file, please check if everything looks correct.')
	input("Press Enter to continue...")
	print('Setting environment variables...')

	for key,value in configs.items():
		os.environ[key] = str(value)


def is_tool(name):
	"""Check whether `name` is on PATH and marked as executable."""
	# from whichcraft import which
	from shutil import which
	return which(name) is not None

def is_git_repo(path):
	try:
		_ = git.Repo(path).git_dir
		return True
	except git.exc.InvalidGitRepositoryError:
		return False

if is_tool("git"):
	print("\N{check mark} Git is installed, proceeding.")
else:
	sys.exit("Git was not found and I can't install Gregory without it. Exiting.")

print('''
####
## Check for directories
####
''')

p = Path("nodered-data")

if p.is_dir():
	print("\N{check mark} Found nodered-data directory")
else:
	print("Didn't find nodered-data, creating ...")
	p.mkdir(parents=True, exist_ok=True)

p = Path("python-ml/")
if p.is_dir():
	print("\N{check mark} Found python-ml directory")
else:
	print("Didn't find python-ml, creating ...")
	p.mkdir(parents=True, exist_ok=True)

p = Path("django/")
if p.is_dir():
	print("\N{check mark} Found django directory")
else:
	print("Didn't find django, aborting ...")

print('''
####
## Check for docker-compose.yaml file
####
''')

docker_compose = Path("docker-compose.yaml")

if docker_compose.is_file():
	print("\N{check mark} Found docker-compose.yaml file")
else:
	print("Didn't find docker-compose.yaml, downloading latest version from https://raw.githubusercontent.com/brunoamaral/gregory/main/docker-compose.yaml")

	url = 'https://raw.githubusercontent.com/brunoamaral/gregory/main/docker-compose.yaml'
	res = requests.get(url)
	file_name = 'docker-compose.yaml'
	with open(file_name, "w") as f:
		f.write(res.text)
		f.close()

print('''
####
## Check for docker-compose command
####
''')
if is_tool("docker-compose"):
	print("\N{check mark} Found docker-compose")
else:
	print("Didn't find docker-compose, please install it. Details at https://docs.docker.com/compose/install/")

		
print('''
####
## Check for Hugo
####
''')

hugo_path = os.getenv('HUGO_PATH')

if hugo_path == True or is_tool('hugo') == True:
	print("\N{check mark} Found Hugo in path or environment variable")
else:
	print("Didn't find Hugo, please install it. Details at https://gohugo.io")
	sys.exit('Hugo not installed')

print('''
####
## Updating any hugo modules that may exist
####
''')

args = (which('hugo'), "mod", "get","-u")
popen = Popen(args, stdout=PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

print('''
####
## Running docker-compose up -d db
## This will launch Postgres
####
''')

args = ("sudo","docker-compose","up","-d","db")
popen = Popen(args, stdout=PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

print('''
Give Postgres 20 seconds to finish setting up...
''')
for i in range(20,0,-1):
		sys.stdout.write(str(i)+' ')
		sys.stdout.flush()
		time.sleep(1)


print('''
####
## Creating the Metabase database
####

We assume that the `db` container is running and that we can access it from localhost:5432.
''')

db_host = 'localhost'
postgres_user = os.getenv('POSTGRES_USER')
postgres_password = os.getenv('POSTGRES_PASSWORD')
postgres_db = os.getenv('POSTGRES_DB')

try:
	conn = psycopg.connect(dbname=postgres_db, user=postgres_user,host=db_host,password=postgres_password,autocommit=True)
	cur = conn.cursor()
	cur.execute("CREATE DATABASE metabase;")
	conn.close()
except:
	print("I am unable to connect to postgres. Please create the `metabase` database manually and restart the containers.")

def runshell(*args):
		p = Popen(*args, shell=True, stdout=sys.stdout, stderr=sys.stderr, stdin=sys.stdin)
		p.wait()

print('''
####
## Running docker-compose up -d admin --build
## This will launch Django
####
''')

args = ("sudo","docker-compose","up","--build","-d","admin")
popen = Popen(args, stdout=PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

print('''
####
## Running Django setup
####
''')
		
print('## `./manage.py makemigrations`')
runshell('sudo docker exec -it admin ./manage.py makemigrations')
print('## `./manage.py migrate`')
runshell('sudo docker exec -it admin ./manage.py migrate')
print('## `./manage.py createsuperuser`')
runshell('sudo docker exec -it admin ./manage.py createsuperuser')

print('''
####
## Installing Node-RED and nodes required by flows.json
####
''')

args = ("sudo","docker-compose","up","-d","node-red")
popen = Popen(args, stdout=PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

runshell('sudo docker exec -it node-red npm install {node-red-contrib-cheerio,node-red-contrib-moment,node-red-contrib-sqlstring,node-red-dashboard,node-red-node-feedparser,node-red-node-sqlite,node-red-node-ui-list,node-red-contrib-persist,node-red-contrib-rss,node-red-contrib-meta,node-red-contrib-join-wait,node-red-contrib-postgresql,node-red-contrib-re-postgres,node-red-contrib-string}')

original = r'flows.json'
target = r'nodered-data/flows.json'
shutil.copyfile(original, target)

print('### Restarting Node-RED container')
runshell('sudo docker restart node-red')

print('### Starting Metabase container')
runshell('sudo docker-compose up -d metabase')

print('''
####
## Next steps
####

There are some things outside the scope of this setup script.

## Setup Nginx

You can find an example configuration in `nginx-example-configuration/nginx.conf`.

## Configure your Node-RED flows 

Visit https://nodered.''' + os.getenv('DOMAIN_NAME') + ''''/ or http://localhost:1880/ to check and configure Node-RED flows for your research. This is meant to help with sites that don't have an RSS Feed.

## Configure your RSS Sources

Visit https://api.''' + os.getenv('DOMAIN_NAME') + '''/admin or http://localhost:8000/ to configure your publication sources (journals, news websites, clinical trials, etc.)


## Email service

We use mailgun to send emails, check the .env file for the settings and remember to configure your DNS.


## Setup database maintenance tasks

Gregory needs to run a series of tasks to fetch missing information and apply the machine learning algorithm. For that, we are using [Django-Con](https://github.com/Tivix/django-cron). Add the following to your crontab:

```cron
*/5 * * * * /usr/bin/docker exec admin ./manage.py runcrons > /root/log
```

## Configure and run hugo (optional)

```bash
cd hugo && npm i && cd ..;
```

In the `hugo` dir you will find a `config.toml` file that needs to be configured with your domain.

**Build the website** by running `python3 ./build.py`.
''')