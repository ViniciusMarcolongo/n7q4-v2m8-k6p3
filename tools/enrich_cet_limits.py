"""Read confirmed CET vehicle limits without guessing lane-dependent values."""
import collections
import copy
import datetime
import math
import re


def active_rows(rows, date):
    end = datetime.datetime.fromisoformat(date).replace(hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc).timestamp() * 1000
    return [r for r in rows if r.get("ATIVAÇÃO") and r["ATIVAÇÃO"] <= end and not r.get("DESATIVAÇÃO")]


def confirmed_limit(row):
    codes = set((row.get("ENQUADRAMENTOS") or "").split(","))
    if "V" not in codes:
        return None
    value = (row.get("VELOCIDADE") or "").strip()
    single = re.fullmatch(r"(\d{2,3}) km/h", value)
    if single:
        return int(single[1])
    # CET NT 253 distinguishes light/heavy for these pairs. Local carriageway
    # values 60/50 and 60/50/50 also depend on lane and are deliberately excluded.
    if value in {"90/60 km/h", "70/60 km/h"} and re.search(r"\bMARG(?:INAL)?\.?\s+(?:TIETE|PINHEIROS)\b", row["DESCRIÇÃO DO LOCAL"]):
        return int(value[:2])
    return None


def enrich(catalog, rows, date):
    result = copy.deepcopy(catalog)
    index = collections.defaultdict(list)
    for row in active_rows(rows, date):
        index[str(row["CÓDIGO LOCAL"])].append(row)
    evidence = []
    for point in result["points"]:
        if point["source"] != "CET" or point["car"] is not None:
            continue
        matches = []
        for row in index[point["id"].removeprefix("CET-")]:
            try:
                lat, lon = float(row["LATITUDE"]), float(row["LONGITUDE"])
                distance = math.hypot((point["lat"] - lat) * 111195, (point["lon"] - lon) * 111195 * math.cos(math.radians(lat)))
            except (TypeError, ValueError):
                continue
            if distance <= 25 and row["DESCRIÇÃO DO LOCAL"] == point["description"]:
                matches.append(row)
        values = {confirmed_limit(row) for row in matches}
        if len(values) != 1 or None in values:
            continue
        limit = values.pop()
        restricted = any("MT" in row["ENQUADRAMENTOS"].split(",") for row in matches)
        point["car"] = limit
        if not restricted:
            point["moto"] = limit
        evidence.append({"id": point["id"], "car": limit, "moto": point["moto"], "motorcycleRestriction": restricted, "rows": matches})
    return result, evidence
