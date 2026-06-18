# =============================================================================
# Lidl-Angebote-Proxy fuer BEUTEPLAN
# -----------------------------------------------------------------------------
# Holt serverseitig die aktuellen Wochenangebote einer Lidl-Filiale ueber die
# inoffiziellen, login-freien Lidl-Plus-Endpunkte (Repo: EvickaStudio/lidl-
# discounts) und liefert sie CORS-offen als JSON an die Web-App.
#
# WARUM SERVER UND NICHT BROWSER:
#   Der Browser blockiert direkte Lidl-Zugriffe per CORS. Dieser Proxy laeuft
#   serverseitig (kein CORS) und gibt die Daten CORS-offen an die App weiter.
#
# SCHEMA, DAS DIE APP ERWARTET (pro Angebot):
#   {name, price, regularPrice, deal, unit, validFrom, validTo}
#   WICHTIG: price/regularPrice werden als €/kg interpretiert. Lidl liefert oft
#   Packungspreise + eine Einheit wie "1 kg = 4.32" -> daraus wird der kg-Preis
#   abgeleitet. Loses Obst/Gemuese ist bereits "x €/kg".
#
# Die genauen Quell-Feldnamen sind inoffiziell und koennen sich aendern.
# normalize() ist daher tolerant und durchsucht das Angebot breit.
# Zur Kontrolle:  /raw?ort=Wuerzburg  zeigt die Rohstruktur der ersten Angebote.
# =============================================================================

import os
import re
import time

from flask import Flask, request, jsonify
from flask_cors import CORS

# Aus dem geklonten Repo (liegt im selben Verzeichnis):
#   LidlPlus(country="DE") -> offers_for_store_search("Wuerzburg") -> (store, offers)
#   store.label ; offers.offers (Liste) ; offers.total_offers
from main import LidlPlus  # noqa: E402

app = Flask(__name__)
CORS(app)  # alle Origins erlaubt (Eigenbedarf). Bei Bedarf einschraenken.

_CACHE = {}            # (ort, land) -> (timestamp, payload)
_CACHE_TTL = 6 * 3600  # 6 h -> schont die Lidl-Endpunkte

_PER_KG = re.compile(r"(\d+[.,]?\d*)\s*€?\s*/\s*kg", re.I)
_EQ_KG = re.compile(r"1\s*kg\s*=\s*(\d+[.,]?\d*)", re.I)
_PCT = re.compile(r"-\s*\d{1,3}\s*%")


def _to_dict(o):
    """Macht aus einem Angebot (Objekt ODER dict) ein flaches dict."""
    if isinstance(o, dict):
        return o
    if hasattr(o, "__dict__") and vars(o):
        return {k: getattr(o, k) for k in vars(o)}
    if hasattr(o, "__slots__"):
        return {k: getattr(o, k, None) for k in o.__slots__}
    return {}


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"\d+[.,]?\d*", v.replace(",", "."))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for nk in ("name", "label", "text", "title", "value"):
                if isinstance(v.get(nk), str) and v[nk].strip():
                    return v[nk].strip()
    return None


def _first_num(d, keys):
    for k in keys:
        if k in d:
            n = _num(d.get(k))
            if n is not None:
                return n
            v = d.get(k)
            if isinstance(v, dict):
                for nk in ("price", "value", "amount", "gross"):
                    n = _num(v.get(nk))
                    if n is not None:
                        return n
    return None


def _eur_per_kg(*texts):
    for t in texts:
        if not isinstance(t, str):
            continue
        m = _PER_KG.search(t) or _EQ_KG.search(t)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                pass
    return None


def normalize(offer):
    d = _to_dict(offer)
    hay = " | ".join(str(v) for v in d.values() if isinstance(v, (str, int, float)))

    title = (_first_str(d, ["title", "name", "product", "productTitle", "fullTitle",
                            "keyfacts", "brand"])
             or _first_str(d, list(d.keys())))
    cur = _first_num(d, ["price", "offerPrice", "currentPrice", "discountedPrice",
                         "dealPrice", "priceValue", "salesPrice"])
    reg = _first_num(d, ["regularPrice", "oldPrice", "basePrice", "strikePrice",
                         "priceOld", "regular", "rrp"])
    unit = _first_str(d, ["unit", "basicPrice", "basePriceText", "packaging",
                          "pricePerUnitText", "packageInfo", "keyfacts"])
    deal = _first_str(d, ["discount", "deal", "promotion", "discountText",
                          "badge", "priceLabel"])

    # €/kg ableiten (App rechnet in €/kg)
    ppk = _eur_per_kg(unit, str(d.get("price")), hay)
    reg_ppk = _eur_per_kg(unit, str(d.get("regularPrice")), hay)
    price = ppk if ppk is not None else cur
    regular = reg_ppk if reg_ppk is not None else reg

    if not deal:
        m = _PCT.search(hay)
        if m:
            deal = m.group(0).replace(" ", "")
    if not deal and price and regular and regular > price:
        deal = "-%d%%" % round((1 - price / regular) * 100)

    return {
        "name": title,
        "price": price,
        "regularPrice": regular,
        "deal": deal,
        "unit": unit,
        "validFrom": _first_str(d, ["validFrom", "startDate", "startValidityDate", "from"]),
        "validTo": _first_str(d, ["validTo", "endDate", "endValidityDate", "to"]),
    }


def _fetch(ort, land):
    with LidlPlus(country=land) as lidl:
        store, result = lidl.offers_for_store_search(ort)
    raw = getattr(result, "offers", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("offers", [])
    raw = raw or []
    label = getattr(store, "label", None) or getattr(store, "name", None) or ort
    return label, raw


@app.route("/offers")
def offers():
    ort = request.args.get("ort", "Berlin")
    land = request.args.get("land", "DE")

    cached = _CACHE.get((ort, land))
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return jsonify(cached[1])

    try:
        label, raw = _fetch(ort, land)
        payload = {
            "store": label,
            "count": len(raw),
            "offers": [normalize(o) for o in raw],
        }
        _CACHE[(ort, land)] = (time.time(), payload)
        return jsonify(payload)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "offers": []}), 502


@app.route("/raw")
def raw():
    """Debug: zeigt die Rohstruktur der ersten 3 Angebote (zum Feldnamen-Pruefen)."""
    ort = request.args.get("ort", "Berlin")
    land = request.args.get("land", "DE")
    try:
        label, raw_offers = _fetch(ort, land)
        sample = []
        for o in raw_offers[:3]:
            sample.append(o if isinstance(o, dict) else _to_dict(o))
        return jsonify({"store": label, "count": len(raw_offers), "sample": sample})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502


@app.route("/")
def index():
    return jsonify({"ok": True, "use": "/offers?ort=Wuerzburg", "debug": "/raw?ort=Wuerzburg"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
