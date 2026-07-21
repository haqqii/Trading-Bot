#!/bin/bash
# ============================================
# Ochobot Production Deployment Script
# For Ubuntu/Debian (Oracle Cloud Free Tier)
# ============================================

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root: sudo bash deploy.sh"
    exit 1
fi

# ============================================
# 1. SETUP VARIABLES
# ============================================
echo ""
echo "============================================"
echo "  Ochobot Production Deployment"
echo "============================================"
echo ""

read -p "Enter your Telegram bot token: " BOT_TOKEN
read -p "Enter your domain (e.g., ochobot.duckdns.org): " DOMAIN
read -p "Enter your email (for SSL): " EMAIL
read -p "Enter GitHub repo URL [https://github.com/haqqii/Trading-Bot.git]: " REPO_URL
REPO_URL=${REPO_URL:-https://github.com/haqqii/Trading-Bot.git}

BOT_USER="ubuntu"
BOT_DIR="/home/$BOT_USER/Trading-Bot"
SERVICE_NAME="ochobot"

log_info "Configuration:"
echo "  Domain: $DOMAIN"
echo "  Email: $EMAIL"
echo "  Bot User: $BOT_USER"
echo "  Bot Directory: $BOT_DIR"
echo ""

# ============================================
# 2. UPDATE SYSTEM
# ============================================
log_info "Updating system packages..."
apt update && apt upgrade -y

# ============================================
# 3. INSTALL DEPENDENCIES
# ============================================
log_info "Installing dependencies..."
apt install -y python3-pip python3-venv python3-dev \
    nginx certbot python3-certbot-nginx \
    git curl wget ufw

# ============================================
# 4. CLONE REPO
# ============================================
if [ -d "$BOT_DIR" ]; then
    log_warn "Bot directory already exists. Pulling latest..."
    cd $BOT_DIR
    sudo -u $BOT_USER git pull
else
    log_info "Cloning repository..."
    sudo -u $BOT_USER git clone $REPO_URL $BOT_DIR
    cd $BOT_DIR
fi

# ============================================
# 5. SETUP PYTHON VIRTUALENV
# ============================================
log_info "Setting up Python virtual environment..."
sudo -u $BOT_USER python3 -m venv venv
sudo -u $BOT_USER bash -c "source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# ============================================
# 6. CREATE .ENV FILE
# ============================================
log_info "Creating .env file..."
cat > $BOT_DIR/.env << EOF
# Telegram Bot Token
TELEGRAM_BOT_TOKEN=$BOT_TOKEN

# Optional API Keys
FINNHUB_API_KEY=
NEWS_API_KEY=

# === WEBHOOK SETTINGS ===
WEBHOOK_URL=https://$DOMAIN
WEBHOOK_PATH=/
WEBHOOK_PORT=8443
EOF

chown $BOT_USER:$BOT_USER $BOT_DIR/.env
chmod 600 $BOT_DIR/.env
log_success ".env created"

# ============================================
# 7. SETUP NGINX
# ============================================
log_info "Configuring Nginx..."
cat > /etc/nginx/sites-available/$SERVICE_NAME << EOF
server {
    listen 80;
    server_name $DOMAIN;

    # Redirect to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Telegram webhook endpoint
    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60;
        proxy_connect_timeout 60;
    }
}
EOF

# Enable site
ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test config
nginx -t

# ============================================
# 8. SETUP SSL WITH CERTBOT
# ============================================
log_info "Setting up SSL certificate..."
certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $EMAIL || {
    log_warn "Certbot failed. SSL might be issue with DNS propagation."
    log_warn "Check if domain points to this server's IP."
    log_warn "You can run: certbot --nginx -d $DOMAIN later."
}

# ============================================
# 9. CONFIGURE FIREWALL
# ============================================
log_info "Configuring firewall..."
ufw --force enable
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw status

# ============================================
# 10. CREATE SYSTEMD SERVICE
# ============================================
log_info "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Ochobot Telegram Bot
After=network.target nginx.service

[Service]
Type=simple
User=$BOT_USER
Group=$BOT_USER
WorkingDirectory=$BOT_DIR
Environment="PATH=$BOT_DIR/venv/bin"
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

# ============================================
# 11. START SERVICES
# ============================================
log_info "Starting services..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart nginx

# Wait for nginx to be ready
sleep 3

# Start bot
systemctl start $SERVICE_NAME
sleep 5

# Check status
systemctl status $SERVICE_NAME --no-pager

# ============================================
# 12. SET TELEGRAM WEBHOOK
# ============================================
log_info "Setting Telegram webhook..."
WEBHOOK_URL="https://$DOMAIN/bot$BOT_TOKEN"

# Try to set webhook via bot API
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook?url=${WEBHOOK_URL}" || {
    log_warn "Failed to set webhook. Set it manually:"
    log_warn "curl -X POST \"https://api.telegram.org/bot<TOKEN>/setWebhook?url=${WEBHOOK_URL}\""
}

# Verify webhook
echo ""
log_info "Webhook info:"
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | python3 -m json.tool

# ============================================
# 13. CREATE MAINTENANCE SCRIPTS
# ============================================
log_info "Creating maintenance scripts..."

# Backup script
cat > /home/$BOT_USER/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=/home/ubuntu/backups
mkdir -p $BACKUP_DIR
cp /home/ubuntu/Trading-Bot/data/ochobot.db $BACKUP_DIR/ochobot_$(date +%Y%m%d_%H%M%S).db
# Keep only last 7 days
find $BACKUP_DIR -name "ochobot_*.db" -mtime +7 -delete
echo "Backup done: $BACKUP_DIR"
EOF
chmod +x /home/$BOT_USER/backup.sh
chown $BOT_USER:$BOT_USER /home/$BOT_USER/backup.sh

# Update script
cat > /home/$BOT_USER/update.sh << 'EOF'
#!/bin/bash
cd /home/ubuntu/Trading-Bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart ochobot
echo "Bot updated and restarted"
EOF
chmod +x /home/$BOT_USER/update.sh
chown $BOT_USER:$BOT_USER /home/$BOT_USER/update.sh

# Add backup to crontab (daily at 2 AM)
(crontab -u $BOT_USER -l 2>/dev/null; echo "0 2 * * * /home/$BOT_USER/backup.sh") | crontab -u $BOT_USER -

# ============================================
# DONE
# ============================================
echo ""
echo "============================================"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "============================================"
echo ""
echo "✓ Bot service: systemctl status $SERVICE_NAME"
echo "✓ Nginx: systemctl status nginx"
echo "✓ Logs: sudo journalctl -u $SERVICE_NAME -f"
echo "✓ Backup: /home/$BOT_USER/backup.sh"
echo "✓ Update: /home/$BOT_USER/update.sh"
echo ""
echo "Webhook URL: https://$DOMAIN"
echo "Test bot: send /start to your bot"
echo ""

log_info "Useful commands:"
echo "  systemctl status $SERVICE_NAME    # Check status"
echo "  systemctl restart $SERVICE_NAME   # Restart bot"
echo "  systemctl stop $SERVICE_NAME      # Stop bot"
echo "  journalctl -u $SERVICE_NAME -f    # View logs"
echo "  bash /home/$BOT_USER/update.sh    # Update bot"
echo ""
EOF