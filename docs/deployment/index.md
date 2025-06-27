# Deployment Guide

This guide covers deploying Gregory in production environments.

## Production Requirements

- **Server**: Linux-based server with at least 4GB RAM and 2 vCPUs
- **Storage**: Minimum 20GB SSD storage
- **Memory**: At least 2GB of swap memory for ML model building
- **Software**: Docker and docker-compose
- **Domain**: A domain with DNS control for setting up subdomains
- **Email**: Mailgun account (or alternative SMTP provider)

## Deployment Steps

### 1. Server Preparation

1. Update your server:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. Install Docker and docker-compose:
   ```bash
   # Docker
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   
   # Docker Compose
   sudo curl -L "https://github.com/docker/compose/releases/download/v2.15.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
   ```

3. Add swap memory (if needed):
   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

### 2. Application Deployment

1. Clone the repository:
   ```bash
   git clone <repository_url> /opt/gregory
   cd /opt/gregory
   ```

2. Configure environment:
   ```bash
   cp example.env .env
   # Edit .env with production settings
   ```

3. Start the application:
   ```bash
   docker-compose up -d
   ```

4. Run initial setup:
   ```bash
   docker exec admin python manage.py migrate
   docker exec admin python manage.py createsuperuser
   ```

### 3. Web Server Configuration

#### Nginx Setup

1. Install Nginx:
   ```bash
   sudo apt install nginx -y
   ```

2. Configure Nginx:
   ```bash
   sudo nano /etc/nginx/sites-available/gregory
   ```

3. Add server block:
   ```nginx
   server {
       listen 80;
       server_name api.yourdomain.com;
       
       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
       
       location /static/ {
           alias /opt/gregory/django/static/;
       }
       
       location /media/ {
           alias /opt/gregory/django/media/;
       }
   }
   ```

4. Enable the site:
   ```bash
   sudo ln -s /etc/nginx/sites-available/gregory /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

#### SSL with Certbot

1. Install Certbot:
   ```bash
   sudo apt install certbot python3-certbot-nginx -y
   ```

2. Obtain certificates:
   ```bash
   sudo certbot --nginx -d api.yourdomain.com
   ```

### 4. Database Backup

Set up regular database backups:

```bash
# Create backup script
cat > /opt/gregory/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/gregory/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p $BACKUP_DIR
docker exec gregory_postgres pg_dump -U postgres gregory > $BACKUP_DIR/gregory_$TIMESTAMP.sql
find $BACKUP_DIR -type f -mtime +7 -delete
EOF

# Make executable
chmod +x /opt/gregory/backup.sh

# Add to crontab
(crontab -l 2>/dev/null; echo "0 2 * * * /opt/gregory/backup.sh") | crontab -
```

### 5. Monitoring and Maintenance

Set up cron jobs for maintenance:

```bash
# Add to crontab
(crontab -l 2>/dev/null; echo "*/3 * * * * /usr/bin/docker exec -t admin ./manage.py runcrons") | crontab -
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/flock -n /tmp/get_takeaways /usr/bin/docker exec admin ./manage.py get_takeaways") | crontab -
(crontab -l 2>/dev/null; echo "25 */12 * * * /usr/bin/flock -n /tmp/pipeline /usr/bin/docker exec admin ./manage.py pipeline") | crontab -
(crontab -l 2>/dev/null; echo "0 8 */2 * * /usr/bin/docker exec admin python manage.py send_admin_summary") | crontab -
(crontab -l 2>/dev/null; echo "5 8 * * 2 docker exec admin python manage.py send_weekly_summary") | crontab -
```

### 6. Logging and Monitoring

1. View logs:
   ```bash
   docker logs admin
   docker logs postgres
   ```

2. Set up log rotation:
   ```bash
   sudo nano /etc/logrotate.d/docker
   ```

   Add:
   ```
   /var/lib/docker/containers/*/*.log {
       rotate 7
       daily
       compress
       size=10M
       missingok
       delaycompress
       copytruncate
   }
   ```

## Scaling

For larger deployments:

1. Separate database server
2. Redis for caching
3. Multiple application containers behind load balancer
4. CDN for static assets

## Security Considerations

1. Restrict SSH access
2. Configure firewall (UFW)
3. Regular security updates
4. Database credential rotation
5. API rate limiting

## Disaster Recovery

1. Regular database backups
2. Configuration backup
3. Docker image versioning
4. Documented recovery procedures

## Upgrading Gregory

1. Pull the latest code:
   ```bash
   cd /opt/gregory
   git pull
   ```

2. Rebuild containers:
   ```bash
   docker-compose down
   docker-compose up -d --build
   ```

3. Run migrations:
   ```bash
   docker exec admin python manage.py migrate
   ```

4. Collect static files:
   ```bash
   docker exec admin python manage.py collectstatic --noinput
   ```
