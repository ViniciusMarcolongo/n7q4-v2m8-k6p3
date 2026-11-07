"""Merge official São Paulo datasets. Inconclusive matches stay visible, without voice."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import struct
import unicodedata
from pathlib import Path
from enrich_cet_limits import confirmed_limit



def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value).upper()) if not unicodedata.combining(c))


def point(source, code, lat, lon, description, types, scope, speed=None):
    if not -26 < lat < -19 or not -54 < lon < -43:
        return None
    return dict(id=source+"-"+str(code),lat=lat,lon=lon,description=description,types=types,
                source=source,scope=scope,car=speed,moto=speed if source=="CET" else None,
                segment=None,directions="",offset=0.0)


def cet(rows, as_of):
    for r in rows:
        if r["DESATIVAÇÃO"] or not r["ATIVAÇÃO"] or r["ATIVAÇÃO"] > as_of.timestamp()*1000:
            continue
        codes = set((r["ENQUADRAMENTOS"] or "").split(","))
        kinds = []
        if "V" in codes:
            kinds.append("Radar de velocidade")
        if "A" in codes:
            kinds.append("Fiscalização de semáforo")
        if codes & {"EXD","EXE"}:
            kinds.append("Fiscalização de faixa exclusiva")
        if not kinds:
            continue
        try:
            lat,lon=(float(str(r[k]).replace(",",".")) for k in ("LATITUDE","LONGITUDE"))
        except ValueError:
            continue
        speed = confirmed_limit(r)
        desc = r["DESCRIÇÃO DO LOCAL"] or ""
        value = point("CET",r["CÓDIGO LOCAL"],lat,lon,desc,
                      ", ".join(kinds),desc,speed)
        if value and "MT" in codes:
            value["moto"] = None
        yield value


def der(path, as_of):
    import openpyxl
    for r in list(openpyxl.load_workbook(path,read_only=True).active.values)[1:]:
        kind,code,ref,_,km,lanes,city,_,_,start,end,coord = r
        if kind not in {"CEV","CEC","REV","REC"} or not isinstance(start,dt.datetime) or start.date()>as_of.date():
            continue
        if end and (not isinstance(end,dt.datetime) or end.date()<=as_of.date()):
            continue
        nums = re.findall(r"-\d+\.\d+",str(coord))
        if len(nums)!=2:
            continue
        yield point("DER",code,*map(float,nums),f"{ref} · km {km} · {city}",
                    "Radar de velocidade",str(lanes or ""))


def antt(path):
    for r in csv.DictReader(path.read_text("cp1252").splitlines(),delimiter=";"):
        if r["uf"]!="SP" or r["situacao"].strip().lower()!="ativo" or r["data_da_inativacao"].strip():
            continue
        if r["tipo_de_radar"] not in {"Controlador","Redutor"}:
            continue
        lat,lon=(float(r[k].replace(",",".")) for k in ("latitude","longitude"))
        key=hashlib.sha256(("|".join(r[k] for k in ("rodovia","km_m","sentido","latitude","longitude"))).encode()).hexdigest()[:16]
        speed=int(float(r["velocidade_leve"].replace(",","."))) if r["velocidade_leve"] else None
        value = point("ANTT",key,lat,lon,f"{r['rodovia']} · km {r['km_m']} · {r['Municipio']}",
                      "Radar de velocidade",r["sentido"],speed)
        if value:
            value["moto"] = speed
        yield value


def artesp(path):
    for feature in json.loads(path.read_text("utf8"))["features"]:
        props = feature["properties"]
        text = re.sub(r"<[^>]+>", " ", props.get("popupinfo") or "").replace("_", " ")
        upper = normalized(text).replace("_", " ")
        if re.search(r"INOPERANTE|DESATIVAD|REMOVID|A IMPLANTAR|SOMENTE AUTUACAO DE VEICULOS COMERCIAIS", upper):
            continue
        lon, lat = feature["geometry"]["coordinates"][:2]
        description = " · ".join([re.sub(r"\s+", " ", text).strip(), props.get("nm_mun") or "", props.get("lote") or ""])
        speed = re.search(r"(?:LIMITE|VELOCIDADE)[^\d]{0,20}(\d{2,3})\s*(?:KM|QUIL)", upper)
        value = point("ARTESP", props["objectid"], lat, lon, description, "Radar de velocidade", upper, int(speed[1]) if speed else None)
        if value:
            value["moto"] = value["car"]
            yield value


def geom_match(blob, lat, lon):
    nodes=list(struct.iter_unpack("<ii",blob))
    sy=111195.0
    sx=sy*math.cos(math.radians(lat))
    length=0.0
    nearest=None
    for (ay,ax),(by,bx) in zip(nodes,nodes[1:]):
        dx=(bx-ax)*sx/1e6; dy=(by-ay)*sy/1e6
        norm=math.hypot(dx,dy)
        if norm<0.01:
            continue
        x=(lon-ax/1e6)*sx; y=(lat-ay/1e6)*sy
        scale=max(0,min(1,(x*dx+y*dy)/(norm*norm)))
        gap=math.hypot(x-scale*dx, y-scale*dy)
        entry=(gap, length+scale*norm, math.degrees(math.atan2(dx,dy))%360)
        if nearest is None or gap<nearest[0]:
            nearest=entry
        length+=norm
    return nearest


def road_match(db, camera):
    lat,lon=camera["lat"],camera["lon"]
    row,col=math.floor(lat*50),math.floor(lon*50)
    cells=[(row+y)*20000+col+x for y in (-1,0,1) for x in (-1,0,1)]
    options=[]
    text=normalized(camera["description"])
    tags=set(re.findall(r"[A-Z0-9]+",text))
    for rid,way,name,ref,direction,blob in db.execute("""SELECT DISTINCT r.id,r.osm,r.name,r.ref,r.oneway,r.geometry
        FROM grid g JOIN roads r ON r.id=g.road WHERE cell IN (?,?,?,?,?,?,?,?,?)""",cells):
        if camera["source"]=="CET":
            useful=set(re.findall(r"[A-Z0-9]+",normalized(name)))-{"DA","DE","DO","DAS","DOS","RUA","AVENIDA","ESTRADA","DOUTOR","DR","R","AV","VIADUTO","RODOVIA"}
            if not useful or not (useful & tags) or len(useful & tags) / len(useful)<0.55:
                continue
        else:
            roadrefs=re.findall(r"(?:SP|BR|SPA)[ -]*(\d+)",text)
            if not any(re.search(r"(?<!\d)0*"+str(int(n))+r"(?!\d)",ref or "") for n in roadrefs):
                continue
        projection=geom_match(blob,lat,lon)
        if not projection or projection[0]>26:
            continue
        gap,offset,bearing=projection
        options.append((gap,rid,way,direction,offset,bearing))
    if not options:
        return
    best=min(options)
    if any(o[2]!=best[2] and o[0]<best[0]+5 for o in options):
        return
    _,rid,_,one,offset,bearing=best
    scope=normalized(camera["scope"])
    dirs=""
    if camera["source"] in {"DER", "ARTESP"}:
        axes={"N":0,"L":90,"S":180,"O":270}
        heads=[axes[k] for k in re.findall(r"\b([NLSO])\s*-",scope)]
        if camera["source"] == "ARTESP":
            heads=[{"NORTE":0,"LESTE":90,"SUL":180,"OESTE":270}[k] for k in re.findall(r"\b(NORTE|LESTE|SUL|OESTE)\b", scope)]
        for direction,course in (("+",bearing),("-",(bearing+180)%360)):
            if any(abs((course-heading+540)%360-180)<65 for heading in heads):
                dirs+=direction
    elif one!=0:
        dirs="+" if one==1 else "-"
    elif "AMBOS" in scope or ("B/C" in scope and "C/B" in scope):
        dirs="+-"
    if one==1: dirs=dirs.replace("-","")
    if one==-1: dirs=dirs.replace("+","")
    camera.update(segment=rid,offset=round(offset,1),directions=dirs)


def main():
    args=argparse.ArgumentParser()
    for name in ("map","cet","der","antt","output"):
        args.add_argument(name,type=Path)
    args.add_argument("--date",required=True)
    args.add_argument("--artesp",type=Path)
    a=args.parse_args()
    as_of=dt.datetime.fromisoformat(a.date).replace(hour=23,minute=59,tzinfo=dt.timezone.utc)
    with sqlite3.connect(a.map) as db:
        rows=list(cet(json.loads(a.cet.read_text("utf8")),as_of))+list(der(a.der,as_of))+list(antt(a.antt))
        if a.artesp: rows += list(artesp(a.artesp))
        result={}
        for r in rows:
            if r:
                road_match(db,r)
                result[r["id"]]=r
    records=list(result.values())
    with a.map.open("rb") as stream:
        geometry_sha256=hashlib.file_digest(stream,"sha256").hexdigest()
    data=dict(schema=1,date=a.date,mapId="SP-20261105-1",geometrySha256=geometry_sha256,points=records)
    a.output.parent.mkdir(exist_ok=True,parents=True)
    a.output.write_text(json.dumps(data,ensure_ascii=False,separators=(",",":")),"utf8")
    for src in ("CET","DER","ANTT","ARTESP"):
        pts=[p for p in records if p["source"]==src]
        print(src,len(pts),"com sentido identificado:",sum(bool(p["directions"]) for p in pts))


if __name__=="__main__":
    main()
