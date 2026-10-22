"""
Penanda bot untuk log.

Dua bot jalan di container yang sama (bot trading Telegram dan bot Zulip
Aimapps) dan keduanya menulis ke stdout container, jadi lognya bercampur.
Supaya jelas baris itu ditulis bot mana, nama logger (field "Object" di
format log) diberi prefix:

    trading.handlers.scheduler.jobs_stocks   -> bot trading
    aimapps.bot                              -> bot Zulip Aimapps

Prefix dipasang lewat log record factory, bukan dengan mengubah format:
dengan begitu semua handler (file, console, alert Telegram) ikut melihat
nama yang sama, dan tidak ada kode yang perlu tahu soal prefix ini.

Nama logger asli tetap utuh -- yang diubah hanya record.name, jadi
logging.getLogger('x') tidak berubah dan tidak ada yang bisa salah cari
logger.
"""
import logging


def install(tag: str) -> None:
    """Pasang prefix `tag.` pada nama logger di semua log record.

    Aman dipanggil berulang: prefix yang sudah ada tidak ditambahkan lagi.
    """
    if not tag:
        return
    prefix = f"{tag}."
    previous_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        if not record.name.startswith(prefix):
            record.name = prefix + record.name
        return record

    logging.setLogRecordFactory(factory)
