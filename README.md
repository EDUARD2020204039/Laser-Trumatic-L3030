# Laser TruMatic L3030

Faza 1 pentru monitorizare laser:

- dashboard web modern, cu selectie dedicata pe `Laser1`, `Laser2`, `Abkant`
- stari manuale pentru `machine_on`, `cutting_active`, `table_change`
- butoane pentru stergerea rapida a testelor manuale
- operator curent luat din `PontajWorkCenter` / baza `Metal`
- `workcenter_id` configurabil direct din UI pentru fiecare utilaj
- persistenta locala in SQLite pentru evenimente, timpi pe zi si cicluri salvate
- Dockerfile pentru Unraid
- workflow GitHub Actions pentru publicare imagine in GHCR ca `ghcr.io/eduard2020204039/lasertrumaticl3030:latest`

## Ce este inclus acum

Aplicatia afiseaza:

- selector principal pentru `Laser1`, `Laser2`, `Abkant`
- statusul curent al utilajului selectat
- operatorul activ pentru `workcenter_id` configurat pe utilajul selectat
- istoricul ultimelor schimbari pe utilajul selectat
- timp total alimentat, timp de taiere, timp schimb masa, idle si randament estimat pentru ziua curenta

Pentru inceput, controalele sunt manuale. Mai tarziu, aceleasi endpoint-uri pot fi chemate de un PLC, Raspberry Pi sau modul IO industrial.

## Pornire locala pe Windows

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Aplicatia porneste implicit pe `http://localhost:3030`.

## Variabile importante

- `PONTAJ_LASER1_WORKCENTER_ID=1` pentru workcenterul implicit al lui `Laser1`
- `PONTAJ_LASER2_WORKCENTER_ID=` pentru al doilea laser
- `PONTAJ_ABKANT_WORKCENTER_ID=` pentru utilajul `Abkant`
- `PONTAJ_WORKCENTER_ID=1` ramine fallback pentru instalari mai vechi
- `PONTAJ_SQL_DRIVER`
  - pe Windows merge de obicei `ODBC Driver 17 for SQL Server`
  - in containerul Docker se foloseste `ODBC Driver 18 for SQL Server`
- `LASER_DATA_DIR` pentru directorul persistent in care se salveaza SQLite-ul local
- `LASER_SQLITE_PATH` daca vrei sa controlezi direct fisierul SQLite
- daca exista deja o baza veche in `/app/data/laser_monitor.db`, aplicatia o migreaza automat spre noul path persistent la prima pornire
- fara volum persistent montat, se pierd la update: timpii, randamentele, evenimentele si ciclurile salvate
- `LASER_REAL_DATA_NAME` numele sursei reale care va trimite date spre dashboard
- `LASER_REAL_DATA_ENDPOINT` endpointul sau descrierea sursei reale, pentru afisare in UI
- `LASER1_REAL_DATA_ENDPOINT`, `LASER1MODBUS_REAL_DATA_ENDPOINT`, `LASER2_REAL_DATA_ENDPOINT`, `LASER2MODBUS_REAL_DATA_ENDPOINT`, `ABKANT_REAL_DATA_ENDPOINT` pentru endpoint separat pe fiecare utilaj (pentru OCR la laser foloseste streamul dedicat, ex. `http://laserbvision-1:8081`)
- `LASER1_CAMERA_FEED_URL`, `LASER2_CAMERA_FEED_URL`, `ABKANT_CAMERA_FEED_URL` pentru feedul video pe care vrei sa-l vezi in dashboard
- `LASER1_CAMERA_FEED_MODE`, `LASER2_CAMERA_FEED_MODE`, `ABKANT_CAMERA_FEED_MODE` cu `image` sau `page`, daca feedul camerei trebuie afisat ca imagine sau ca pagina embed-uita
- `LASER1_CAMERA_FEED_USERNAME`, `LASER2_CAMERA_FEED_USERNAME`, `ABKANT_CAMERA_FEED_USERNAME` daca feedul camerei cere autentificare
- `LASER1_CAMERA_FEED_PASSWORD`, `LASER2_CAMERA_FEED_PASSWORD`, `ABKANT_CAMERA_FEED_PASSWORD` pentru parola camerei
- `LASER1_CAMERA_FEED_AUTH`, `LASER2_CAMERA_FEED_AUTH`, `ABKANT_CAMERA_FEED_AUTH` cu `basic` sau `digest`, daca feedul camerei cere autentificare HTTP
- `LASER1_HMI_FEED_URL`, `LASER2_HMI_FEED_URL`, `ABKANT_HMI_FEED_URL` pentru pagina HMI embed-uita in dashboard
- `LASER1MODBUS_MODBUS_TRANSPORT`, `LASER2MODBUS_MODBUS_TRANSPORT` cu `tcp` sau `rtu`
- `LASER1MODBUS_MODBUS_HOST`, `LASER2MODBUS_MODBUS_HOST`, port, unit id, tip biti si adresa start pentru citirea Modbus TCP directa din container
- `LASER1MODBUS_MODBUS_SERIAL_PORT`, `LASER2MODBUS_MODBUS_SERIAL_PORT`, baud rate, parity si stop bits pentru citirea Modbus RTU prin RS485/USB direct din container sau din Windows
- `LASER1MODBUS_MODBUS_IN1_SIGNAL` .. `LASER1MODBUS_MODBUS_IN4_SIGNAL` si `LASER2MODBUS_MODBUS_IN1_SIGNAL` .. `LASER2MODBUS_MODBUS_IN4_SIGNAL` daca vrei o configurare initiala din `.env`; ulterior aceeasi mapare poate fi schimbata direct din dashboard
- `ABKANT_PG_HOST`, `ABKANT_PG_DATABASE`, `ABKANT_PG_USER`, `ABKANT_PG_PASSWORD` pentru fallback-ul Abkant din PostgreSQL cind linkul video pica
- daca rulezi aplicatia in Docker pe un server public, endpointurile de tip Tailscale sau hostname intern trebuie sa fie accesibile si din container; altfel utilajul ramine `OFF`
- `BACKGROUND_SYNC_ENABLED=1` porneste pollerul din fundal care urmareste utilajele chiar daca nu ai pagina deschisa
- `BACKGROUND_SYNC_INTERVAL_SECONDS=1` controleaza la cite secunde se face sincronizarea live si salvarea automata in istoricul local
- `REQUEST_LIVE_SYNC_ENABLED=0` pastreaza dashboard-ul rapid si evita sync-ul live direct in request cind exista deja pollerul din fundal; seteaza `1` doar daca vrei fallback sincron in request
- `SAVED_RECORDS_PROMETHEUS_ENABLED=1` (recomandat in productie) reconstruieste istoricul din Prometheus cind SQLite local este gol dupa un update/redeploy
- `PROMETHEUS_QUERY_TIMEOUT_SECONDS=10` controleaza timpul de asteptare pe fiecare query Prometheus; pentru rapoarte pe saptamina/luna e recomandat sa nu fie prea mic
- `SNAPSHOT_FRESHNESS_SECONDS=3` forteaza refresh live daca ultimul snapshot din runtime este prea vechi
- `TELEGRAM_BOT_TOKEN` tokenul botului Telegram pentru rapoarte si comenzi
- `TELEGRAM_CHAT_IDS` lista de chat-uri care primesc raportul automat, separate prin virgula/spatiu
- `TELEGRAM_ALLOWED_CHAT_IDS` optional, limiteaza cine poate folosi comenzile `/raportzilaser` si `/raportziabkant`; daca lipseste, se foloseste `TELEGRAM_CHAT_IDS`
- `TELEGRAM_REPORT_TIME=23:30` ora locala la care se trimite raportul automat de seara
- `TELEGRAM_REPORT_MACHINE_KEYS=laser1modbus,laser2modbus,abkant1modbus` masinile incluse in raportul automat; implicit raman toate cele trei utilaje
- `TELEGRAM_NOTIFICATION_MODE=active` controleaza sunetul mesajelor trimise de bot; foloseste `silent` pentru mesaje fara sunet
- `TELEGRAM_COMPLETED_CYCLE_REPORTS_ENABLED=0` tine oprite notificarile automate de tip dosar/ciclu finalizat; comenzile manuale si raportul de noapte raman active
- `TELEGRAM_REPORT_TOP_LIMIT=10` cati operatori apar in topul de randament
- `TELEGRAM_DOSAR_LOOKBACK_DAYS=730` cite zile cauta comanda `/randament_dosar 34158` in istoricul Prometheus
- `TELEGRAM_TABLE_CHANGE_FREE_SECONDS=90` pragul gratuit pentru `Table change`; ce depaseste pragul scade randamentul Telegram
- `ABKANT_OCR_BENDING_GRACE_SECONDS=180` tine ABKANT1MODBUS in `Indoire activa` timp de 3 minute dupa fiecare crestere OCR de piese, apoi trece in `Idle`
- `ABKANT_IDLE_STAGNATION_SECONDS=600` marcheaza fallback-ul vechi Abkant/PostgreSQL ca `Idle` daca programul si progresul ramin neschimbate mai mult de 10 minute
- `ABKANT_FEED_STALE_SECONDS=120` spune dupa cite secunde fara colectare recenta snapshotul Abkant trebuie tratat ca `Feed indisponibil`
- `MODBUS_TCP_RETRY_ATTEMPTS=3` reincearca citirea Modbus TCP de citeva ori inainte sa declare timeout
- `MODBUS_TCP_RETRY_DELAY_SECONDS=0.15` pauza scurta intre incercarile de retry Modbus TCP
- `MODBUS_SNAPSHOT_GRACE_SECONDS=18` pastreaza temporar ultimul snapshot valid LASER1MODBUS la intreruperi scurte, ca sa evite reseturi false
- `PROMETHEUS_BASE_URL=http://localhost:9090` spune dashboard-ului de unde sa citeasca istoricul foilor salvate din Prometheus
- paginile `Date salvate` si `Date Salvate MODBUS` incearca mai intii sa reconstruiasca ciclurile din Prometheus; daca Prometheus nu raspunde sau nu are inca seriile salvate, cad temporar pe SQLite
- pentru persistenta reala dupa update, Prometheus trebuie sa aiba propriul director de date persistent si o retenție suficient de mare

## Bridge Modbus RTU -> TCP pentru PC separat

Daca adaptorul USB-RS485 este bagat intr-un alt PC decit serverul Docker, nu folosi `Modbus RTU / RS485` direct in dashboard. In schimb:

1. Pe PC-ul unde exista portul `COM9`, rulezi bridge-ul:

```powershell
python .\modbus_rtu_tcp_bridge.py --serial-port COM9 --baudrate 9600 --parity N --stopbits 1 --unit-id 1 --tcp-host 0.0.0.0 --tcp-port 502
```

2. In dashboard-ul de pe serverul Docker alegi:

- `Transport`: `Modbus TCP`
- `Host / IP`: IP-ul PC-ului pe care ruleaza bridge-ul, de exemplu `192.168.2.222`
- `Port`: `502`
- `Unit ID`: `1`
- `Tip biti`: `Discrete Inputs`
- `Adresa start`: `0` sau `1`, in functie de cum numeroteaza modulul `DI1`

Bridge-ul expune prin TCP citirile Modbus RTU pentru:

- `Coils` (`Function Code 1`)
- `Discrete Inputs` (`Function Code 2`)

Asta este suficient pentru dashboard-ul Laser1, care citeste doar stari digitale `IN1..IN4`.

### Startup automat pe Linux cu systemd

Daca PC-ul de linga utilaj ruleaza Linux, poti porni bridge-ul automat la boot:

1. copiezi repo-ul in `/opt/lasertrumaticl3030`
2. copiezi fisierul de mediu:

```bash
sudo cp /opt/lasertrumaticl3030/deploy/systemd/modbus-rtu-tcp-bridge.env.example /etc/default/modbus-rtu-tcp-bridge
```

3. editezi `/etc/default/modbus-rtu-tcp-bridge` si pui portul serial real, de exemplu `/dev/ttyUSB0`
4. instalezi serviciul:

```bash
sudo cp /opt/lasertrumaticl3030/deploy/systemd/modbus-rtu-tcp-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now modbus-rtu-tcp-bridge.service
```

5. verifici statusul:

```bash
sudo systemctl status modbus-rtu-tcp-bridge.service
sudo journalctl -u modbus-rtu-tcp-bridge.service -f
```

Pe Linux, portul serial va fi de obicei `/dev/ttyUSB0` sau `/dev/ttyUSB1`, nu `COM9`.

## Mutare pe 192.168.2.242 + Cloudflare (recomandat)

Daca vrei ca site-ul sa ruleze direct pe PC-ul `192.168.2.242`, foloseste stack-ul:

- `deploy/docker-compose.242-cloudflare.yml`
- `deploy/.env.242-cloudflare.example`

### De ce apare `NET::ERR_CERT_AUTHORITY_INVALID`

Eroarea din browser inseamna ca raspunsul TLS nu vine cu un certificat public valid pentru `laser.helpan.ro` (si domeniul are HSTS, deci browserul blocheaza complet accesul nesigur).

Cea mai curata varianta este Cloudflare Tunnel:

- nu mai deschizi porturi in router
- nu depinzi de certificat local/self-signed pe origin
- hostname-ul public este terminat TLS la Cloudflare

### Pasii rapizi pe 192.168.2.242

1. instalezi Docker Desktop si il setezi sa porneasca automat la boot
2. copiezi proiectul pe PC-ul `192.168.2.242`
3. copiezi `deploy/.env.242-cloudflare.example` in `deploy/.env.242-cloudflare` si completezi `CLOUDFLARE_TUNNEL_TOKEN`
4. pornesti stack-ul:

```powershell
docker compose --env-file deploy/.env.242-cloudflare -f deploy/docker-compose.242-cloudflare.yml up -d
```

5. in Cloudflare Tunnel setezi public hostname:

- `laser.helpan.ro` -> `http://lasertrumaticl3030:3030`

6. in dashboard-ul aplicatiei verifici `LASER1MODBUS`:

- `Transport`: `Modbus TCP`
- `Host / IP`: `192.168.2.242`
- `Port`: `502`
- `Unit ID`: `1`

### Verificare rapida

```powershell
docker ps
docker logs --tail 100 lasertrumaticl3030
docker logs --tail 100 lasertrumaticl3030_cloudflared
```

## Grafana

Repo-ul include un dashboard Grafana gata de import:

- `grafana/dashboards/haba_production_monitor.json`

Dashboard-ul are tabele pentru:

- `Laser1` pe operator si pe program
- `Abkant` pe operator si pe program, inclusiv `Setup-uri`
- ferestre `zi / saptamana / luna`
- randament real cumulat, nu media simpla pe foi
- pentru `Abkant`, timpul de `Setup change` este derivat din schimbarea sculelor `Upper/Lower`

Import manual:

1. deschizi Grafana
2. `Dashboards` -> `New` -> `Import`
3. alegi fisierul `grafana/dashboards/haba_production_monitor.json`
4. selectezi datasource-ul Prometheus

Provisioning automat:

- datasource sample: `grafana/provisioning/datasources/prometheus.yml`
- dashboard provisioning: `grafana/provisioning/dashboards/dashboard.yml`

Pentru containerul Grafana, montezi:

- `grafana/provisioning/datasources` -> `/etc/grafana/provisioning/datasources`
- `grafana/provisioning/dashboards` -> `/etc/grafana/provisioning/dashboards`
- `grafana/dashboards` -> `/var/lib/grafana/dashboards`

Si setezi variabila:

```env
GRAFANA_PROMETHEUS_URL=http://192.168.2.23:9090
```

## Docker pentru Unraid

Build local:

```bash
docker build -t lasertrumaticl3030:latest .
```

Run:

```bash
docker run -d \
  --name lasertrumaticl3030 \
  -p 3030:3030 \
  -e BACKGROUND_SYNC_ENABLED=1 \
  -e BACKGROUND_SYNC_INTERVAL_SECONDS=10 \
  -e LASER1_REAL_DATA_ENDPOINT='http://laserbvision-1:8081' \
  -e LASER1MODBUS_REAL_DATA_ENDPOINT='http://laserbvision-1:8081' \
  -e LASER2_REAL_DATA_ENDPOINT='' \
  -e LASER2MODBUS_REAL_DATA_ENDPOINT='http://192.168.2.138:8081' \
  -e ABKANT_REAL_DATA_ENDPOINT='https://abkant.helpan.ro/' \
  -e LASER1_CAMERA_FEED_URL='http://192.168.2.140/ISAPI/Streaming/channels/101/picture' \
  -e LASER1MODBUS_CAMERA_FEED_URL='http://192.168.2.140/ISAPI/Streaming/channels/101/picture' \
  -e LASER2_CAMERA_FEED_URL='' \
  -e ABKANT_CAMERA_FEED_URL='https://abkant.helpan.ro/' \
  -e LASER1_CAMERA_FEED_MODE='image' \
  -e LASER2_CAMERA_FEED_MODE='image' \
  -e ABKANT_CAMERA_FEED_MODE='image' \
  -e LASER1_CAMERA_FEED_USERNAME='' \
  -e LASER2_CAMERA_FEED_USERNAME='' \
  -e ABKANT_CAMERA_FEED_USERNAME='' \
  -e LASER1_CAMERA_FEED_PASSWORD='' \
  -e LASER2_CAMERA_FEED_PASSWORD='' \
  -e ABKANT_CAMERA_FEED_PASSWORD='' \
  -e LASER1_CAMERA_FEED_AUTH='basic' \
  -e LASER2_CAMERA_FEED_AUTH='basic' \
  -e ABKANT_CAMERA_FEED_AUTH='basic' \
  -e LASER1_HMI_FEED_URL='https://laser.helpan.ro/' \
  -e LASER2_HMI_FEED_URL='' \
  -e LASER2MODBUS_HMI_FEED_URL='http://192.168.2.138:8081' \
  -e ABKANT_HMI_FEED_URL='https://abkant.helpan.ro/' \
  -e PONTAJ_LASER1_WORKCENTER_ID=1 \
  -e PONTAJ_LASER2_WORKCENTER_ID=2 \
  -e PONTAJ_LASER2MODBUS_WORKCENTER_ID=1 \
  -e PONTAJ_ABKANT_WORKCENTER_ID=3 \
  -e LASER2MODBUS_MODBUS_HOST='<modbus-host>' \
  -e LASER2MODBUS_MODBUS_PORT=502 \
  -e LASER2MODBUS_MODBUS_UNIT_ID=1 \
  -e LASER2MODBUS_MODBUS_IN1_SIGNAL='machine_on' \
  -e PONTAJ_SQL_SERVER='<sql-host>' \
  -e PONTAJ_SQL_DATABASE='<sql-database>' \
  -e PONTAJ_SQL_USERNAME='<sql-user>' \
  -e PONTAJ_SQL_PASSWORD='<sql-password>' \
  -e PONTAJ_SQL_DRIVER='ODBC Driver 18 for SQL Server' \
  -v /mnt/user/appdata/lasertrumaticl3030:/data \
  lasertrumaticl3030:latest
```

Pe Unraid, monteaza `/data` ca volum persistent. Acolo ramin `laser_monitor.db`, timpii, randamentele si toate datele salvate dupa update.

### Update sigur cu Watchtower

Repo-ul include un exemplu gata de folosit: `deploy/docker-compose.watchtower.yml`.

Pornire:

```bash
docker compose -f deploy/docker-compose.watchtower.yml up -d
```

Cheia este volumul persistent:

- `lasertrumaticl3030_data:/data`

Atit timp cit `/data` ramine acelasi volum, update-urile Watchtower nu mai sterg randamentele, setarile MODBUS si istoricul salvat.

## GitHub Container Registry

Workflow-ul din `.github/workflows/docker-image.yml` publica imaginea:

- `ghcr.io/eduard2020204039/lasertrumaticl3030:latest`
- `ghcr.io/eduard2020204039/lasertrumaticl3030:sha-...`

Ca sa tragi imaginea dupa push pe `main`:

```bash
docker pull ghcr.io/eduard2020204039/lasertrumaticl3030:latest
```

Publicarea imaginii nu face singura si redeploy. Dupa `docker pull`, containerul trebuie recreat sau repornit cu noua imagine.

## Tailscale

Tailscale este util pentru:

- acces securizat la dashboard din afara halei
- acces la serverul care ruleaza aplicatia fara port forward
- eventual subnet routing catre reteaua unde este laserul

Tailscale nu citeste singur datele din laser. El doar iti da acces sigur la reteaua unde exista deja PLC-ul, gateway-ul IO sau calculatorul care colecteaza semnalele.

## Cum legam butoanele la IO real

Acum, dashboardul trimite manual `POST /api/events` cind apesi un buton. La IO real facem acelasi lucru, doar ca in loc de click-ul din browser trimite semnalul un dispozitiv hardware.

Exemplu simplu:

- iei trei semnale digitale din masina: `Machine ON`, `Cutting active`, `Table change`
- le bagi intr-un modul IO sau PLC cu intrari izolate
- un script mic pe Raspberry Pi, mini-PC sau gateway citeste intrarile
- la fiecare schimbare de stare face request catre aplicatie:

```json
POST /api/events
{
  "signal_name": "cutting_active",
  "value": true,
  "source": "plc-io"
}
```

Practic, butoanele manuale sint doar simulatorul pentru semnalele care vor veni mai tirziu din hardware-ul real.

## API rapid

`POST /api/events`

```json
{
  "signal_name": "cutting_active",
  "value": true,
  "note": "test schimb 1",
  "source": "manual-dashboard"
}
```

`GET /api/dashboard` intoarce tot ce are nevoie frontend-ul pentru refresh.

### API pentru agent Hermes

Lista de endpoint-uri si exemple:

`GET /api/hermes/endpoints`

Observatie live pentru laser, cu raspuns direct la "taie acum?" si "de ce nu taie?":

`GET /api/hermes/laser/observe?machine=laser1modbus`

Campuri utile din raspuns:

- `state.cutting_now`, `state.attention_level`, `state.status_text`
- `why_not_cutting[]` cu `code`, `severity`, `message`
- `current_job.dosar_id`, `current_job.selected_program`, `current_job.completion_percent`
- `operator.operator_name`, `operator.operator_id`, `operator.is_active`
- `feed.feeds[]`, `feed.available`, `feed.connected`, `feed.message`
- `signals.machine_on`, `signals.cutting_active`, `signals.table_change`, `signals.idle_abort`

Ultimele dosare/cicluri finalizate, ca sa vezi ce dosar a fost taiat si de cine:

`GET /api/hermes/laser/cycles?machine=laser1modbus&limit=10`

Poti folosi `machine=laser2modbus`, `machine=abkant1modbus` sau `machine=all`. `limit` este intre 1 si 50.

Endpoint admin pentru ca Hermes sa vada aproape tot ce vede aplicatia:

`GET /api/hermes/site/full-snapshot?include_db_rows=1&db_limit=25&include_telegram_reports=1`

Pe domeniul public:

`GET https://habaproduction.habaresearch.eu/api/hermes/site/full-snapshot?include_db_rows=1&db_limit=25&include_telegram_reports=1`

Implicit, endpointul raspunde in modul rapid `cached`, ca monitorul sa nu intre in timeout. Pentru varianta grea, care forteaza detalii live si query-uri MODBUS/Prometheus:

`GET /api/hermes/site/full-snapshot?full=1&include_db_rows=1&db_limit=25&include_telegram_reports=1`

Accepta si `POST` cu JSON:

```json
{
  "include_db_rows": true,
  "db_limit": 25,
  "include_telegram_reports": true,
  "full": false
}
```

Aliasuri acceptate:

- `/api/hermes/site/full-snapshot`
- `/api/hermes/site/full-snapshot/`
- `/api/hermes/full-snapshot`
- `/api/hermes/full-snapshot/`

Include:

- harta rutelor site-ului
- toate masinile, dashboardurile si observatiile live
- status Telegram, comenzi, formule si preview de rapoarte
- baza SQLite: schema pentru tabele si, daca `include_db_rows=1`, randuri din tabele
- environment snapshot, saved cycles si saved records MODBUS

Dump direct din SQLite:

`GET /api/hermes/database/dump?table=all&limit=100`

Status Telegram separat:

`GET /api/hermes/telegram/status?include_reports=1`

Pentru protectie, seteaza in `.env`:

```env
HERMES_API_TOKEN=un-token-lung-aici
```

Si cheama endpointurile admin cu:

```http
Authorization: Bearer un-token-lung-aici
```

Daca `HERMES_API_TOKEN` lipseste, endpointurile admin raspund fara token, dar payload-ul include un warning.

Verificare rapida ca requestul ajunge in aplicatia Flask corecta:

`GET /api/hermes/ping`

Pe domeniul public:

`GET https://habaproduction.habaresearch.eu/api/hermes/ping`

Trebuie sa intoarca JSON cu `service: HABA Production Monitor`. Daca primesti HTML, `Bad Request` sau alt text, requestul loveste alt server/proxy/container, nu aplicatia actualizata.
