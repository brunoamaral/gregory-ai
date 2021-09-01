#!/usr/bin/python3
import os
import subprocess
import pathlib
from pathlib import Path
import git
import sys

def is_tool(name):
    """Check whether `name` is on PATH and marked as executable."""

    # from whichcraft import which
    from shutil import which

    return which(name) is not None

# Check for directories

p = Path("docker-data")

if p.is_dir():
    print("Found docker-data directory")
else:
    sys.exit('docker-data directory not found. Did the clone process from https://github.com/brunoamaral/gregory finish correctly?')


# Check for .env file
f = Path(".env")

if p.is_file:
    print("Found .env file")
else:
    print(".env file not found, creating with empty values")
    with open(".env", "w+") as f:
        env_file = "DOMAIN_NAME=''"
    f.write(env_file)
    f.close()

# Check for docker-compose.yaml file
docker_compose = Path("docker-compose.yaml")

if docker_compose.is_file:
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
if is_tool("docker-compose"):
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

# Launch Node-RED
print("Running Node-RED, open http://127.0.0.1:1880/ on your browser to confirm Node-Red is working.")

args = ("/usr/local/bin/docker-compose", "up")
popen = subprocess.Popen(args, stdout=subprocess.PIPE, universal_newlines=True)
popen.wait()
output = popen.stdout.read()
print(output)
