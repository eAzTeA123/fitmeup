# =============================================================================
# Lidl-Angebote-Proxy fuer Beuteplan — Render-Edition
# -----------------------------------------------------------------------------
# Holt serverseitig die aktuellen Wochenangebote einer Lidl-Filiale ueber die
# inoffiziellen, login-freien Lidl-Plus-Endpunkte und liefert sie als JSON mit
# offenem CORS an die Web-App.
#
# WARUM SERVER UND NICHT BROWSER:
#   Der Browser blockiert direkte Zugriffe auf Lidl per CORS. Dieser Proxy laeuft
#   serverseitig (kein CORS) und gibt die Daten dann CORS-offen an deine App weiter.
#
# RECHTLICHES / FAIRPLAY:
#   Inoffiziell, kann jederzeit brechen, wenn Lidl die Endpunkte aendert.
#   Nur fuer den privaten Eigenbedarf, niedrige Request-Frequenz (Cache unten).
#
# DEPLOYMENT AUF RENDER:
#   Siehe README.md in diesem Ordner fuer die Schritt-fuer-Schritt-Anleitung.
#   Kurzfassung:
#     Build Command: bash build.sh
#     Start Command : gunicorn lidl_proxy:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 30
#   (1 Worker-PROZESS, mehrere Threads: nur so teilen sich alle Requests den
#   In-Memory-Cache unten. Fuer privaten Eigenbedarf locker ausreichend.)
#   build.sh installiert requirements.txt UND holt main.py + das lidl/-Paket
#   (LidlPlus-Client) aus dem Repo EvickaStudio/lidl-discounts (Apache-2.0,
#   login-frei).
# =============================================================================

import logging
import os
import threading
import time

from flask import Flask, jsonify, request
from flask_cors import CORS

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("lidl_proxy")

# main.py + das lidl/-Paket kommen aus dem geklonten lidl-discounts-Repo (siehe
# build.sh). Das Repo nutzt intern httpx + pydantic, nicht requests.
# Dokumentiertes Interface:
#   LidlPlus(country="DE")  ->  offers_for_store_search("Wuerzburg") -> (store, offers)
#   store.label ; offers.offers (Liste von Offer-Objekten) ; offers.total_offers
try:
    from main import LidlPlus  # noqa: E402
except ImportError as exc:  # pragma: no cover - nur falls build.sh nicht lief
    raise ImportError(
        "main.py (LidlPlus-Client) fehlt. Lokal: siehe README.md Schritt 1. "
        "Auf Render: stelle sicher, dass der Build Command 'bash build.sh' ist."
    ) from exc

# --------------------------------------------------------------------------
# Konfiguration ueber Environment-Variablen (alle optional, sinnvolle Defaults)
# --------------------------------------------------------------------------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", 6 * 3600))  # 6h Default
DEFAULT_ORT = os.environ.get("DEFAULT_ORT", "Berlin")
DEFAULT_LAND = os.environ.get("DEFAULT_LAND", "DE")

app = Flask(__name__)
# CORS_ORIGINS="*" (Default, Eigenbedarf) oder kommagetrennte Liste eigener
# Domains, z.B. "https://meine-app.netlify.app,https://meine-app.vercel.app"
CORS(app, resources={r"/*": {"origins": CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else "*"}})

_CACHE = {}              # (ort, land) -> (timestamp, payload)
_CACHE_LOCK = threading.Lock()  # verhindert parallele Doppel-Requests bei kaltem Cache


def _get(o, *keys, default=None):
    """Holt einen Wert robust aus dict ODER Objekt (Feldnamen variieren)."""
    for k in keys:
        if isinstance(o, dict) and k in o and o[k] is not None:
            return o[k]
        if hasattr(o, k) and getattr(o, k) is not None:
            return getattr(o, k)
    return default


def _clean(v):
    """'-' und leere Strings (Platzhalter aus dem LidlPlus-Client) -> None,
    damit die Web-App ihre eigenen Fallbacks (z.B. dealFrom()) greifen laesst."""
    if isinstance(v, str) and v.strip() in ("", "-"):
        return None
    return v


def normalize(offer):
    """Bringt ein Lidl-Angebot in das von der App erwartete Schema.

    App erwartet pro Eintrag: {name, price, regularPrice, deal, unit, validFrom, validTo}
    Feldnamen passend zum lidl.models.Offer-Schema von EvickaStudio/lidl-discounts
    (title/brand/packaging/price_per_unit als rohe Felder, price/old_price/discount
    als formatierte Properties wie "1.88 €"). Faellt auf zusaetzliche Varianten
    zurueck, falls sich das inoffizielle Schema mal aendert.
    """
    return {
        "name":         _clean(_get(offer, "title", "label", "name", "product", "productTitle")),
        "brand":        _clean(_get(offer, "brand")),
        "price":        _clean(_get(offer, "price", "offerPrice", "currentPrice", "deal_price")),
        "regularPrice": _clean(_get(offer, "old_price", "regularPrice", "oldPrice", "basePrice", "regular")),
        "deal":         _clean(_get(offer, "discount", "deal", "promotion", "discountText")),
        "unit":         _clean(_get(offer, "packaging", "price_per_unit", "unit", "basePriceText")),
        "validFrom":    _clean(_get(offer, "start_validity_date", "validFrom", "startDate", "startValidityDate")),
        "validTo":      _clean(_get(offer, "end_validity_date", "validTo", "endDate", "endValidityDate")),
    }


def _fetch_offers(ort, land):
    """Tatsaechlicher Lidl-Call (nicht gecacht). Wirft bei Fehlern weiter."""
    with LidlPlus(country=land) as lidl:
        store, result = lidl.offers_for_store_search(ort)
    raw = _get(result, "offers", default=[]) or []
    return {
        "store":  _get(store, "label", "name", default=ort),
        "count":  len(raw),
        "offers": [normalize(o) for o in raw],
    }


@app.route("/")
def index():
    return jsonify({"ok": True, "use": "/offers?ort=Wuerzburg"})


@app.route("/healthz")
def healthz():
    """Schneller Health-Check fuer Render, ruft KEINE Lidl-Endpunkte auf."""
    return jsonify({"status": "ok"})


@app.route("/offers")
def offers():
    ort = request.args.get("ort", DEFAULT_ORT)
    land = request.args.get("land", DEFAULT_LAND)
    key = (ort, land)

    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return jsonify(cached[1])

    # Nur EIN Request pro Key gleichzeitig an Lidl, alle anderen warten kurz
    # und lesen danach aus dem (dann frischen) Cache.
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return jsonify(cached[1])
        try:
            payload = _fetch_offers(ort, land)
        except Exception as e:  # noqa: BLE001 - bewusst breit, externe API
            log.exception("Lidl-Abruf fehlgeschlagen fuer ort=%s land=%s", ort, land)
            return jsonify({"error": str(e), "offers": []}), 502
        _CACHE[key] = (time.time(), payload)
        return jsonify(payload)


@app.route("/raw")
def raw():
    """Debug: zeigt die rohe Struktur des ersten Angebots, um Feldnamen zu pruefen."""
    ort = request.args.get("ort", DEFAULT_ORT)
    land = request.args.get("land", DEFAULT_LAND)
    try:
        with LidlPlus(country=land) as lidl:
            store, result = lidl.offers_for_store_search(ort)
        raw_offers = _get(result, "offers", default=[]) or []
        first = raw_offers[0] if raw_offers else None
        if first is not None and hasattr(first, "model_dump"):
            first_dump = first.model_dump(by_alias=True)
        elif first is not None and hasattr(first, "__dict__"):
            first_dump = first.__dict__
        else:
            first_dump = first
        return jsonify({
            "store": _get(store, "label", default=ort),
            "first_offer_type": type(first).__name__ if first else None,
            "first_offer": first_dump,
        })
    except Exception as e:  # noqa: BLE001
        log.exception("Lidl /raw fehlgeschlagen fuer ort=%s land=%s", ort, land)
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    # Nur fuer lokale Entwicklung. Auf Render startet gunicorn die App
    # (siehe Start Command in der README), dieser Block laeuft dort nicht.
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
