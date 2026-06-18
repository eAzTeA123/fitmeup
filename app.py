# =============================================================================
# Lidl-Angebote-Proxy fuer Beuteplan — Render-Edition
# =============================================================================
import logging
import os
import sys
import threading
import time

# --- WICHTIG: Arbeitsverzeichnis zum Python-Pfad hinzufügen ---
# (seit Python 3.11 ist das aktuelle Verzeichnis nicht mehr automatisch dabei)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("lidl_proxy")

try:
    from main import LidlPlus
except ImportError as exc:
    raise ImportError(
        "main.py (LidlPlus-Client) fehlt. Lokal: siehe README.md Schritt 1. "
        "Auf Render: stelle sicher, dass der Build Command 'bash build.sh' ist."
    ) from exc

# --------------------------------------------------------------------------
# Konfiguration ueber Environment-Variablen (alle optional)
# --------------------------------------------------------------------------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", 6 * 3600))  # 6h Default
DEFAULT_ORT = os.environ.get("DEFAULT_ORT", "Berlin")
DEFAULT_LAND = os.environ.get("DEFAULT_LAND", "DE")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else "*"}})

_CACHE = {}              # (ort, land) -> (timestamp, payload)
_CACHE_LOCK = threading.Lock()

def _get(o, *keys, default=None):
    """Holt einen Wert robust aus dict ODER Objekt."""
    for k in keys:
        if isinstance(o, dict) and k in o and o[k] is not None:
            return o[k]
        if hasattr(o, k) and getattr(o, k) is not None:
            return getattr(o, k)
    return default

def _clean(v):
    if isinstance(v, str) and v.strip() in ("", "-"):
        return None
    return v

def normalize(offer):
    """Bringt ein Lidl-Angebot in das von der App erwartete Schema."""
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
    """Tatsaechlicher Lidl-Call (nicht gecacht)."""
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
    return jsonify({"status": "ok"})

@app.route("/offers")
def offers():
    ort = request.args.get("ort", DEFAULT_ORT)
    land = request.args.get("land", DEFAULT_LAND)
    key = (ort, land)

    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return jsonify(cached[1])

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return jsonify(cached[1])
        try:
            payload = _fetch_offers(ort, land)
        except Exception as e:
            log.exception("Lidl-Abruf fehlgeschlagen fuer ort=%s land=%s", ort, land)
            return jsonify({"error": str(e), "offers": []}), 502
        _CACHE[key] = (time.time(), payload)
        return jsonify(payload)

@app.route("/raw")
def raw():
    """Debug: zeigt die rohe Struktur des ersten Angebots."""
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
    except Exception as e:
        log.exception("Lidl /raw fehlgeschlagen fuer ort=%s land=%s", ort, land)
        return jsonify({"error": str(e)}), 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
