# Laser TruMatic L3030

Faza 1 pentru monitorizare laser:

- dashboard web modern
- stari manuale pentru `machine_on`, `cutting_active`, `table_change`
- operator curent luat din `PontajWorkCenter` / baza `Metal`
- persistenta locala in SQLite pentru evenimente si timpi pe zi
- Dockerfile pentru Unraid
- workflow GitHub Actions pentru publicare imagine in GHCR ca `ghcr.io/<owner>/lasertrumaticl3030:latest`

## Ce este inclus acum

Aplicatia afiseaza:

- statusul curent al masinii
- operatorul activ pe `WorkCenterID=1` implicit
- istoricul ultimelor schimbari
- timp total alimentat, timp de taiere, timp schimb masa si idle pentru ziua curenta

Pentru inceput, controalele sint manuale. Mai tirziu, aceleasi endpoint-uri pot fi chemate de un PLC, Raspberry Pi sau modul IO industrial.

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

- `PONTAJ_WORKCENTER_ID=1` pentru workcenterul Laser din baza voastra
- `PONTAJ_SQL_DRIVER`
  - pe Windows merge de obicei `ODBC Driver 17 for SQL Server`
  - in containerul Docker se foloseste `ODBC Driver 18 for SQL Server`
- `LASER_SQLITE_PATH` daca vrei alta locatie pentru baza locala

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
  -e PONTAJ_WORKCENTER_ID=1 \
  -e PONTAJ_SQL_SERVER=192.168.2.6 \
  -e PONTAJ_SQL_DATABASE=Metal \
  -e PONTAJ_SQL_USERNAME=bogdan \
  -e PONTAJ_SQL_PASSWORD='HELPAN123$' \
  -e PONTAJ_SQL_DRIVER='ODBC Driver 18 for SQL Server' \
  -v /mnt/user/appdata/lasertrumaticl3030:/app/data \
  lasertrumaticl3030:latest
```

Pe Unraid, monteaza `/app/data` ca volum persistent.

## GitHub Container Registry

Workflow-ul din `.github/workflows/docker-image.yml` publica imaginea:

- `ghcr.io/<owner>/lasertrumaticl3030:latest`
- `ghcr.io/<owner>/lasertrumaticl3030:sha-...`

Ca sa tragi imaginea dupa push pe `main`:

```bash
docker pull ghcr.io/<owner>/lasertrumaticl3030:latest
```

## Tailscale

Tailscale este util pentru:

- acces securizat la dashboard din afara halei
- acces la serverul care ruleaza aplicatia fara port forward
- eventual subnet routing catre reteaua unde este laserul

Tailscale nu citeste singur datele din laser. El doar iti da acces sigur la reteaua unde exista deja PLC-ul, gateway-ul IO sau calculatorul care colecteaza semnalele.

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
