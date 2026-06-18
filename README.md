# BEUTEPLAN – Lidl-Proxy (Render)

Liefert die aktuellen Lidl-Wochenangebote einer Filiale CORS-offen als JSON,
damit die Web-App (Vercel) sie laden kann. Ein Browser kann Lidl wegen CORS
nicht direkt abfragen – darum dieser kleine Server.

## Deploy auf Render (kostenlos)

1. Dateien dieses Ordners in ein neues GitHub-Repo pushen.
2. render.com → New → **Web Service** → das Repo wählen.
3. Render liest `render.yaml` automatisch. Falls nicht, manuell setzen:
   - **Runtime:** Python
   - **Build Command:**
     ```
     pip install -r requirements.txt && git clone https://github.com/EvickaStudio/lidl-discounts.git && (pip install -e ./lidl-discounts || true) && cp lidl_proxy.py lidl-discounts/lidl_proxy.py
     ```
   - **Start Command:**
     ```
     cd lidl-discounts && gunicorn lidl_proxy:app --bind 0.0.0.0:$PORT --timeout 120 --pythonpath .
     ```
   - **Environment Variable:** `PYTHON_VERSION = 3.12.4`
4. Nach ~2 min läuft der Service unter `https://<name>.onrender.com`.

## Testen

- `https://<name>.onrender.com/`            → `{"ok": true, ...}`
- `https://<name>.onrender.com/offers?ort=Würzburg`  → Angebote als JSON
- `https://<name>.onrender.com/raw?ort=Würzburg`     → Rohstruktur (zum Prüfen der Feldnamen)

In der App unter **Angebote → Lidl-Proxy** die `/offers`-URL eintragen.

## Hinweise

- **Free-Tier schläft** nach ~15 min ein; der erste Aufruf danach dauert ~50 s.
- Quelle ist **inoffiziell** (Lidl-Plus). Wenn Lidl das Format ändert, kann es
  brechen. Bitte nicht massenhaft abfragen (Cache = 6 h ist eingebaut).
- Stimmen Namen/Preise nicht? `/raw` aufrufen und die echten Feldnamen melden –
  dann wird `normalize()` exakt darauf gemappt.
