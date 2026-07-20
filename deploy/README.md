# Deploy Ochobot ke Oracle Cloud Free Tier

Panduan lengkap dari nol untuk pemula — deploy bot Telegram 24/7 di Oracle Cloud Always Free (Ubuntu ARM).

> **Pendekatan: systemd** (bukan Docker). Lebih simpel untuk pemula, dan repo sudah menyediakan file service-nya.

---

## Daftar Isi

1. [Daftar Oracle Cloud Free Tier](#1-daftar-oracle-cloud-free-tier)
2. [Generate SSH Key Pair di Windows](#2-generate-ssh-key-pair-di-windows)
3. [Bikin VM Always Free ARM](#3-bikin-vm-always-free-arm)
4. [Buka Port 22 di Firewall Oracle](#4-buka-port-22-di-firewall-oracle)
5. [SSH ke VM Pertama Kali](#5-ssh-ke-vm-pertama-kali)
6. [Setup Awal VM](#6-setup-awal-vm)
7. [Clone Repo & Install Dependencies](#7-clone-repo--install-dependencies)
8. [Buat File `.env` di VM](#8-buat-file-env-di-vm)
9. [Test Run Manual](#9-test-run-manual)
10. [Setup systemd Service](#10-setup-systemd-service-auto-start)
11. [Setup UFW Firewall](#11-setup-ufw-firewall-security)
12. [Verifikasi Akhir](#12-verifikasi-akhir)
13. [Perintah Sehari-hari](#13-perintah-sehari-hari)
14. [Troubleshooting](#14-troubleshooting)

---

## Prasyarat

- Akun Oracle Cloud (gratis, perlu kartu kredit untuk verifikasi — **tidak di-charge**)
- Repo GitHub: `https://github.com/haqqii/Trading-Bot.git`
- Windows 10/11 (untuk SSH dari lokal)
- Token Telegram Bot dari @BotFather

---

## 1. Daftar Oracle Cloud Free Tier

1. Buka https://www.oracle.com/cloud/free/
2. Klik **"Start for Free"**
3. Isi email, nama (sesuai KTP), password, negara: **Indonesia**
4. **Verifikasi email** — cek inbox, klik link dari Oracle
5. Login pertama kali → Oracle minta **kartu kredit** untuk verifikasi
   - Tidak di-charge (Always Free tier beneran gratis selamanya)
   - Kartu debit Mastercard/Visa apa aja bisa
6. Pilih **Home Region** → pilih **Jakarta** atau **Singapore** (pilih yang paling deket / available)

> Catatan: Kadang setelah isi kartu kredit, Oracle butuh 5–15 menit untuk approve akun.

---

## 2. Generate SSH Key Pair di Windows

Windows 10/11 sudah punya OpenSSH built-in. Buka **PowerShell**:

```powershell
ssh-keygen -t ed25519 -C "bot-saham" -f $HOME\.ssh\bot_saham_key
```

Prompt:
- **Enter passphrase** → kosongin aja, tekan Enter 2x (biar otomatis gak ngetik tiap SSH)

Key tersimpan di:
- **Private**: `C:\Users\<user>\.ssh\bot_saham_key` ⚠️ **JANGAN DI-SHARE**
- **Public**: `C:\Users\<user>\.ssh\bot_saham_key.pub`

Cek isi public key:

```powershell
Get-Content $HOME\.ssh\bot_saham_key.pub
```

Output ini (dimulai dengan `ssh-ed25519 ...`) yang akan di-paste ke Oracle.

---

## 3. Bikin VM Always Free ARM

1. Dashboard Oracle Cloud → **Compute** → **Instances** → **Create Instance**
2. Isi form:

   | Setting | Value |
   |---|---|
   | Name | `bot-saham-vm` |
   | Image | Canonical **Ubuntu 22.04** (atau 24.04) |
   | Shape | `VM.Standard.A1.Flex` |
   | OCPU | 4 (max Always Free) |
   | RAM | 24 GB (max Always Free) |
   | Boot Volume | 50 GB (default) |

3. **Networking** → centang **"Assign a public IPv4 address"**
4. **SSH Keys** → pilih **"Paste public key"** → paste isi `bot_saham_key.pub`
5. Klik **Create**

> ⚠️ **Masalah umum: "Out of host capacity"**
> ARM VM Oracle **sering penuh**. Solusi:
> - Tunggu 5–10 menit, coba lagi
> - Atau ganti region (Singapore ↔ Mumbai ↔ Tokyo)
> - Atau turunkan ke 2 OCPU / 12 GB (lebih mudah dapet slot)
>
> Bisa butuh beberapa kali retry. Sabar ya ☕.

6. Tunggu ~2 menit sampai instance **status: RUNNING**
7. **Copy Public IP** (misal `132.145.67.89`) — catat!

---

## 4. Buka Port 22 di Firewall Oracle

1. Halaman instance → klik **"Subnets"** (di bagian Resources bawah)
2. Klik nama subnet (misal `subnet-...`)
3. Klik **"Default Security List"**
4. Klik **"Add Ingress Rules"**:

   | Field | Value |
   |---|---|
   | Source CIDR | `0.0.0.0/0` |
   | Protocol | TCP |
   | Destination Port | `22` |
   | Description | SSH |

5. **Save**

Tanpa step ini, SSH dari luar akan ditolak.

---

## 5. SSH ke VM Pertama Kali

Buka PowerShell baru:

```powershell
ssh -i $HOME\.ssh\bot_saham_key ubuntu@<PUBLIC_IP>
```

Ganti `<PUBLIC_IP>` dengan IP dari Step 3. Contoh:

```powershell
ssh -i $HOME\.ssh\bot_saham_key ubuntu@132.145.67.89
```

Pertama kali muncul:

```
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Ketik `yes` lalu Enter.

Kalau berhasil, prompt jadi:

```
ubuntu@bot-saham-vm:~$
```

🎉 Kamu sudah di dalam VM Ubuntu.

---

## 6. Setup Awal VM

Jalankan **satu per satu**:

```bash
sudo apt update && sudo apt upgrade -y
```

```bash
sudo apt install -y python3.11 python3.11-venv python3-pip git
```

```bash
sudo timedatectl set-timezone Asia/Jakarta
```

```bash
date
```

Output harusnya: `Sunday, July 12, 2026 PM07:30:00 WIB`

Verifikasi Python:

```bash
python3 --version
```

Harusnya: `Python 3.11.x`

---

## 7. Clone Repo & Install Dependencies

```bash
mkdir -p ~/bot-saham && cd ~/bot-saham
```

```bash
git clone https://github.com/haqqii/Trading-Bot.git .
```

```bash
python3 -m venv venv
```

```bash
source venv/bin/activate
```

Prompt jadi `(venv) ubuntu@...$` — tanda venv aktif.

```bash
pip install --upgrade pip
```

```bash
pip install -r requirements.txt
```

Tunggu ~1-2 menit.

---

## 8. Buat File `.env` di VM

⚠️ **PENTING**: Token di `.env` lokal TIDAK BOLEH di-copy dengan cara yang bocor (misal paste ke GitHub Gist). Kita bikin file langsung di VM.

```bash
nano .env
```

Isi dengan token kamu (ganti `xxx` dengan token asli dari @BotFather):

```
TELEGRAM_BOT_TOKEN=xxx:xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FINNHUB_API_KEY=
NEWS_API_KEY=
```

Simpan: **Ctrl+X** → **Y** → **Enter**

Verifikasi:

```bash
cat .env
```

---

## 9. Test Run Manual

```bash
source venv/bin/activate   # aktifkan venv (kalau belum)
```

```bash
python main.py
```

Kalau jalan, akan muncul log telegram-bot (misal "Bot started").

**Test di HP:** Buka Telegram → cari bot kamu → kirim `/start`

Kalau bot bales → ✅ jalan!

Stop dengan **Ctrl+C**.

---

## 10. Setup systemd Service (Auto Start)

Supaya bot auto-start pas VM reboot.

```bash
sudo cp ~/bot-saham/deploy/bot-saham.service /etc/systemd/system/
```

```bash
sudo chmod 644 /etc/systemd/system/bot-saham.service
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable bot-saham
```

```bash
sudo systemctl start bot-saham
```

Cek status:

```bash
sudo systemctl status bot-saham
```

Harusnya muncul **"active (running)"** warna hijau.

Cek log real-time:

```bash
sudo journalctl -u bot-saham -f
```

(Ctrl+C untuk keluar)

---

## 11. Setup UFW Firewall (Security)

Supaya VM aman, cuma port 22 yang terbuka.

```bash
sudo ufw allow 22/tcp
```

```bash
sudo ufw --force enable
```

```bash
sudo ufw status
```

Output:

```
Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
```

---

## 12. Verifikasi Akhir

1. ✅ **Telegram**: kirim `/start` ke bot → harusnya bales
2. ✅ **SSH**: keluar VM (`exit`), lalu SSH balik → harusnya bisa
3. ✅ **Reboot test** (opsional, recommended):
   ```bash
   sudo reboot
   ```
   Tunggu 1-2 menit, SSH balik, cek:
   ```bash
   sudo systemctl status bot-saham
   ```
   Status harusnya tetap **active (running)**.

🎉 **Bot sekarang jalan 24/7!**

---

## 13. Perintah Sehari-hari

| Mau... | Command |
|---|---|
| Cek status bot | `sudo systemctl status bot-saham` |
| Lihat log real-time | `sudo journalctl -u bot-saham -f` |
| Lihat 50 log terakhir | `sudo journalctl -u bot-saham -n 50` |
| Restart bot | `sudo systemctl restart bot-saham` |
| Stop bot | `sudo systemctl stop bot-saham` |
| Update kode | `cd ~/bot-saham && git pull && source venv/bin/activate && pip install -r requirements.txt && sudo systemctl restart bot-saham` |

---

## 14. Troubleshooting

### Bot gak start setelah reboot

```bash
sudo systemctl is-enabled bot-saham   # harus "enabled"
sudo journalctl -u bot-saham -n 50    # cek error
```

Penyebab umum:
- `.env` hilang atau typo → cek `cat ~/bot-saham/.env`
- Dependencies belum install di venv → `source venv/bin/activate && pip install -r requirements.txt`
- Working directory salah di service file → cek `WorkingDirectory=/home/ubuntu/bot-saham` (default user-nya `ubuntu`, bukan `opc`)

### Locked out SSH (firewall salah)

Login via Oracle Cloud Console → **Compute** → pilih instance → **Console Connection** → **Connect** (paling bawah).

### Bot crash loop

```bash
sudo journalctl -u bot-saham --since "1 hour ago" | grep -i error
```

Lihat error paling bawah.

### Out of Memory

VM Oracle free tier ada batas RAM. Cek:

```bash
free -h
```

Kalau mentok:
- Kurangi jumlah saham/crypto yang di-scan
- Naikkan sleep time antar request di kode
- Naikkan OCPU/RAM di Step 3 (kalau masih di bawah limit Always Free)

### SSH key permission denied (Windows)

```powershell
# Windows kadang strict soal permission
icacls $HOME\.ssh\bot_saham_key /inheritance:r
icacls $HOME\.ssh\bot_saham_key /grant:r "$($env:USERNAME):(R)"
```

### "Out of capacity" terus saat bikin VM

- Ganti region
- Turunkan OCPU/RAM
- Tunggu 15-30 menit lalu coba lagi
- Cek [Reddit r/oraclecloud](https://www.reddit.com/r/oraclecloud/) untuk tips terbaru

---

## Biaya

| Item | Biaya |
|---|---|
| Oracle Cloud Always Free ARM VM | **GRATIS** |
| Total | **GRATIS** |

> Selalu pakai resource **Always Free**. Jangan add paid services.

---

## Catatan

- PC lokal boleh MATI — bot tetap jalan
- Oracle Cloud Always Free SELALU ON (data center tier)
- Backup state bot (`user_data.json`, `last_signals.json`) di-share antara lokal & VM (otomatis update via `git push`/`pull`, tapi INGAT: file-file ini di-`.gitignore` jadi gak ke-track — backup manual kalau perlu)