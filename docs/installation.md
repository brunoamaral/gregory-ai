# Gregory Installation Guide

This guide will walk you through the process of installing and configuring Gregory on your server.

## Prerequisites

- Docker and docker-compose
- Minimum 2GB of swap memory for building Machine Learning Models
- Mailgun account (optional, for email notifications)
- ORCID API credentials (optional, for author identification)

## Installation Steps

### 1. Clone and Initial Setup

```bash
git clone <repository_url>
cd <repository_directory>
cp example.env .env
```

Edit the `.env` file with your configuration details.

### 2. Start Containers

```bash
docker compose up -d
docker exec admin python manage.py makemigrations
docker exec admin python manage.py migrate
```

### 3. Create Admin User

```bash
docker exec -it admin python manage.py createsuperuser
```

### 4. Configure DNS (Production Only)

#### API Subdomain

Add an A record for `api.yourdomain.com` pointing to your server's IP address.

#### Mailgun Domain (Optional)

If using Mailgun, add the required DNS records for `mg.yourdomain.com`:
- TXT record
- MX record
- CNAME record

### 5. Configure Email (Optional)

Add these to your `.env` file:

```
EMAIL_USE_TLS=true
EMAIL_MAILGUN_API='YOUR API KEY'
EMAIL_DOMAIN='YOURDOMAIN'
EMAIL_MAILGUN_API_URL="https://api.eu.mailgun.net/v3/YOURDOMAIN/messages"
```

### 6. Set Up Cron Jobs

Add these to your crontab for regular maintenance:

```cron
# Run cron jobs every 3 minutes
*/3 * * * * /usr/bin/docker exec -t admin ./manage.py runcrons

# Get takeaways every 5 minutes
*/5 * * * * /usr/bin/flock -n /tmp/get_takeaways /usr/bin/docker exec admin ./manage.py get_takeaways

# Run pipeline every 12 hours
25 */12 * * * /usr/bin/flock -n /tmp/pipeline /usr/bin/docker exec admin ./manage.py pipeline

# Send admin summary every 2 days
0 8 */2 * * /usr/bin/docker exec admin python manage.py send_admin_summary

# Send weekly summary every Tuesday
5 8 * * 2 docker exec admin python manage.py send_weekly_summary
```

## Post-Installation Configuration

### Configure Site Settings

1. Log in to the admin panel at `http://yourdomain.com/admin/`
2. Go to "Sites" and update the example.com site to match your domain
3. Go to "Custom Settings" and set the Site and Title fields

### Add Sources

1. Go to "Sources" in the admin panel
2. Click "Add Source" and configure your RSS feeds or manual sources

## Troubleshooting

If you encounter issues during installation:

1. Check Docker container logs: `docker logs admin`
2. Verify database connection in `.env` file
3. Ensure required ports are open in your firewall
4. Check for disk space issues if ML models fail to build

For more specific issues, please refer to the [Developer Guide](../dev/index.md).
