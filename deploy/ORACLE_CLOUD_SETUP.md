# PANDUAN LENGKAP SETUP ORACLE CLOUD + DOCKER
# Bot Saham Telegram - Always On 24/7
# Generated: 2026-05-28

================================================================================
BAGIAN 1: DAFTAR ORACLE CLOUD FREE TIER
================================================================================

1. Buka browser, pergi ke:
   https://www.oracle.com/cloud/free/

2. Klik "Start for Free"

3. Pilih "Sign Up" (bukan trial berbayar)

4. Isi formulir:
   - Country: Indonesia
   - Email: email@domain.com
   - Name: Nama Lengkap (sesuai KTP)
   - Password: Buat password kuat

5. Verifikasi email:
   - Buka email dari Oracle
   - Klik link verifikasi
   - Selesai!

================================================================================
BAGIAN 2: BUAT ALWAYS FREE VIRTUAL MACHINE
================================================================================

2.1 LOGIN KE ORACLE CLOUD
----------------------------
1. Buka: https://cloud.oracle.com/
2. Klik "Sign In"
3. Pilih Region: Jakarta (atau Singapore)
4. Masukkan email & password

2.2 BUAT NAMESPACE (PERTAMA KALI SAJA)
---------------------------------------
1. Klik menu hamburger (左上) → Identity & Security → Tenancy
2. Atau langsung ke: https://cloud.oracle.com/tenancy
3. Buat OCID unik jika diminta

2.3 BUAT COMPUTE INSTANCE
--------------------------
1. Menu utama → Compute → Instances
2. Klik "Create Instance"

2.4 KONFIGURASI INSTANCE
------------------------
| Setting              | Value                      |
|----------------------|----------------------------|
| Name                | stock-bot                  |
| Compartment         | Root (leave default)       |
| Region              | Jakarta (ap-joaquboro-1)   |
| Image               | Canonical Ubuntu 22.04      |
| Shape               | VM.Standard.A1.Flex         |
| OCPU                | 1                          |
| RAM                 | 1 GB                       |
| Boot Volume Size    | 50 GB (default)            |

**PENTING:** Pastikan muncul label "Always Free" berwarna hijau!

2.5 SSH KEY
-----------
Pilih "Generate SSH Key Pair":
- Klik "Save Private Key"
- Simpan file .key di folder aman di PC
- Contoh: D:\Keys\oracle_key

2.6 SECURITY LIST (FIREWALL)
----------------------------
Ini langkah PENTING! Tanpa ini, SSH akan ditolak.

1. Scroll ke bawah ke "Networking"
2. Klik "Assign a public IP address" → biarkan default
3. Klik "Choose existing virtual cloud network"
4. Klik "Existent VCN" yang muncul
5. Klik "Subnets" → pilih subnet
6. Klik "Security Lists" → "Default Security List"
7. Klik "Add Ingress Rule":
   - Source CIDR: 0.0.0.0/0
   - IP Protocol: TCP
   - Destination Port Range: 22, 80, 443
   - Description: SSH, HTTP, HTTPS

ATAU lebih mudah:
1. Menu → Networking → Virtual Cloud Networks
2. Pilih VCN yang ada
3. Security Lists → Default
4. Add Ingress Rules sesuai di atas

2.7 KLAIK CREATE
----------------
- Tunggu 2-5 menit sampai status "RUNNING"
- Copy PUBLIC IP ADDRESS (misal: 132.145.67.89)

================================================================================
BAGIAN 3: CONNECT KE VM VIA SSH
================================================================================

3.1 BUKA TERMINAL
------------------
Windows:
- Option 1: Windows Terminal (rekomendasi)
- Option 2: PowerShell
- Option 3: Git Bash

3.2 LOGIN SSH
--------------
```bash
ssh -i "D:\Keys\oracle_key" opc@132.145.67.89
```

PENJELASAN:
- ssh          → perintah SSH
- -i "..."     → path ke private key
- opc          → username default Ubuntu di Oracle
- 132.145.67.89 → PUBLIC IP VM kamu

3.3 FIRST TIME CONNECT
----------------------
Akan muncul warning tentang host authenticity:

```
Are you sure you want to continue connecting (yes/no)?
```

Ketik: yes

3.4 JIKA GAGAL CONNECT
----------------------
Error: "Permission denied (publickey)"

Solusi:
1. Pastikan path key benar
2. Pastikan key adalah file private (bukan public)
3. Windows: Klik kanan file .key → Properties → Security → Pastikan bisa di-read

================================================================================
BAGIAN 4: INSTALL DOCKER DI UBUNTU
================================================================================

4.1 UPDATE SYSTEM
-----------------
```bash
sudo apt update && sudo apt upgrade -y
```

4.2 INSTALL DOCKER
-------------------
```bash
sudo apt install docker.io docker-compose -y
```

4.3 ENABLE DOCKER
------------------
```bash
sudo systemctl enable docker
sudo systemctl start docker
```

4.4 VERIFIKASI DOCKER
----------------------
```bash
docker --version
# Output: Docker version 24.x.x, build xxxxxxx

docker-compose --version
# Output: Docker Compose version v2.x.x
```

4.5 SETUP DOCKER WITHOUT SUDO (OPTIONAL)
-----------------------------------------
```bash
sudo usermod -aG docker opc
newgrp docker
```

TEST (tanpa sudo):
```bash
docker ps
```

================================================================================
BAGIAN 5: DEPLOY BOT
================================================================================

5.1 BUAT FOLDER PROJECT
-----------------------
```bash
mkdir -p ~/bot && cd ~/bot
```

5.2 UPLOAD FILE BOT
--------------------
Ada 3 cara:

CARA A - GIT CLONE (jika repo sudah di GitHub):
```bash
git clone https://github.com/haqqii/Trading-Bot.git .
```

CARA B - SFTP (WinSCP):
1. Download WinSCP: https://winscp.net/
2. Connect pakai:
   - Host: IP VM kamu
   - Username: opc
   - Private key: file .key
3. Upload semua file bot ke folder ~/bot

CARA C - COPY PASTE via SSH:
```bash
# Buat file satu per satu via nano
nano run.py
# Paste content, Ctrl+X, Y, Enter
```

5.3 BUAT FILE .env
------------------
```bash
nano .env
```

Isi dengan:
```env
TELEGRAM_BOT_TOKEN=ganti_dengan_token_bot_kamu
FINNHUB_API_KEY=kosongkan_atau_is_api_key
```

Simpan: Ctrl+X → Y → Enter

5.4 BUAT DOCKERFILE
-------------------
```bash
nano Dockerfile
```

Isi:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Run bot
CMD ["python", "run.py"]
```

Simpan: Ctrl+X → Y → Enter

5.5 BUAT REQUIREMENTS.TXT (jika belum ada)
-------------------------------------------
```bash
pip freeze > requirements.txt
```

5.6 BUILD DOCKER IMAGE
----------------------
```bash
docker build -t stock-bot .
```

Tunggu beberapa menit...

5.7 JALANKAN BOT
----------------
```bash
docker run -d \
  --name stock-bot \
  --restart always \
  --env-file .env \
  stock-bot
```

PENJELASAN:
- -d              → run di background (detached)
- --name          → nama container
- --restart always → auto start kalau server reboot
- --env-file      → load environment variables

5.8 CEK STATUS BOT
-------------------
```bash
# Lihat semua container
docker ps

# Lihat log bot (real-time)
docker logs -f stock-bot

# Lihat log (last 50 lines)
docker logs --tail 50 stock-bot
```

================================================================================
BAGIAN 6: KELOLA BOT
================================================================================

6.1 CEK BOT RUNNING
--------------------
```bash
docker ps
```

Akan tampil:
```
CONTAINER ID   IMAGE       COMMAND          STATUS
abc123def456   stock-bot   "python run.py"  Up 2 minutes
```

6.2 CEK LOG/ERROR
-----------------
```bash
# Real-time log
docker logs -f stock-bot

# Log последние 100 lines
docker logs --tail 100 stock-bot

# Log sejak awal
docker logs stock-bot 2>&1
```

6.3 RESTART BOT
----------------
```bash
docker restart stock-bot
```

6.4 STOP BOT
------------
```bash
docker stop stock-bot
```

6.5 START BOT LAGI
-------------------
```bash
docker start stock-bot
```

6.6 HAPUS & BUAT BARU
----------------------
```bash
docker stop stock-bot
docker rm stock-bot
docker run -d --name stock-bot --restart always --env-file .env stock-bot
```

6.7 UPDATE BOT (JIKA PAKAI GIT)
--------------------------------
```bash
cd ~/bot

# Pull latest code
git pull

# Rebuild image
docker build -t stock-bot . --no-cache

# Restart container
docker stop stock-bot
docker rm stock-bot
docker run -d --name stock-bot --restart always --env-file .env stock-bot

# Cek log
docker logs -f stock-bot
```

================================================================================
BAGIAN 7: TROUBLESHOOTING
================================================================================

MASALAH 1: SSH CONNECTION REFUSED
----------------------------------
Cause: Firewall Oracle belum buka port 22

Solusi:
1. Oracle Cloud Console → Networking → Virtual Cloud Networks
2. Pilih VCN → Security Lists
3. Add Ingress Rule:
   - Source: 0.0.0.0/0
   - Port: 22
   - Protocol: TCP

MASALAH 2: DOCKER COMMAND NEEDS SUDO
-------------------------------------
Cause: User belum di grup docker

Solusi:
```bash
sudo usermod -aG docker $USER
# Logout & login again
# Atau:
newgrp docker
```

MASALAH 3: BOT NOT RESPONDING
-------------------------------
Cek log:
```bash
docker logs stock-bot
```

Common errors:
- "No such file": requirements.txt tidak ada → buat dengan pip freeze
- "Token invalid": TELEGRAM_BOT_TOKEN salah di .env
- "Connection error": Cek koneksi internet VM

MASALAH 4: OUT OF MEMORY
-------------------------
VM Oracle free tier cuma 1 GB RAM

Solusi:
1. Kurangi stocks yang di-scan
2. Naikkan sleep time di code
3. Jangan跑banyak command gleichzeitig

MASALAH 5: PERMISSION DENIED FILE
---------------------------------
Cause: File .env tidak bisa di-read container

Solusi:
```bash
chmod 600 .env
```

MASALAH 6: PORT ALREADY IN USE
-------------------------------
Cause: Port sudah dipake service lain

Solusi:
```bash
# Cek port 80/443
sudo netstat -tlnp | grep :80

# Atau cek Docker lain
docker ps
```

================================================================================
BAGIAN 8: UPDATE BOT (RINGKASAN)
================================================================================

1. SSH ke VM:
```bash
ssh -i "D:\Keys\oracle_key" opc@IP_VM
```

2. Ke folder bot:
```bash
cd ~/bot
```

3. Update code:
```bash
git pull
```

4. Rebuild & restart:
```bash
docker build -t stock-bot . --no-cache
docker stop stock-bot
docker rm stock-bot
docker run -d --name stock-bot --restart always --env-file .env stock-bot
```

5. Cek status:
```bash
docker logs -f stock-bot
```

================================================================================
BAGIAN 9: MONITORING & ALERTING
================================================================================

9.1 CEK RESOURCE USAGE
-----------------------
```bash
# CPU & RAM
htop

# Disk usage
df -h

# Docker stats
docker stats
```

9.2 AUTO RESTART JIKA CRASH
----------------------------
Sudah di-set dengan --restart always

Untuk cek:
```bash
docker ps -a
# Lihat STATUS = Up
```

9.3 LOG ROTATION (PREVENT DISK FULL)
-------------------------------------
Edit docker daemon.json:
```bash
sudo nano /etc/docker/daemon.json
```

Isi:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

Restart docker:
```bash
sudo systemctl restart docker
```

================================================================================
PENUTUP
================================================================================

Bot sekarang berjalan 24/7 di Oracle Cloud Free Tier!

Yang perlu diingat:
- PC kamu boleh MATI - bot tetap jalan
- Oracle Cloud SELALU ON (data center)
- Gratis FOREVER untuk spec ini

Untuk bantuan tambahan:
- Docker docs: https://docs.docker.com/
- Oracle docs: https://docs.oracle.com/

================================================================================
