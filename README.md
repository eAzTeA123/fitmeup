# Beuteplan — Lidl-Proxy auf Render

Dieser Ordner enthält alles, um `lidl_proxy.py` produktionsreif auf
[Render](https://render.com) zu deployen — inklusive automatischem Beziehen
des LidlPlus-Clients, Production-WSGI-Server (gunicorn), Health-Check, CORS
und konfigurierbarem Cache.

## Was wurde gegenüber der ursprünglichen `lidl_proxy.py` geändert?

1. **`requirements.txt` korrigiert.** Das alte Setup ging von `requests` aus.
   Der aktuelle Client [EvickaStudio/lidl-discounts](https://github.com/EvickaStudio/lidl-discounts)
   nutzt tatsächlich **httpx + pydantic + pydantic-settings**. Außerdem
   `gunicorn` ergänzt (Flasks eigener Dev-Server ist nicht für Produktion
   gedacht — Render erwartet einen echten WSGI-Server).
2. **`build.sh` neu.** Render baut aus deinem Git-Repo, kennt aber das externe
   `lidl-discounts`-Repo nicht. `build.sh` installiert die Dependencies und
   klont anschließend `main.py` **und** das komplette `lidl/`-Paket
   (`config.py`, `models.py`, `utils.py`) aus dem Upstream-Repo — das war im
   ursprünglichen Kommentar ("git clone … main.py liegt daneben") nicht
   berücksichtigt, das Repo ist kein Einzeldatei-Skript mehr.
3. **`normalize()` korrigiert.** Die Feldnamen-Rateliste (`oldPrice`,
   `startValidityDate`, …) passte nicht zum tatsächlichen `Offer`-Schema des
   Clients (`title`, `brand`, `packaging`, sowie die Properties `price`,
   `old_price`, `discount`, `start_validity_date`, `end_validity_date`).
   Ohne diese Korrektur wären `regularPrice`, `validFrom` und `validTo` immer
   `null` gewesen.
4. **PORT/ENV statt Hardcoding.** `lidl_proxy.py` liest jetzt `PORT` (von
   Render vorgegeben), `CORS_ORIGINS`, `CACHE_TTL_SECONDS`, `DEFAULT_ORT`,
   `DEFAULT_LAND` aus Environment-Variablen statt feste Werte/Port 8000 zu
   erzwingen.
5. **`/healthz`** als schneller Health-Check-Endpunkt (ruft selbst keine
   Lidl-Endpunkte auf) — für Render's Health Checks.
6. **Robusteres Error-Handling & Logging** statt stillem Fehlschlagen, plus
   ein Lock, der verhindert, dass mehrere gleichzeitige Requests bei kaltem
   Cache parallel dieselbe Lidl-Anfrage auslösen.
7. **`render.yaml`** als Blueprint für 1-Klick-Deploy.

## Deploy in 5 Minuten

### Schritt 1 — Eigenes Git-Repo

Render deployt aus Git, nicht aus losen Dateien. Lege ein **neues, privates**
GitHub-Repo an und pushe die 4 Dateien aus diesem Ordner
(`lidl_proxy.py`, `requirements.txt`, `build.sh`, `render.yaml`):

```bash
mkdir beuteplan-lidl-proxy && cd beuteplan-lidl-proxy
# die 4 Dateien hier hineinkopieren
git init && git add . && git commit -m "Lidl-Proxy für Beuteplan"
git branch -M main
git remote add origin https://github.com/<dein-user>/beuteplan-lidl-proxy.git
git push -u origin main
```

> main.py + lidl/ NICHT mit hochladen — die holt build.sh bei jedem Deploy
> frisch vom Upstream-Repo. So bekommst du automatisch Fixes, falls Lidl mal
> wieder seine Endpunkte ändert.

### Schritt 2 — Blueprint auf Render

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. Das gerade erstellte Repo verbinden. Render erkennt `render.yaml`
   automatisch und schlägt Build/Start-Command, Health-Check und Env-Vars
   vor (alles schon vorausgefüllt).
3. **Apply** klicken. Erster Build dauert ca. 1–2 Minuten (klont das
   Lidl-Repo, installiert httpx/pydantic/Flask/gunicorn).

Ohne Blueprint geht's auch manuell: **New → Web Service**, Repo verbinden,
Runtime **Python 3**, Build Command `bash build.sh`, Start Command
`gunicorn lidl_proxy:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 30`.

### Schritt 3 — URL in die App eintragen

Render gibt dir eine URL wie `https://beuteplan-lidl-proxy.onrender.com`.
In der Beuteplan-App unter **Angebote → Lidl-Proxy** eintragen:

```
https://beuteplan-lidl-proxy.onrender.com/offers?ort=Würzburg
```

### Schritt 4 — Testen

```
curl "https://beuteplan-lidl-proxy.onrender.com/offers?ort=Würzburg"
```

Erste Antwort kann auf dem kostenlosen Plan ein paar Sekunden dauern
(„Cold Start" nach 15 Minuten Inaktivität — siehe Hinweise unten).

## Endpunkte

| Pfad | Zweck |
|---|---|
| `/offers?ort=<Stadt>&land=<DE/AT/...>` | Angebote der nächsten Filiale, JSON, gecacht |
| `/healthz` | Schneller Health-Check (für Render selbst) |
| `/raw?ort=<Stadt>` | Debug: rohe Struktur des ersten Angebots |
| `/` | einfacher „ok"-Ping |

## Konfiguration (Environment-Variablen, alle optional)

| Variable | Default | Bedeutung |
|---|---|---|
| `CORS_ORIGINS` | `*` | `*` oder kommagetrennte Liste eigener Domains |
| `CACHE_TTL_SECONDS` | `21600` (6h) | Wie lange eine Filiale/Land-Kombi gecacht wird |
| `DEFAULT_ORT` | `Berlin` | Fallback, falls `ort` im Request fehlt |
| `DEFAULT_LAND` | `DE` | Fallback, falls `land` im Request fehlt |
| `LIDL_LATITUDE`, `LIDL_LONGITUDE` | länderabhängig | Werden direkt vom LidlPlus-Client gelesen (Such-Ranking) |
| `LIDL_TIMEOUT` | `20` | HTTP-Timeout des Clients in Sekunden |

## Bekannte Render-Free-Tier-Eigenheiten

- **Spin-down nach 15 Min Inaktivität** → erste Anfrage danach dauert ein
  paar Sekunden länger (Cold Start), genau wie der Lidl-Cache nach jedem
  Neustart wieder kalt ist (In-Memory, kein persistenter Speicher).
- Ein Worker-Prozess + Threads (statt mehrere Worker) ist hier bewusst
  gewählt, damit sich alle Requests **einen** Cache teilen — bei mehreren
  Worker-Prozessen hätte jeder seinen eigenen, getrennten Cache.

## Lokal testen (ohne Render)

```bash
python3 -m venv .venv && source .venv/bin/activate
bash build.sh                 # installiert deps + holt main.py/lidl/
python lidl_proxy.py          # Dev-Server auf Port 8000
curl "http://localhost:8000/offers?ort=Würzburg"
```
