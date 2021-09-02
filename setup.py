#!/usr/bin/python3
from pathlib import Path
from sql_metadata import Parser
import git
import os
import pathlib
import requests
import sqlite3
import subprocess
import sys

cwd = os.getcwd()
github = "git@github.com:brunoamaral/gregory.git"


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
    print("Git is installed, proceeding.")
else:
    sys.exit("Git was not found and I can't install Gregory without it. Exiting.")

if is_git_repo(cwd) == False or git.Repo(cwd).remotes[0].config_reader.get("url") != github:
    print("Didn't find any git repository, or repository does not match Gregory. Cloning into ./gregory now, please wait...")
    git.Git(".").clone(github)
    os.chdir("./gregory")




# Check for directories

p = Path("docker-data")

if p.is_dir():
    print("Found docker-data directory")
else:
    sys.exit('docker-data directory not found. Did the clone process from https://github.com/brunoamaral/gregory finish correctly?')


# Check for .env file
f = Path(".env")

if p.is_file():
    print("Found .env file")
else:
    print(".env file not found, creating with empty values")
    with open(".env", "w+") as f:
        env_file = "DOMAIN_NAME=''"
        f.write(env_file)
        f.close()

# Check for docker-compose.yaml file
docker_compose = Path("docker-compose.yaml")

if docker_compose.is_file():
    print("Found docker-compose.yaml file")
else:
    print("Didn't find docker-compose.yaml, downloading latest version from https://raw.githubusercontent.com/brunoamaral/gregory/main/docker-compose.yaml")
    # Get trials
    url = 'https://raw.githubusercontent.com/brunoamaral/gregory/main/docker-compose.yaml'
    res = requests.get(url)
    file_name = 'docker-compose.yaml'
    with open(file_name, "w") as f:
        f.write(res.text)
        f.close()

# Check for docker-compose command
if is_tool("docker-compose command installed"):
    print("Found docker-compose")
else:
    print("Didn't find docker-compose, please install it. Details at https://docs.docker.com/compose/install/")

# Check for SQLite

if is_tool("sqlite3"):
    print("Found Sqlite3, just in case")
else: 
    print("Didn't find SQLite3 (optional) on the host system, proceeding")
        
# Check for Hugo
if is_tool("hugo"):
    print("Found Hugo")
else:
    print("Didn't find Hugo, please install it. Details at https://gohugo.io")
    sys.exit('Hugo not installed')

# Updating Hugo modules
print("# Updating Hugo modules")
#`hugo mod get -u;`

args = ("/usr/local/bin/hugo", "mod", "get","-u")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)

# Check if SQLite3 db is present
database = sqlite3.connect('./docker-data/gregory.db')

if database == False:
    sys.exit('SQLite database not found, create it please')

# Check Database is OK
schema = open('gregory_schema.sql', 'r')
Lines = schema.readlines()

for line in Lines:
    table = Parser(line)
    for t in table.tables:
        query ='select * from '+ t +';'
        # This is going to break the script if it can't find the table
        table_exists = database.execute(query).description
        if table_exists:
            print("Found table: " + t)
        for column in table.columns:
            query ='select '+ column +' from '+ t +';'
            column_exists=database.execute(query).description
            if column_exists:
                print("Found column: " + column)

# TO DO: If the Database is not ok, run corresponding SQL

print("Running Node-RED, open http://127.0.0.1:1880/ on your browser to confirm Node-Red is working.")

args = ("/usr/local/bin/docker-compose", "up")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)
