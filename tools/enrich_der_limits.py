"""Enrich existing radar points from DER-SP's public radar consultation.

Never infer a limit from the road's general limit or a nearby, opposite-direction
camera. Keep the input coordinates and routing associations unchanged.
"""
import argparse
import collections
import copy
import datetime
import hashlib
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

SOURCE_URL = "https://dersp.der.sp.gov.br/Upload/XML/radares.xml"
PAGE_URL = "https://dersp.der.sp.gov.br/WebSite/MalhaRodoviaria/PesquisaRadar.aspx"
MAX_GAP_METERS = 100
MAX_KM_DIFFERENCE = 0.025


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


def road(text):
    match = re.search(r"\b(SP[A-Z]?)[ -]*(\d{3})(?:\s*/\s*(\d{3}))?\b", normalized(text))
    return "".join(match.groups(default="")) if match else None


def kilometer(text):
    match = re.search(r"\bKM\s*(\d+)(?:\+(\d{3})|[.,](\d+))?", normalized(text))
    if not match:
        return None
    return float(match[1]) + (int(match[2]) / 1000 if match[2] else float("0." + match[3]) if match[3] else 0)


def directions(text):
    value = normalized(text)
    names = {"NORTE": "N", "SUL": "S", "LESTE": "L", "OESTE": "O"}
    return set(re.findall(r"\b([NLSO])\s*-", value)) | {names[x] for x in re.findall(r"\b(NORTE|SUL|LESTE|OESTE)\b", value)}


def light_limit(raw):
    # DER lists light / heavy; CONTRAN 798 art. 12 includes motorcycles in light.
    match = re.fullmatch(r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*km/h\s*", raw, re.I)
    if match and all(5 <= int(n) <= 160 and int(n) % 5 == 0 for n in match.groups()):
        return int(match[1])
    return None


def usable(row, as_of):
    return (
        row.get("TipoRadar") in {"Controlador", "Redutor"}
        and row.get("rad_ativo") == "Ativo"
        and row.get("trc_em_operacao") == "true"
        and row.get("rad_dt_cadastro", "9999")[:10] <= as_of
        and light_limit(row.get("rad_vel_regul", "")) is not None
    )


def location_matches(point, row):
    try:
        km = kilometer(point["description"])
        if road(point["description"]) != road(row["Rodovia"]) or km is None:
            return False
        if abs(km - float(row["KmInicial"])) > MAX_KM_DIFFERENCE:
            return False
        bearing = directions(point["scope"])
        if not bearing or bearing != directions(row["Orientacao"]):
            return False
        lat, lon = float(row["Latitude"]), float(row["Longitude"])
        gap = math.hypot((lat - point["lat"]) * 111195, (lon - point["lon"]) * 111195 * math.cos(math.radians(lat)))
        return gap <= MAX_GAP_METERS
    except (KeyError, ValueError):
        return False


def enrich(catalog, rows, as_of):
    result = copy.deepcopy(catalog)
    eligible = [r for r in rows if usable(r, as_of)]
    by_id, by_road = collections.defaultdict(list), collections.defaultdict(list)
    for row in eligible:
        by_id[row.get("tra_numero_der", "").lstrip("0")].append(row)
        by_road[road(row.get("Rodovia", ""))].append(row)
    applied = []
    for point in result["points"]:
        if point["source"] == "DER":
            candidates = by_id[point["id"].removeprefix("DER-").lstrip("0")]
            method = "DER equipment ID + road/km/direction/coordinates"
        elif point["source"] == "ARTESP":
            candidates = by_road[road(point["description"])]
            method = "ARTESP road/km/direction/coordinates matched to DER"
        else:
            continue
        matches = [r for r in candidates if location_matches(point, r)]
        limits = {light_limit(r["rad_vel_regul"]) for r in matches}
        if len(limits) != 1:
            continue
        limit = limits.pop()
        if any(point.get(k) not in (None, limit) for k in ("car", "moto")):
            continue
        point["car"] = point["moto"] = limit
        applied.append({"id": point["id"], "limitCarAndMoto": limit, "method": method,
                        "description": point["description"], "scope": point["scope"],
                        "evidence": matches})
    return result, applied


def read_rows(path):
    return [{field.tag: field.text or "" for field in row} for row in ET.parse(path).getroot().iter("radares")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("xml", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    datetime.date.fromisoformat(args.date)
    original = json.loads(args.catalog.read_text("utf8"))
    if args.date < original["date"] or args.revision < 1:
        raise ValueError("Date/revision would downgrade the catalogue")
    result, applied = enrich(original, read_rows(args.xml), args.date)
    result.update(date=args.date, revision=args.revision)
    audit = {"source": SOURCE_URL, "consultation": PAGE_URL, "retrievedOn": args.date,
             "sourceSha256": hashlib.sha256(args.xml.read_bytes()).hexdigest(),
             "matched": len(applied), "bySource": dict(collections.Counter(p["id"].split("-")[0] for p in applied)),
             "points": applied}
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), "utf8")
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), "utf8")
    print(json.dumps({k: v for k, v in audit.items() if k != "points"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
