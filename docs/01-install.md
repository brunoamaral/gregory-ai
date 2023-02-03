# Install

## Server Requirements

- [ ] Python 3.9
- [ ] [Docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) with 2GB of swap memory to be able to build the MachineLearning Models. ([Adding swap for Ubuntu](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-20-04))
- [ ] [Hugo](https://gohugo.io/)
- [ ] [Mailgun](https://www.mailgun.com/) (optional)

## Installing Gregory

1. **Install python dependencies locally**
2. **Edit the .env file** to reflect your settings and credentials.

```bash
DOMAIN_NAME=DOMAIN.COM
# Set this to the subdomain you configured with Mailgun. Example: mg.domain.com
EMAIL_DOMAIN=
# The SMTP server and credentials you are using. For example: smtp.eu.mailgun.org
EMAIL_HOST=
EMAIL_HOST_PASSWORD=
EMAIL_HOST_PASSWORD=
EMAIL_HOST_USER=
# We use Mailgun by default on the newsletters, input your API key here
EMAIL_MAILGUN_API_URL=
EMAIL_PORT=587
EMAIL_USE_TLS='True'
# Where you cloned the repository>
GREGORY_DIR=
# Leave this blank and come back to them when you're finished installing Metabase.
METABASE_SECRET_KEY=
# Where do you want to host Metabase?
METABASE_SITE_URL='https://metabase.DOMAIN.COM/'
# Set your postgres DB and credentials
POSTGRES_DB=
POSTGRES_PASSWORD=
POSTGRES_USER=
SECRET_KEY='Yeah well, you know, that is just, like, your DJANGO SECRET_KEY, man' # you should set this manually https://docs.djangoproject.com/en/4.0/ref/settings/#secret-key
```

3. **Execute** `python3 setup.py`. 

The script will check if you have all the requirements and run help you setup the containers

Once finished, login at <https://api.DOMAIN.TLD/admin> or wherever your reverse proxy is listening on.

4. **Configure** your RSS Sources in the Django admin page

5. **Setup** database maintenance tasks

Gregory needs to run a series of tasks to fetch missing information and apply the machine learning algorithm. For that, we are using [Django-Con](https://github.com/Tivix/django-cron). Add the following to your crontab:

```cron
*/5 * * * * /usr/bin/docker exec admin ./manage.py runcrons > /root/log
```

6.  **Install** hugo

You need to install some node modules for hugo to build and process the css. Simply run this.

```bash
cd hugo && npm i && cd ..;
```

In the `hugo` dir you will find a `config.toml` file that needs to be configured with your domain.

7. **Build** the website by running `python3 ./build.py`.
