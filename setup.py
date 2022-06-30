#!/usr/bin/python3
from pathlib import Path
import docker
import git
import os
import requests
import shutil
import subprocess
import sys
from shutil import which
from dotenv import load_dotenv
load_dotenv()
# TO DO: Run docker-compose up as root

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
	''')

	configs = {
	"DB_HOST" : os.getenv('DB_HOST'),
	"DOMAIN_NAME" : os.getenv('DOMAIN_NAME'),
	"EMAIL_DOMAIN" : os.getenv('EMAIL_DOMAIN'),
	"EMAIL_HOST_PASSWORD" : os.getenv('EMAIL_HOST_PASSWORD'), 
	"EMAIL_HOST_USER" : os.getenv('EMAIL_HOST_USER'), 
	"EMAIL_HOST" : os.getenv('EMAIL_HOST'), 
	"EMAIL_MAILGUN_API_URL" : os.getenv('EMAIL_MAILGUN_API_URL'),
	"EMAIL_MAILGUN_API" : os.getenv('EMAIL_MAILGUN_API'),
	"EMAIL_PORT" : os.getenv('EMAIL_PORT'), 
	"EMAIL_USE_TLS" : os.getenv('EMAIL_USE_TLS'), 
	"GREGORY_DIR" : os.getenv('GREGORY_DIR'),
	"HUGO_PATH" : os.getenv('HUGO_PATH'),
	"METABASE_SECRET_KEY" : os.getenv('METABASE_SECRET_KEY'),
	"METABASE_SITE_URL" : os.getenv('METABASE_SITE_URL'),
	"POSTGRES_DB" : os.getenv('POSTGRES_DB'),
	"POSTGRES_PASSWORD" : os.getenv('POSTGRES_PASSWORD'),
	"POSTGRES_USER" : os.getenv('POSTGRES_USER'),
	"SECRET_KEY" : os.getenv('SECRET_KEY'),
	"SERVER" : os.getenv('SERVER'),
	"WEBSITE_PATH" : os.getenv('WEBSITE_PATH'),
	}

	for key,value in configs.items():
		if value == None:
			value = input('please enter value for ' + key + ': ')
			configs[key] = value
	with open('.env','a') as file:
		for key,value in configs.items():
			line = key + '=\'' + value + '\'\n'
			file.write(line)
		file.close()
	print('Settings written to .env file, please check if everything looks correct.')
	input("Press Enter to continue...")



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

if is_git_repo(cwd) == False or git.Repo(cwd).remotes[0].config_reader.get("url") != github:
	print("Didn't find any git repository, or repository does not match Gregory. Cloning into ./gregory now, please wait...")
	git.Git(".").clone(github)
	os.chdir("./gregory")

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
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)


print('''
####
## Running docker-compose up -d --build
## This will launch Django, NodeRed, and Postgres
####
''')

args = ("sudo","docker-compose","up","-d","--build")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

print('''
####
## Migrate PostGres schema (WIP, not working)
####


Trying to run `python manage.py makemigrations && python manage.py migrate && python manage.py createsuperuser` to setup the postgres database and django.
If this command fails
''')
args = ("sudo","docker","exec","-it","admin","python manage.py makemigrations")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

args = ("sudo","docker","exec","-it","admin","python manage.py migrate")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

args = ("sudo","docker","exec","-it","admin","python manage.py createsuperuser")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)
print('''
####
## Next steps
####

There are some things outside the scope of this setup script.

## Node-RED flows to index content

For each source there is a a flow (a tab on Node-RED) that runs a search and saves the results in the database. Open the file gregory_schema.sql to understand how the database tables are configured.

If you wish to apply Gregory to your own research subject, you will have to delete these flows and configure your own. The ones present are just functional examples to guide you.

## Email service

We use mailgun to send emails, check the .env file for the settings.
''')