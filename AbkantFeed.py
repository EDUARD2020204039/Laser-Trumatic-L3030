#TODO: trebuie setat o interogare la fiecare 10 secunde , care verifica daca numele s-a schimbat , eventual daca s-a inchis abkantul

import pickle
import json
import urllib.request
import cv2
import numpy as np    
import sys
import telegram
import warnings
warnings.simplefilter("ignore", DeprecationWarning)
import imutils
import time
import pytesseract
import logging
import os.path
from os import path
logging.basicConfig( level=logging.INFO, filename='/var/log/oee.log',format='%(asctime)s - %(filename)s - %(message)s')

import psycopg2
from termcolor import colored

conn = psycopg2.connect(
   host="192.168.2.130",
   database="oee_helpan",
   user="postgres",
   password="postgres")

from Server import Server
BDICT = {}
try:
    from PIL import Image
except ImportError:
    import Image

status_file_path = 'status.txt'

DEBUG = True
test= False
pozaRapida=True
import paho.mqtt.client as mqtt
class Punct:
    def __init__(self):
        self.PunctX=0
        self.PunctX=0
    def __init__(self,PunctX,PunctY,lungime,inaltime):
        self.PunctX=PunctX
        self.PunctY=PunctY
        self.PunctYFinal=PunctY+inaltime
        self.PunctXFinal=PunctX+lungime
        self.lungime=lungime
        self.inaltime=inaltime

class Dreptunghi(Punct):
    def  __init__(self,PunctX,PunctY,lungime,inaltime):
        super().__init__(PunctX,PunctY,lungime,inaltime)
        self.lungime=lungime
        self.inaltime=inaltime

#23,220

PunctX=23 
PunctY=220

lungime=300
latime=50
angle=360
NumeProgramPunct=Punct(23,220,300,inaltime=50)
NrBucPunct=Punct(340,210,200,inaltime=70)
ActualPunct=Punct(660, 270, 200, inaltime=50)
UpperToolPunct=Punct(150,55,430,inaltime=55)
LowerToolPunct=Punct(150,105,430,inaltime=55)
#print(NumeProgramPunct.PunctX)
my_token="1260858483:AAFmQBXz1Fsg_JqESNmIv9OtcmozFQ7WUbg"
my_chat_id="-1001284842892"
# Connect to our bot
bot = telegram.Bot(my_token)
chat_id=my_chat_id
client = mqtt.Client()
client.username_pw_set(username="bogdan",password="HELPAN123$")
try:
    client.connect("192.168.2.1", 1883, 60)
except:
     logging.info("Nu merge MQTT")

class DateIdentificate:
    DenumireProgram: str
    EsteActivat: bool

    def __init__(self):
        self.DenumireProgram=""
        self.NrBuc=""
        self.NrBucProdus=0
        self.NrBucTotal=0
        self.UpperTool=""
        self.LowerTool=""
        self.Activ=""
        self.EsteActivat=False
        self.EsteSchimbat=False
    def analizaNrBuc(self):
        if "/" in self.NrBuc: #8/0
            parts = self.NrBuc.split("/")
            try:
                # Handle case where first part is empty (like "/210")
                self.NrBucProdus = int(parts[0]) if parts[0] else 0
            except Exception as ex:
                self.NrBucProdus = 0
                print(f"Eroare la conversie {ex} -- {self.NrBuc}")

            # Make sure we don't get IndexError if there's no second part
            self.NrBucTotal = parts[1] if len(parts) > 1 else "0"
            
    def ComparaCuUltimaInregistrare(self,denumireVeche: str,nrbucvechi: str, upperVeche: str="", lowerVeche: str=""):
        if  self.DenumireProgram!=denumireVeche or self.NrBuc!=nrbucvechi or self.UpperTool!=upperVeche or self.LowerTool!=lowerVeche:
            logging.info("Program schimbat!")
            print("Program neschimbat" )
            self.EsteSchimbat=True
        else:
            logging.info("Program NEschimbat ###")
            print("Program Neschimbat" )
            

def CaptureFrontCamera():
    _bytes = bytes()
    try:
        stream = urllib.request.urlopen('http://100.126.29.52:8081') #192.168.5.130
    except:
        return False
    while True:
        _bytes += stream.read(1024)
        a = _bytes.find(b'\xff\xd8')
        b = _bytes.find(b'\xff\xd9')
        if a != -1 and b != -1:
            jpg = _bytes[a:b+2]
            _bytes = _bytes[b+2:]
            filename = 'captureAbkant.jpg'
            i = cv2.imdecode(np.fromstring(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            cv2.imwrite(filename, i)
            return filename
    return False

def is_blank_screenshot(image_path):
    # Load the image
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    
    # Calculate the average pixel value
    average_pixel_value = cv2.mean(img)[0]
    
    # Define a threshold value to determine if it's a blank screen
    threshold = 8
    
    # Check if the average pixel value is below the threshold
    if average_pixel_value < threshold:
        return True  # Blank screen
    else:
        return False  # Not a blank screen


def normalize_tool_name(raw_text):
    cleaned = raw_text.strip().upper().replace("\n", " ").replace("\r", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace("|", "I")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in {" ", "/", "-", "_"})
    return cleaned


def read_tool_name(image, area, filename):
    cropped = image[area.PunctY:area.PunctYFinal, area.PunctX:area.PunctXFinal]
    cv2.imwrite(filename, cropped)
    tool_name = pytesseract.image_to_string(
        cropped,
        config='--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-_ '
    )
    tool_name = normalize_tool_name(tool_name)
    logging.info("Tool OCR %s -> %s", filename, tool_name)
    return tool_name


def ensure_abkant_report_columns(server):
    for sql in (
        "ALTER TABLE raportare_abkant ADD COLUMN IF NOT EXISTS upper_tool TEXT",
        "ALTER TABLE raportare_abkant ADD COLUMN IF NOT EXISTS lower_tool TEXT",
    ):
        try:
            server.RunStatement(sql)
        except Exception as ex:
            logging.info("Nu am putut extinde raportare_abkant cu SQL %s din cauza %s", sql, ex)


def IdentificareProgram(NumeVechi) :
    global lastBDID,DenumireIdentificata
    fisier='captureAbkant.jpg'
    is_blank = is_blank_screenshot(fisier)
    if is_blank:
        print("The screenshot is blank. Return Null")
    else:
        print("The screenshot is not blank.")
    original = cv2.imread (fisier)
    W = 1400
    height, width, depth = original.shape
    imgScale = W / width
    if DEBUG :
        print ("Dimensiunile originale: {}x{}".format (width, height))
    newX, newY = original.shape[1] * imgScale, original.shape[0] * imgScale
    newimg = cv2.resize (original, (int (newX), int (newY)))
    orig = newimg
    img = cv2.cvtColor (orig, cv2.COLOR_BGR2GRAY)
    height, width = img.shape
    if DEBUG :
        print ("Dimensiunile reformate: {}x{}".format (width, height))
    size = img.size
    cropped = orig[PunctY :PunctY + latime, PunctX :PunctX + lungime]
    ImagineCroppedActual= orig[ActualPunct.PunctY:ActualPunct.PunctYFinal, ActualPunct.PunctX:ActualPunct.PunctXFinal]
    allowed_char = "ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    # allowed_char = "|-"
    # if DEBUG:
    #    print(pytesseract.image_to_string (cropped, config='--psm 0 --oem 3'))

###  Nr Buc
    ActualIdentificat = pytesseract.image_to_string(ImagineCroppedActual, config='--psm 10 --oem 3')
    try :
        x = ActualIdentificat.strip ().replace (" ", "_").replace (".", "_")
        if len (x) > 1 :
            if (len (x[0] < 3)) :
                ActualIdentificat = x[1]
            else :
                ActualIdentificat = x[0]
        ActualIdentificat = x
    except :
        logging.info ("Not working split")
        ActualIdentificat = ActualIdentificat.strip().replace (" ", "_")

    logging.info ('Trying to split file %s', ActualIdentificat)
    print (f'Actual identificat {ActualIdentificat}')
    logging.info ("Running: %s", ActualIdentificat)

    cv2.imwrite ("/tmp/save_me3.jpg", ImagineCroppedActual)

    if ('ctua' not in ActualIdentificat.lower()):
        print("Nu e meniu de lucru. Anulam")
        logging.info(f'Nu e in meniul de lucru. Nu vad ctua in campul dorit')
        # bot.sendPhoto(chat_id=chat_id, caption="Denumire Identificata " + str(DenumireIdentificata),
        #               photo=open('/tmp/save_me2.jpg', 'rb'))
        # bot.sendPhoto(chat_id=chat_id, caption="Nr buc: " + str(NrBucIdentificat),
        #               photo=open('/tmp/save_me3.jpg', 'rb'))
        # bot.sendPhoto(chat_id=chat_id, caption="abkant", photo=open('/tmp/aici-snapshots_marcat2.jpg', 'rb'))
        exit(10)
    DateIdentificare=DateIdentificate()
    DateIdentificare.EsteActivat=True
    DateIdentificare.UpperTool = read_tool_name(orig, UpperToolPunct, "/tmp/save_me_upper.jpg")
    DateIdentificare.LowerTool = read_tool_name(orig, LowerToolPunct, "/tmp/save_me_lower.jpg")
### Analiza NrBuc
    start_time = time.time()

    DenumireIdentificata = pytesseract.image_to_string(cropped, config='--psm 10 --oem 3')
    end_time = time.time()

    execution_time = end_time - start_time
    print(f"Execution time of image_to_string: {execution_time} seconds")

    try:
        x = DenumireIdentificata.strip().replace(" ", "_").replace(".", "_")
        if len(x) > 1:
            if (len(x[0] < 3)):
                DenumireIdentificata = x[1]
            else:
                DenumireIdentificata = x[0]
        DenumireIdentificata = x
    except:
        logging.info("Not working split")
        DenumireIdentificata = DenumireIdentificata.strip().replace(" ", "_")
    logging.info('Trying to split file %s', DenumireIdentificata)
    print(f'Denumire identificata {DenumireIdentificata}')
    logging.info("Running: %s", DenumireIdentificata)
    cv2.imwrite("/tmp/save_me2.jpg", cropped)
    DateIdentificare.DenumireProgram=DenumireIdentificata
    ## END of Program

    ImagineCroppedNrBuc = orig[NrBucPunct.PunctY:NrBucPunct.PunctYFinal, NrBucPunct.PunctX:NrBucPunct.PunctXFinal]
    tryRecognise=1


    while tryRecognise>=0:
        boxes = pytesseract.image_to_boxes(ImagineCroppedNrBuc, lang="deu",config='--psm 13 --oem 3 -c tessedit_char_whitelist=0123456789/ ')
        print(f'Date: {boxes}')

        NrBucIdentificat = pytesseract.image_to_string(ImagineCroppedNrBuc, config='--psm 10 --oem 3 -c tessedit_char_whitelist=0123456789/ ')
        print(f'String: {NrBucIdentificat}')
        #Page Segmentation Modes (--psm) and OCR Engine Modes (--oem)
        try:
            #x = NrBucIdentificat.strip().replace(" ", "_").replace(".", "_")
            if ('/' not in NrBucIdentificat):
                print(f'Nr Buc neidentificat corect {NrBucIdentificat}. Il punem NA')
                NrBucIdentificat="NA"
                tryRecognise-=1
            else:
                print(NrBucIdentificat)
                tryRecognise=-1
        except:
            logging.info("Not working split")
            NrBucIdentificat = "NA"

    logging.info('Trying to split file %s', NrBucIdentificat)
    print(f'Nr Buc identificat {NrBucIdentificat}')
    logging.info("Running: %s", NrBucIdentificat)

    cv2.imwrite("/tmp/save_me4.jpg", ImagineCroppedNrBuc)
    DateIdentificare.NrBuc=NrBucIdentificat
    DateIdentificare.analizaNrBuc()
        ####
    #exit(1)
    # img_marcat=cv2.circle(img,(PunctX,PunctY), 50, (0,0,255), 4)
    img_marcat = cv2.rectangle (img, (PunctX, PunctY), (PunctX + lungime, PunctY + latime), (255, 255, 0), 4)
    img_marcat = cv2.rectangle (img_marcat, (ActualPunct.PunctX, ActualPunct.PunctY), (ActualPunct.PunctXFinal, ActualPunct.PunctYFinal), (255, 255, 0), 4)
    img_marcat = cv2.rectangle(img_marcat, (NrBucPunct.PunctX, NrBucPunct.PunctY),
                               (NrBucPunct.PunctXFinal, NrBucPunct.PunctYFinal), (255, 255, 0), 4)
    img_marcat = cv2.rectangle(img_marcat, (UpperToolPunct.PunctX, UpperToolPunct.PunctY),
                               (UpperToolPunct.PunctXFinal, UpperToolPunct.PunctYFinal), (255, 255, 0), 4)
    img_marcat = cv2.rectangle(img_marcat, (LowerToolPunct.PunctX, LowerToolPunct.PunctY),
                               (LowerToolPunct.PunctXFinal, LowerToolPunct.PunctYFinal), (255, 255, 0), 4)
    cv2.imwrite ("/tmp/aici-snapshots_marcat2.jpg", img_marcat)
    if(NumeVechi!=DenumireIdentificata): #NumeVechi!=DenumireIdentificata
        print("Nu e aceeasi chestie")
        logging.info(f'Trimitem poza {NumeVechi} vs {DenumireIdentificata}')
        #bot.sendPhoto (chat_id=chat_id, caption="Denumire Identificata " + str (DenumireIdentificata), photo=open ('/tmp/save_me2.jpg', 'rb'))
        #bot.sendPhoto (chat_id=chat_id, caption="Actual: " + str (ActualIdentificat), photo=open ('/tmp/save_me3.jpg', 'rb'))
        #bot.sendPhoto(chat_id=chat_id, caption="NrBuc: " + str(NrBucIdentificat),
        #              photo=open('/tmp/save_me4.jpg', 'rb'))
        #TODO: pozele sa fie trimise doar daca le cerem
        bot.sendPhoto (chat_id=chat_id, caption=""+str (DenumireIdentificata)+" "+str(NrBucIdentificat), photo=open ('/tmp/aici-snapshots_marcat2.jpg', 'rb'))
    logging.info ("Setam MQTT Message Abkant/StareProgramIdentificat - True")
    client.publish ("Abkant/StareProgramIdentificat", "True", retain=True)
    logging.info ("Setam MQTT Message Abkant/ProgramActiv - %s", DenumireIdentificata)
    client.publish ("Abkant/ProgramActiv", DenumireIdentificata, retain=True)

    print("Scriem in BD..")
    PostgresOEE=Server()
    from datetime import datetime
    try:
        PostgresOEE.setConn(conn)
    except:
        logging.error("Se pare ca e serverul de BD neconectat. Ies")
        return
    ensure_abkant_report_columns(PostgresOEE)
    #TODO verifica daca e schimbat
    try:
        sql = f"SELECT * FROM raportare_abkant ORDER BY id DESC LIMIT 1"
        if (last_row:=PostgresOEE.RunOneStatement(sql)):
            print(f'Ultimul program: {last_row[2]} cu nrBuc {last_row[3]}')
            DateIdentificare.ComparaCuUltimaInregistrare(
                denumireVeche=last_row[2],
                nrbucvechi=last_row[3],
                upperVeche=last_row[6] if len(last_row) > 6 and last_row[6] else "",
                lowerVeche=last_row[7] if len(last_row) > 7 and last_row[7] else "",
            )
            #TODO: compara last_row[1]
            print("Am rulat comparatia")
        else:
            print("Eroare la rulare sql")

    except Exception as ex:
        print(f"eroare {ex}")

    # scriem
    try:
        sql = f"Insert into raportare_abkant (datacolectare,programidentificat,numar_bucati,faraschimbare,nr_bucati,upper_tool,lower_tool) values ('{datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S.%f')}','{DateIdentificare.DenumireProgram}','{DateIdentificare.NrBuc}',{DateIdentificare.EsteSchimbat},{DateIdentificare.NrBucProdus},'{DateIdentificare.UpperTool}','{DateIdentificare.LowerTool}')"
        # sa ma uit pe screen daca e Running # vezi /opt/oee/tests/OCR.py
        if (PostgresOEE.RunStatement(sql)):
            print("Am rulat")
        else:
            print("Eroare la rulare sql")
    except:
        logging.info("laser down")


    PostgresOEE.close()


def DacaTrimitNotificare():
    global sql
    print("Scriem in BD..")
    PostgresOEE = Server()
    from datetime import datetime
    try:
        PostgresOEE.setConn(conn)
    except:
        logging.error("Se pare ca e serverul de BD neconectat. Ies")
    try:
        sql = f"SELECT * FROM parameters WHERE lower(parametru) like 'rpiabkantworking' "
        if (last_row := PostgresOEE.RunOneStatement(sql)):

            print(f'Parametru: {last_row[1]} are notificare {last_row[3]} si status curent pe {last_row[2]}')
            # DateIdentificare.ComparaCuUltimaInregistrare(denumireVeche=last_row[2], nrbucvechi=last_row[3])
            # TODO: compara last_row[1]
            print("Am rulat comparatia")
            if last_row[2].upper()=='TRUE' and last_row[3]==True: #adica daca inainte era True si acum e false si vreau sa fiu notificat

                sql2 = f"UPDATE parameters set status=False where lower(parametru) like 'rpiabkantworking'"
                PostgresOEE.RunOneStatement(sql2) #returneaza no results to fetch
                PostgresOEE.close()
                return True
            else:
                PostgresOEE.close()
                #print(f'Nu trimit notificare')
                return False

        else:
            print("Eroare la rulare sql")

    except Exception as ex:
        print(f"eroare {ex}")


if __name__ == "__main__":

    if (path.exists("/opt/oee/DICT_ITEMS.txt")):
        with open('/opt/oee/DICT_ITEMS.txt', 'rb') as dict_items_open:
            BDICT = pickle.load(dict_items_open)
            #print(BDICT)
    print("Pornim analiza ecranului de la abkant")
    if (CaptureFrontCamera()==False):
        if(DacaTrimitNotificare()):
            print('Trimit notificare pe telegram')
            bot.sendMessage(chat_id=chat_id, text="Nu pot accesa RPI de pe abkant. Vezi reteaua http://192.168.5.130:8081")
        else:
            print('Nu Trimit notificare pe telegram')
        quit()
    else:
        PostgresOEE = Server()
        try:
            PostgresOEE.setConn(conn)
        except:
            logging.error("Se pare ca e serverul de BD neconectat. Ies")
        sql = f"UPDATE parameters set status=True where lower(parametru) like 'rpiabkantworking'"
        PostgresOEE.RunOneStatement(sql)  # returneaza no results to fetch

        IdentificareProgram(BDICT["ProgramIdentificat"])
#        print(f'Program din MQTT: {BDICT["ProgramIdentificat"]}')
        logging.info(f"Identificare {DenumireIdentificata}")
        if(BDICT["ProgramIdentificat"]!=DenumireIdentificata):
            logging.info("Salvam noua valoare")
            BDICT["ProgramIdentificat"]=DenumireIdentificata
        else:
            logging.info("Valoare veche")
    # Serialize data into file:
#    print(DenumireIdentificata)
#    json.dump( DenumireIdentificata, open( "file_name.json", 'w' ) )

    if (BDICT):
        logging.info("Salvam datele")
        with open('/opt/oee/DICT_ITEMS.txt', 'wb+') as dict_items_save:
            try:
                pickle.dump(BDICT, dict_items_save)
            except Exception as ex:
                logging.info(f"Eroare la scrierea pickle {ex}")
                print(f"Eroare la scriere {ex}")
    print("done analiza")
