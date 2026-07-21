# Production Deployment Guide

## Quick Start (Oracle Cloud Free Tier)

### Prerequisites
- Oracle Cloud Free Tier account
- VM Ubuntu 22.04+ (ARM or x86)
- Domain atau DuckDNS subdomain
- Telegram Bot Token

### One-Command Deploy

SSH ke Oracle VM, lalu:

```bash
# Upload deploy script dari local
scp deploy/deploy.sh ubuntu@YOUR_IP:~/

# Run deployment
ssh ubuntu@YOUR_IP
sudo bash deploy.sh
```

Script akan otomatis:
1. Install dependencies (Python, Nginx, Certbot)
2. Clone repo dari GitHub
3. Setup Python virtualenv
4. Konfigurasi Nginx + SSL
5. Setup systemd service
6. Set Telegram webhook

### Manual Setup

Kalau prefer manual control, follow steps di bawah.

#### 1. Setup Domain

**Opsi A: DuckDNS (Free)**
- Daftar di https://www.duckdns.org
- Ambil subdomain: `ochobot.duckdns.org`
- Point ke IP Oracle VM

**Opsi B: Beli domain**
- Beli domain murah (~Rp 50k/tahun)
- Setup Cloudflare DNS
- A record → Oracle VM IP

#### 2. Setup VM

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3-pip python3-venv nginx certbot python3-certbot-nginx git ufw
```

#### 3. Setup Bot

```bash
# Clone repo
git clone https://github.com/haqqii/Trading-Bot.git
cd Trading-Bot

# Virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Set TELEGRAM_BOT_TOKEN, WEBHOOK_URL
```

#### 4. Setup SSL

```bash
# Get SSL certificate (Let's Encrypt)
sudo certbot --nginx -d your-domain.com

# Auto-renewal (sudah auto via systemd)
sudo systemctl status certbot.timer
```

#### 5. Setup Nginx

File: `/etc/nginx/sites-available/ochobot`

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ochobot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 6. Setup Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
```

#### 7. Setup systemd Service

File: `/etc/systemd/system/ochobot.service`

```ini
[Unit]
Description=Ochobot Telegram Bot
After=network.target nginx.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Trading-Bot
Environment="PATH=/home/ubuntu/Trading-Bot/venv/bin"
EnvironmentFile=/home/ubuntu/Trading-Bot/.env
ExecStart=/home/ubuntu/Trading-Bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ochobot
sudo systemctl start ochobot
sudo systemctl status ochobot
```

#### 8. Set Webhook

```bash
# Set webhook
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.com"

# Verify
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

---

## Maintenance

### View Logs
```bash
sudo journalctl -u ochobot -f
```

### Restart Bot
```bash
sudo systemctl restart ochobot
```

### Update Bot Code
```bash
cd /home/ubuntu/Trading-Bot
./update.sh
```

Or manual:
```bash
cd /home/ubuntu/Trading-Bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart ochobot
```

### Backup Database
```bash
./backup.sh
```

Or manual:
```bash
cp /home/ubuntu/Trading-Bot/data/ochobot.db /home/ubuntu/backups/ochobot_$(date +%Y%m%d).db
```

### Switch to Polling Mode (Fallback)

Edit `.env`:
```
WEBHOOK_URL=
```

Restart:
```bash
sudo systemctl restart ochobot
```

---

## Troubleshooting

### Bot Not Starting

```bash
# Check logs
sudo journalctl -u ochobot -n 50

# Check if port 8443 is in use
sudo netstat -tlnp | grep 8443

# Test manually
cd /home/ubuntu/Trading-Bot
source venv/bin/activate
python main.py
```

### Webhook Not Working

```bash
# Check webhook info
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Test webhook endpoint
curl https://your-domain.com

# Check Nginx logs
sudo tail -f /var/log/nginx/error.log
```

### SSL Certificate Issues

```bash
# Renew certificate
sudo certbot renew

# Test renewal
sudo certbot renew --dry-run
```

---

## Switching Between Webhook and Polling

### Polling (Default, Easier)
```
WEBHOOK_URL=
```

### Webhook (Faster, Production)
```
WEBHOOK_URL=https://your-domain.com
```

Restart bot after change:
```bash
sudo systemctl restart ochobot
```

---

## Cost Breakdown (Free Tier)

| Component | Provider | Cost |
|-----------|----------|------|
| VM (Ubuntu ARM) | Oracle Cloud | Free (Always Free) |
| Public IP | Oracle Cloud | Free |
| Domain | DuckDNS | Free |
| SSL Certificate | Let's Encrypt | Free |
| DNS (optional) | Cloudflare | Free |
| **Total** | | **Free** |

---

## Backup Strategy

1. **Database Backup**: Daily cron at 2 AM (script: `backup.sh`)
2. **Config Backup**: Version controlled in `.env` (excluded from git, but can be backed up separately)
3. **Code Backup**: Git repository on GitHub
4. **Retention**: Keep 7 days of backups

Manual backup:
```bash
# Full backup
cd /home/ubuntu/Trading-Bot
tar -czf /home/ubuntu/backups/full_$(date +%Y%m%d).tar.gz \
    data/ logs/ .env
```

---

## Performance Tuning

### Nginx Worker Processes
Edit `/etc/nginx/nginx.conf`:
```
worker_processes auto;
worker_connections 1024;
```

### Database Optimization
```bash
# Run vacuum on SQLite
sqlite3 /home/ubuntu/Trading-Bot/data/ochobot.db "VACUUM;"
```

### Bot Resource Limits
Edit `/etc/systemd/system/ochobot.service`:
```ini
[Service]
MemoryMax=512M
CPUQuota=50%
```