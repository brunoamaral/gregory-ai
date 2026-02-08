#!/usr/bin/python3
"""GregoryAI setup script.

Walks through initial configuration: .env creation, dependency checks,
Docker container bootstrapping, Django migrations, and superuser creation.
"""
from dotenv import load_dotenv
from pathlib import Path
from shutil import which
from subprocess import Popen, PIPE
import os
import requests
import sys
import time

load_dotenv()
cwd = os.getcwd()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_command(cmd, shell=False, exit_on_error=True):
	"""Run a shell command, stream output, and optionally exit on failure."""
	print(f"  → {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
	proc = Popen(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True, shell=shell)
	stdout, stderr = proc.communicate()
	if stdout.strip():
		print(stdout)
	if proc.returncode != 0:
		print(f"Error (exit {proc.returncode}):")
		if stderr.strip():
			print(stderr)
		if exit_on_error:
			sys.exit(proc.returncode)
	return proc.returncode, stdout, stderr


def is_tool(name):
	"""Check whether *name* is on PATH and marked as executable."""
	return which(name) is not None


def generate_fernet_key():
	"""Return a fresh Fernet key as a string."""
	try:
		from cryptography.fernet import Fernet
		return Fernet.generate_key().decode()
	except ImportError:
		print(
			"Warning: 'cryptography' package not installed. "
			"Please generate a FERNET_SECRET_KEY manually:\n"
			'  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
		)
		return ""


# ---------------------------------------------------------------------------
# 1. .env file
# ---------------------------------------------------------------------------

print("""
####
## Check for .env file
####
""")
env_file = Path(".env")

if env_file.is_file():
	print("\N{check mark} Found .env file")
else:
	print("""
####
## Configure GregoryAI
####

Did not find a .env file. We need to set some configuration variables.
If in doubt, press Enter to leave a value blank and edit .env later.

Optional variables: EMAIL_*, ORCID_*
""")

	fernet_key = generate_fernet_key()

	configs = {
		"DB_HOST": "db",
		"DOMAIN_NAME": os.getenv("DOMAIN_NAME"),
		"EMAIL_DOMAIN": os.getenv("EMAIL_DOMAIN"),
		"EMAIL_HOST": os.getenv("EMAIL_HOST"),
		"EMAIL_HOST_PASSWORD": os.getenv("EMAIL_HOST_PASSWORD"),
		"EMAIL_HOST_USER": os.getenv("EMAIL_HOST_USER"),
		"EMAIL_POSTMARK_API_KEY": os.getenv("EMAIL_POSTMARK_API_KEY"),
		"EMAIL_POSTMARK_API_URL": os.getenv("EMAIL_POSTMARK_API_URL", "https://api.postmarkapp.com/email"),
		"EMAIL_PORT": 587,
		"EMAIL_USE_TLS": "true",
		"FERNET_SECRET_KEY": os.getenv("FERNET_SECRET_KEY") or fernet_key,
		"GREGORY_DIR": os.getenv("GREGORY_DIR"),
		"ORCID_ClientID": os.getenv("ORCID_ClientID"),
		"ORCID_ClientSecret": os.getenv("ORCID_ClientSecret"),
		"POSTGRES_DB": os.getenv("POSTGRES_DB"),
		"POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
		"POSTGRES_USER": os.getenv("POSTGRES_USER"),
		"SECRET_KEY": os.getenv("SECRET_KEY"),
	}

	for key, value in configs.items():
		if value is None:
			value = input(f"Please enter value for {key}: ")
			configs[key] = value

	with open(".env", "a") as file:
		for key, value in configs.items():
			file.write(f"{key}={value}\n")

	print("Settings written to .env file — please check if everything looks correct.")
	input("Press Enter to continue...")
	print("Setting environment variables...")

	for key, value in configs.items():
		os.environ[key] = str(value)


# ---------------------------------------------------------------------------
# 2. Prerequisites
# ---------------------------------------------------------------------------

if is_tool("git"):
	print("\N{check mark} Git is installed, proceeding.")
else:
	sys.exit("Git was not found and I can't install Gregory without it. Exiting.")

print("""
####
## Check for directories
####
""")

p = Path("django/")
if p.is_dir():
	print("\N{check mark} Found django directory")
else:
	sys.exit("Didn't find django/ directory — aborting.")

print("""
####
## Check for docker-compose.yaml file
####
""")

docker_compose = Path("docker-compose.yaml")

if docker_compose.is_file():
	print("\N{check mark} Found docker-compose.yaml file")
else:
	print("Didn't find docker-compose.yaml, downloading latest version...")
	url = "https://raw.githubusercontent.com/brunoamaral/gregory/main/docker-compose.yaml"
	res = requests.get(url)
	with open("docker-compose.yaml", "w") as f:
		f.write(res.text)

print("""
####
## Check for Docker Compose
####
""")

# Prefer `docker compose` (v2 plugin). Fall back to standalone `docker-compose` if needed.
if run_command(["docker", "compose", "version"], exit_on_error=False)[0] == 0:
	COMPOSE = ["docker", "compose"]
	print("\N{check mark} Found docker compose (v2)")
else:
	sys.exit(
		"docker compose (v2) not found. "
		"Please install the Docker Compose plugin: https://docs.docker.com/compose/install/"
	)

# ---------------------------------------------------------------------------
# 3. Start Postgres
# ---------------------------------------------------------------------------

print("""
####
## Starting Postgres
####
""")

run_command([*COMPOSE, "up", "-d", "db"])

print("\nGive Postgres 20 seconds to finish setting up...")
for i in range(20, 0, -1):
	sys.stdout.write(f"{i} ")
	sys.stdout.flush()
	time.sleep(1)
print()

# ---------------------------------------------------------------------------
# 4. Build and start the Gregory container
# ---------------------------------------------------------------------------

print("""
####
## Building and starting the Gregory container
####
""")

run_command([*COMPOSE, "up", "-d", "--build", "gregory"])

# ---------------------------------------------------------------------------
# 5. Django setup
# ---------------------------------------------------------------------------

print("""
####
## Running Django setup
####
""")

print("## manage.py makemigrations")
run_command(["docker", "exec", "gregory", "python", "manage.py", "makemigrations"])

print("## manage.py migrate")
run_command(["docker", "exec", "gregory", "python", "manage.py", "migrate"])

print("## manage.py createsuperuser")
run_command(["docker", "exec", "-it", "gregory", "python", "manage.py", "createsuperuser"],
			exit_on_error=False)

# ---------------------------------------------------------------------------
# 6. Next steps
# ---------------------------------------------------------------------------

domain = os.getenv("DOMAIN_NAME", "DOMAIN.TLD")

print(f"""
####
## Next steps
####

There are some things outside the scope of this setup script.

## Setup your site

1. Go to the admin dashboard and change the example.com site to match your domain.
2. Go to custom settings and set the Site and Title fields.

## Setup Nginx

You can find an example configuration in `nginx-example-configuration/nginx.conf`.

## Configure your RSS Sources

Visit https://api.{domain}/admin or http://localhost:8000/ to configure your
publication sources (journals, news websites, clinical trials, etc.)

## Email service (Postmark)

GregoryAI uses Postmark to send transactional emails (newsletters, admin
digests, clinical trial notifications).

The preferred approach is to configure Postmark credentials per-team in the
Django admin (Team → postmark_api_token / postmark_api_url). The global
EMAIL_POSTMARK_API_KEY and EMAIL_POSTMARK_API_URL environment variables in .env are
used as a fallback when team-level credentials are not set.

For more information see https://postmarkapp.com/developer

## Setup cron jobs

Add the following to your crontab:

```cron
# Admin summary every 2 days at 08:00
0 8 */2 * * /usr/bin/docker exec gregory python manage.py send_admin_summary

# Weekly summary every Tuesday at 08:05
5 8 * * 2 docker exec gregory python manage.py send_weekly_summary

# Pipeline every 12 hours
25 */12 * * * /usr/bin/flock -n /tmp/pipeline /usr/bin/docker exec gregory python manage.py pipeline
```
""")
