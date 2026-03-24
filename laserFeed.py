import os
import cv2
import numpy as np
import time
import pytesseract
import logging
import redis
import datetime
import telegram
import paho.mqtt.client as mqtt
import urllib.request
import sys
import fcntl
import psutil
from dotenv import load_dotenv
from lib.checkers import AnalizaFolder

# 1. Load env vars
load_dotenv()
my_token = os.getenv("BOT_TOKEN")
chat_id  = os.getenv("CHAT_ID")

# 2. Redis + Telegram + MQTT
r      = redis.Redis()
bot    = telegram.Bot(my_token)
client = mqtt.Client()
client.username_pw_set(username=os.getenv("MQTT_USER"),
                       password=os.getenv("MQTT_PASS"))
client.connect("192.168.2.1", 1883, 60)

# 3. Logging
logging.basicConfig(
    level=logging.INFO,
    filename='/var/log/oee.log',
    format='%(asctime)s - %(filename)s - %(message)s'
)

# 4. Data classes
class JobFile:
    def __init__(self):
        self.denumire    = ""
        self.validata    = False
        self.repetitie   = 0
        self.marked_path = None

class Dreptunghi:
    def __init__(self, x, y, w, h):
        self.PunctX   = x
        self.PunctY   = y
        self.lungime  = w
        self.inaltime = h

# 5. Zone OCR
NumeProgramPunct = Dreptunghi(648, 272, 440, 40)
Repetie           = Dreptunghi(245, 365, 70, 40)
SingleJob         = Dreptunghi(800, 160, 300, 50)

# 6. Lockfile
lockfile_path = "/tmp/laser_monitor2.lock"
_lockfile     = None

def este_blocat(path):
    for proc in psutil.process_iter(['pid', 'open_files']):
        try:
            for f in proc.info['open_files'] or []:
                if f.path == path and proc.pid != os.getpid():
                    return True
        except:
            continue
    return False

def cleanup_lock():
    global _lockfile
    if _lockfile:
        try:
            fcntl.flock(_lockfile, fcntl.LOCK_UN)
            _lockfile.close()
        except:
            pass
    if os.path.exists(lockfile_path):
        try:
            os.remove(lockfile_path)
        except:
            pass

import atexit
atexit.register(cleanup_lock)

def obtine_lock(path):
    global _lockfile
    if este_blocat(path):
        sys.exit(0)
    try:
        _lockfile = open(path, 'w')
        fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lockfile.write(str(os.getpid()))
        _lockfile.flush()
        return _lockfile
    except:
        sys.exit(0)

# 7. CaptureFrontCamera
def CaptureFrontCamera():
    buffer_bytes = bytes()
    url = 'http://100.71.237.128:8081'
    url = 'http://laserbvision-1:8081'
    try:
        stream = urllib.request.urlopen(url, timeout=2)
        # dacă revenim din DOWN, trimitem BACK UP
        prev = r.get("LaserStatus") or b"DOWN"
        if prev.decode()=="DOWN":
            start = float(r.get("DowntimeStart") or 0)
            mid   = r.get("DowntimeMessageID")
            if mid:
                total = int(time.time() - start)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(mid.decode()),
                    text=f"🔴 Laser DOWN - Downtime: {total}s"
                )
                bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Laser BACK UP (downtime {total}s)"
                )
            r.delete("DowntimeStart")
            r.delete("DowntimeMessageID")
        r.set("LaserStatus","UP")
        client.publish("Laser/3020/Status","True",retain=True)
    except Exception:
        now = time.time()
        prev = r.get("LaserStatus") or b"UP"
        if prev.decode()=="UP":
            msg = bot.send_message(
                chat_id=chat_id,
                text="🔴 Laser DOWN - Downtime: 0s"
            )
            r.set("DowntimeStart", now)
            r.set("DowntimeMessageID", msg.message_id)
        else:
            start = float(r.get("DowntimeStart") or now)
            mid   = r.get("DowntimeMessageID")
            if mid:
                elapsed = int(now - start)
                try:
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=int(mid.decode()),
                        text=f"🔴 Laser DOWN - Downtime: {elapsed}s"
                    )
                except:
                    pass
        r.set("LaserStatus","DOWN")
        client.publish("Laser/3020/Status","False",retain=True)
        return False

    # captură continuă JPEG
    try:
        while True:
            buffer_bytes += stream.read(1024)
            a = buffer_bytes.find(b'\xff\xd8')
            b = buffer_bytes.find(b'\xff\xd9')
            if a!=-1 and b!=-1:
                jpg = buffer_bytes[a:b+2]
                buffer_bytes = buffer_bytes[b+2:]
                img = cv2.imdecode(
                    np.frombuffer(jpg,dtype=np.uint8),
                    cv2.IMREAD_COLOR
                )
                cv2.imwrite('/tmp/capture.jpg', img)
                return '/tmp/capture.jpg'
    except:
        return False

# 8. IdentificareProgram
def IdentificareProgram(zone=NumeProgramPunct):
    orig = cv2.imread('/tmp/capture.jpg')
    marked = orig.copy()
    # desenează
    cv2.rectangle(marked,
        (zone.PunctX, zone.PunctY),
        (zone.PunctX+zone.lungime, zone.PunctY+zone.inaltime),
        (0,255,255),2
    )
    cv2.rectangle(marked,
        (Repetie.PunctX, Repetie.PunctY),
        (Repetie.PunctX+Repetie.lungime, Repetie.PunctY+Repetie.inaltime),
        (255,100,0),2
    )
    # salvează
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    marked_path = f"/tmp/laser_marked_{ts}.jpg"
    cv2.imwrite(marked_path, marked)
    # OCR nume
    crop = orig[
        zone.PunctY:zone.PunctY+zone.inaltime,
        zone.PunctX:zone.PunctX+zone.lungime
    ]
    h,w = crop.shape[:2]
    crop = cv2.resize(crop,(w*2,h*2),interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    den = pytesseract.image_to_string(
        gray,
        config='-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_ --psm 7 --oem 3'
    ).strip().replace(" ","_").replace("__","_")
    if den.startswith("8"):
        den = "3"+den[1:]
    # OCR repetitie
    crop2 = orig[
        Repetie.PunctY:Repetie.PunctY+Repetie.inaltime,
        Repetie.PunctX:Repetie.PunctX+Repetie.lungime
    ]
    txt = pytesseract.image_to_string(
        crop2,
        config='-c tessedit_char_whitelist=0123456789 --psm 13 --oem 3'
    )
    try: rep = int(txt)
    except: rep = -1
    return den, rep, marked_path

# 9. procesare_principala
def procesare_principala():
    job = JobFile()
    path = CaptureFrontCamera()
    if not path:
        return job, False, "Camera inaccesibilă"
    job.denumire, job.repetitie, job.marked_path = IdentificareProgram()
    rez, err = AnalizaFolder(job.denumire, bot, chat_id)
    job.validata = rez
    return job, rez, err

# 10. Main
if __name__=="__main__":
    lock = obtine_lock(lockfile_path)
    logging.info("Start laserFeed")
    job = JobFile(); rez=False; err=""
    try:
        for d in ["/tmp/laser_succes","/tmp/laser_failures","/tmp/laser_ignorat"]:
            os.makedirs(d, exist_ok=True)
        job, rez, err = procesare_principala()
    except Exception as e:
        logging.error(f"Eroare procesare: {e}",exc_info=True)
        bot.send_message(chat_id=chat_id, text=f"[EROARE Laserfeed] {e}")
        err = str(e)
    finally:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
            os.remove(lockfile_path)
        client.disconnect(); r.close()

    # --- State Handling (OK or ERR) ---
    #  - dacă camera inaccesibilă, ieșim
    if not rez and err=="Camera inaccesibilă":
        logging.error("Camera NOK")
        sys.exit(0)

    # construim starea și snapshot-ul
    if rez:
        state_str = f"OK – Job: {job.denumire} (rep: {job.repetitie})"
        snapshot  = None
    else:
        state_str = f"ERR – {job.denumire}  Detaliu: {err}"
        snapshot  = job.marked_path

    # chei Redis
    S_KEY    = "LaserState"
    S_START  = "LaserStateStart"
    S_MSGID  = "LaserStateMsgID"

    prev  = r.get(S_KEY)
    pst   = r.get(S_START)
    pmsg  = r.get(S_MSGID)
    now   = time.time()

    # template mesaj
    tmpl = "{state}\nDurata: {elapsed}s".format

    # stare nouă?
    if prev is None or prev.decode()!=state_str or pmsg is None:
        m = bot.send_message(chat_id=chat_id,
                             text=tmpl(state=state_str, elapsed=0))
        if snapshot and os.path.exists(snapshot):
            with open(snapshot,"rb") as ph:
                bot.send_photo(chat_id=chat_id, photo=ph,
                               caption="Snapshot pentru stare nouă")
        r.set(S_KEY, state_str)
        r.set(S_START, now)
        r.set(S_MSGID, m.message_id)
    else:
        elapsed = int(now - float(pst.decode()))
        try:
            bot.edit_message_text(chat_id=chat_id,
                message_id=int(pmsg.decode()),
                text=tmpl(state=state_str, elapsed=elapsed))
        except Exception:
            pass
    logging.info("laserFeed done")
