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
- `LASER1_REAL_DATA_ENDPOINT`, `LASER2_REAL_DATA_ENDPOINT`, `ABKANT_REAL_DATA_ENDPOINT` pentru endpoint separat pe fiecare utilaj
- `LASER1_CAMERA_FEED_URL`, `LASER2_CAMERA_FEED_URL`, `ABKANT_CAMERA_FEED_URL` pentru feedul video pe care vrei sa-l vezi in dashboard
- `LASER1_CAMERA_FEED_MODE`, `LASER2_CAMERA_FEED_MODE`, `ABKANT_CAMERA_FEED_MODE` cu `image` sau `page`, daca feedul camerei trebuie afisat ca imagine sau ca pagina embed-uita
- `LASER1_CAMERA_FEED_USERNAME`, `LASER2_CAMERA_FEED_USERNAME`, `ABKANT_CAMERA_FEED_USERNAME` daca feedul camerei cere autentificare
- `LASER1_CAMERA_FEED_PASSWORD`, `LASER2_CAMERA_FEED_PASSWORD`, `ABKANT_CAMERA_FEED_PASSWORD` pentru parola camerei
- `LASER1_CAMERA_FEED_AUTH`, `LASER2_CAMERA_FEED_AUTH`, `ABKANT_CAMERA_FEED_AUTH` cu `basic` sau `digest`, daca feedul camerei cere autentificare HTTP
- `LASER1_HMI_FEED_URL`, `LASER2_HMI_FEED_URL`, `ABKANT_HMI_FEED_URL` pentru pagina HMI embed-uita in dashboard
- `LASER1MODBUS_MODBUS_TRANSPORT` cu `tcp` sau `rtu`
- `LASER1MODBUS_MODBUS_HOST`, `LASER1MODBUS_MODBUS_PORT`, `LASER1MODBUS_MODBUS_UNIT_ID`, `LASER1MODBUS_MODBUS_BIT_SOURCE`, `LASER1MODBUS_MODBUS_START_ADDRESS` pentru citirea Modbus TCP directa din container
- `LASER1MODBUS_MODBUS_SERIAL_PORT`, `LASER1MODBUS_MODBUS_SERIAL_BAUDRATE`, `LASER1MODBUS_MODBUS_SERIAL_PARITY`, `LASER1MODBUS_MODBUS_SERIAL_STOPBITS` pentru citirea Modbus RTU prin RS485/USB direct din container sau din Windows
- `LASER1MODBUS_MODBUS_IN1_SIGNAL` .. `LASER1MODBUS_MODBUS_IN4_SIGNAL` daca vrei o configurare initiala din `.env`; ulterior aceeasi mapare poate fi schimbata direct din dashboard
- `ABKANT_PG_HOST`, `ABKANT_PG_DATABASE`, `ABKANT_PG_USER`, `ABKANT_PG_PASSWORD` pentru fallback-ul Abkant din PostgreSQL cind linkul video pica
- daca rulezi aplicatia in Docker pe un server public, endpointurile de tip Tailscale sau hostname intern trebuie sa fie accesibile si din container; altfel utilajul ramine `OFF`
- `BACKGROUND_SYNC_ENABLED=1` porneste pollerul din fundal care urmareste utilajele chiar daca nu ai pagina deschisa
- `BACKGROUND_SYNC_INTERVAL_SECONDS=3` controleaza la cite secunde se face sincronizarea live si salvarea automata in istoricul local
- `SNAPSHOT_FRESHNESS_SECONDS=3` forteaza refresh live daca ultimul snapshot din runtime este prea vechi
- `ABKANT_IDLE_STAGNATION_SECONDS=600` marcheaza Abkantul ca `Idle` daca programul si progresul ramin neschimbate mai mult de 10 minute
- `PROMETHEUS_BASE_URL=http://localhost:9090` spune dashboard-ului de unde sa citeasca istoricul foilor salvate din Prometheus
- pagina `Date salvate` incearca mai intii sa reconstruiasca operatorii si foile din Prometheus; daca Prometheus nu raspunde sau nu are inca seriile salvate, cade temporar pe SQLite
- pentru persistenta reala dupa update, Prometheus trebuie sa aiba propriul director de date persistent si o retenție suficient de mare

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
  -e LASER1_REAL_DATA_ENDPOINT='https://laser.helpan.ro/' \
  -e LASER2_REAL_DATA_ENDPOINT='' \
  -e ABKANT_REAL_DATA_ENDPOINT='https://abkant.helpan.ro/' \
  -e LASER1_CAMERA_FEED_URL='https://laser.helpan.ro/' \
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
  -e ABKANT_HMI_FEED_URL='https://abkant.helpan.ro/' \
  -e PONTAJ_LASER1_WORKCENTER_ID=1 \
  -e PONTAJ_LASER2_WORKCENTER_ID=2 \
  -e PONTAJ_ABKANT_WORKCENTER_ID=3 \
  -e PONTAJ_SQL_SERVER=192.168.2.6 \
  -e PONTAJ_SQL_DATABASE=Metal \
  -e PONTAJ_SQL_USERNAME=bogdan \
  -e PONTAJ_SQL_PASSWORD='HELPAN123$' \
  -e PONTAJ_SQL_DRIVER='ODBC Driver 18 for SQL Server' \
  -v /mnt/user/appdata/lasertrumaticl3030:/data \
  lasertrumaticl3030:latest
```

Pe Unraid, monteaza `/data` ca volum persistent. Acolo ramin `laser_monitor.db`, timpii, randamentele si toate datele salvate dupa update.

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
