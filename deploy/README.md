# Deploy Bot Saham ke Oracle Cloud Free Tier

Panduan lengkap deploy bot ke Oracle Cloud Always Free ARM (Singapore region).

---

## Prerequisites

- Akun Oracle Cloud (oracle.com/cloud/free) — perlu kartu kredit tapi gak di-charge
- GitHub repo bot ini

---

## 1. Buat Oracle Cloud Instance

1. Login ke [cloud.oracle.com](https://cloud.oracle.com)
2. Pilih **Compartment** → root (atau buat baru)
3. Menu samping: **Compute** → **Instances** → **Create Instance**
4. **Name:** `bot-saham-vm`
5. **Image:** Ubuntu 24.04 LTS (ARM) — penting: pilih **ARM**, bukan x86
6. **Shape:** `VM.Standard.A1.Flex` (4 OCPU, 24GB RAM) — Always Free eligible
7. **Assign public IP:** ✅ centang
8. **SSH Keys:** upload public key (generate dulu di lokal):
    ```bash
    # Jalankan di terminal lokal
    ssh-keygen -t ed25519 -C "bot-saham" -f ~/.ssh/bot_saham_key
    ```
    Upload file `~/.ssh/bot_saham_key.pub` ke Oracle
9. Klik **Create** — tunggu ~2 menit instance ready

---

## 2. Setup Ubuntu (Initial Server)

SSH ke VM:

```bash
ssh -i ~/.ssh/bot_saham_key ubuntu@<PUBLIC_IP>
```

Update & install dependencies:

```bash
sudo apt update && sudo apt upgrade -y

# Install Python 3.11 + pip + git
sudo apt install -y python3.11 python3.11-venv python3-pip git

# Verifikasi Python
python3 --version  # harusnya 3.11.x
```

---

## 3. Clone Repo & Setup Environment

```bash
# Buat direktori bot
mkdir -p ~/bot-saham && cd ~/bot-saham

# Clone repo (ganti URL ini dengan repo kamu)
git clone https://github.com/USERNAME/bot-saham-2.git .

# Buat virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Buat .env dengan BOT_TOKEN
nano .env
# Isi: BOT_TOKEN=your_telegram_bot_token_here
```

> **PENTING:** Set timezone ke Jakarta:
```bash
sudo timedatectl set-timezone Asia/Jakarta
date  # harusnya tampil WIB (UTC+7)
```

---

## 4. Test Run

```bash
source ~/bot-saham/venv/bin/activate
python main.py
# Tekan Ctrl+C untuk stop setelah verifikasi bot jalan
```

---

## 5. Install systemd Service

Salin service file dari repo:

```bash
sudo cp ~/bot-saham/deploy/bot-saham.service /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/bot-saham.service
sudo systemctl daemon-reload
sudo systemctl enable bot-saham
sudo systemctl start bot-saham

# Cek status
sudo systemctl status bot-saham
```

Harusnya muncul `active (running)`. Cek log:
```bash
sudo journalctl -u bot-saham -n 50 -f
```

---

## 6. Setup Firewall (UFW)

```bash
# Allow SSH (WAJIB, jangan sampe ketutup)
sudo ufw allow 22/tcp

# Enable firewall
sudo ufw --force enable
sudo ufw status
```

---

## 7. Update Bot (Deploy Kode Baru)

```bash
cd ~/bot-saham
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart bot-saham
```

---

## 8. Perintah Berguna

```bash
# Cek status
sudo systemctl status bot-saham

# Log real-time
sudo journalctl -u bot-saham -f

# Restart
sudo systemctl restart bot-saham

# Stop
sudo systemctl stop bot-saham

# Cek apakah alive
sudo systemctl is-active bot-saham
```

---

## 9. Troubleshooting

### Bot gak start setelah reboot
```bash
sudo systemctl is-enabled bot-saham  # harus "enabled"
sudo journalctl -u bot-saham -n 50   # cek error
```

### Locked out SSH (firewall salah)
Login lewat Oracle Cloud Console → **Console Connection** → **Connect**

### Bot crash loop
```bash
sudo journalctl -u bot-saham --since "1 hour ago" | grep -i error
```

---

## Biaya

| Item | Biaya |
|------|-------|
| Oracle Cloud Always Free ARM VM | **GRATIS** |
| Total | **GRATIS** |

> Selalu pakai **Always Free** resources. Jangan tambah paid services.