"""Daily, fail-closed publication from public CET, DER, ANTT and ARTESP sources.

Only data files are distributed. No executable code is downloaded by the app.
New/repositioned cameras remain visible without spoken alerts until their road
and travel direction have been reviewed with the matching approach network.
"""
import collections
import copy
import datetime as dt
import hashlib
import json
import math
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_catalog
import cet_data
import enrich_der_limits as der

BASE = 'https://raw.githubusercontent.com/ViniciusMarcolongo/sp-reference-data/main/'
ANTT = 'https://dados.antt.gov.br/api/3/action/package_show?id=radar'
ARTESP = 'https://mapas.semil.sp.gov.br/server/rest/services/Hosted/ARTESP_Pontos_rodovias/FeatureServer/0/query'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')


def fetch(url, maximum=32_000_000):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('HTTPS required')
    request = urllib.request.Request(url, headers={'User-Agent': 'RadarSP-Data/0.9 (public-data refresh)'})
    with urllib.request.urlopen(request, timeout=90) as response:
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError('Source exceeds size limit')
    return content


def same_location(a, b):
    gap = math.hypot((a['lat']-b['lat'])*111195, (a['lon']-b['lon'])*111195*math.cos(math.radians(a['lat'])))
    if gap > 2:
        return False
    if a['source'] in ('DER', 'ARTESP'):
        return (der.road(a['description']) == der.road(b['description'])
                and der.kilometer(a['description']) == der.kilometer(b['description'])
                and der.directions(a['scope']) == der.directions(b['scope']))
    return build_catalog.normalized(a['scope']) == build_catalog.normalized(b['scope'])


def associate(points, old):
    known = {p['id']: p for p in old}
    for p in points:
        previous = known.get(p['id'])
        if previous and same_location(p, previous):
            for key in ('segment', 'directions', 'offset'):
                p[key] = previous[key]
    return points


def parse_der(rows, previous, today, review=None):
    review = review if review is not None else []
    by_id = {p['id'].removeprefix('DER-').lstrip('0'): p for p in previous if p['source']=='DER'}
    result = {}
    invalid_locations = []
    for r in rows:
        if (r.get('TipoRadar') not in ('Controlador', 'Redutor') or r.get('rad_ativo') != 'Ativo'
                or r.get('trc_em_operacao') != 'true' or r.get('rad_dt_cadastro', '9999')[:10] > today):
            continue
        code = r.get('tra_numero_der', '').strip().lstrip('0')
        if not code or code == '0':
            raise ValueError('DER equipment has no stable identity')
        lat, lon = float(r['Latitude']), float(r['Longitude'])
        p = build_catalog.point('DER', code, lat, lon,
            f"{r['Rodovia']} · km {float(r['KmInicial']):g} · {r['Municipio']}",
            'Radar de velocidade', r['Orientacao'], der.light_limit(r.get('rad_vel_regul', '')))
        if p is None:
            invalid_locations.append(code)
            continue
        old = by_id.get(code)
        p['id'] = old['id'] if old else 'DER-' + code
        p['moto'] = p['car']
        if old:
            if der.location_matches(old, r):
                # Retain reviewed carriageway coordinates; XML is also known to
                # move points onto frontage/opposite roads. Update explicit limits.
                p['lat'], p['lon'] = old['lat'], old['lon']
            else:
                review.append(dict(id=old['id'], reason='DER position/road/direction differs from reviewed point'))
                p = copy.deepcopy(old)
        if p['id'] in result and result[p['id']] != p:
            raise ValueError('Conflicting DER equipment identity')
        result[p['id']] = p
    if len(invalid_locations) > 20:
        raise ValueError('Too many invalid DER locations')
    if invalid_locations:
        print('DER: coordinates rejected for equipment ' + ', '.join(invalid_locations))
        review.extend(dict(id='DER-'+code, reason='Invalid source coordinates') for code in invalid_locations)
    for code, old in by_id.items():
        if old['id'] in result:
            continue
        candidates = [r for r in rows if r.get('tra_numero_der','').lstrip('0') == code and der.location_matches(old,r)]
        disabled = candidates and all((r.get('rad_ativo') not in ('',None,'Ativo') or r.get('trc_em_operacao')=='false') for r in candidates)
        if not disabled:
            result[old['id']] = copy.deepcopy(old)
            review.append(dict(id=old['id'], reason='No matching explicit deactivation; retained last reviewed point'))
    return list(result.values())


def artesp_data():
    def query(**params):
        return json.loads(fetch(ARTESP + '?' + urllib.parse.urlencode(params)))
    ids = query(where="tipo='Radar'", returnIdsOnly='true', f='json')
    wanted = ids['objectIds']
    if not 100 <= len(wanted) <= 10000 or len(set(wanted)) != len(wanted):
        raise ValueError('Unexpected ARTESP size')
    features = []
    for offset in range(0, len(wanted), 300):
        batch = query(objectIds=','.join(map(str, wanted[offset:offset+300])), outFields='*', outSR=4326, f='geojson')
        if batch.get('exceededTransferLimit'):
            raise ValueError('ARTESP truncated response')
        features.extend(batch['features'])
    if {f['properties']['objectid'] for f in features} != set(wanted):
        raise ValueError('ARTESP incomplete response')
    return {'type':'FeatureCollection', 'features':features}


def antt_url(metadata):
    candidates = []
    for r in metadata['result']['resources']:
        url = r['url']
        match = re.search(r'dados-dos-radares(\d{1,2})_(\d{4})\.csv$', url)
        if match and urllib.parse.urlsplit(url).hostname == 'dados.antt.gov.br':
            candidates.append(((int(match[2]), int(match[1])), url))
    if not candidates:
        raise ValueError('ANTT CSV not found')
    return max(candidates)[1]


def validate(points, previous):
    if len({p['id'] for p in points}) != len(points):
        raise ValueError('Duplicate radar IDs')
    before = collections.Counter(p['source'] for p in previous)
    after = collections.Counter(p['source'] for p in points)
    for source, count in before.items():
        # Schema changes and partial exports must not silently erase a source.
        if not count*.85 <= after[source] <= count*1.35:
            raise ValueError(f'{source}: unusual count change {count} -> {after[source]} requires review')
    for p in points:
        if not -26 < p['lat'] < -19 or not -54 < p['lon'] < -43:
            raise ValueError('Invalid coordinates')
        if any(p[k] is not None and (not isinstance(p[k], int) or not 5 <= p[k] <= 160) for k in ('car','moto')):
            raise ValueError('Invalid limit')
        if p['directions'] not in ('', '+', '-', '+-') or (p['directions'] and p['segment'] is None):
            raise ValueError('Invalid road association')
    return dict(after)


def deduplicate(points):
    # ARTESP's inventory overlaps DER. Remove only confirmed identical carriageways.
    official = [p for p in points if p['source'] != 'ARTESP']
    for p in points:
        if p['source'] != 'ARTESP':
            continue
        duplicate = any(p['segment'] is not None and p['directions'] and p['segment']==q['segment']
            and p['directions']==q['directions'] and abs(p['offset']-q['offset'])<12 for q in official)
        if not duplicate:
            matches = [q for q in official if q['source']=='DER'
                and not (p['directions'] and q['directions'] and p['segment']==q['segment'] and p['directions']!=q['directions'])
                and der.road(p['description']) is not None
                and der.road(p['description']) == der.road(q['description'])
                and der.kilometer(p['description']) is not None
                and der.kilometer(q['description']) is not None
                and abs(der.kilometer(p['description'])-der.kilometer(q['description'])) <= .1
                and der.directions(p['scope']) and der.directions(p['scope']) == der.directions(q['scope'])
                and math.hypot((p['lat']-q['lat'])*111195, (p['lon']-q['lon'])*101000) < 15]
            duplicate = bool(matches)
            if len(matches)==1 and p['directions'] and not matches[0]['directions']:
                official.remove(matches[0])
                duplicate = False
        if not duplicate:
            official.append(p)
    return sorted(official, key=lambda p:p['id'])


def file_info(root, path):
    content = path.read_bytes()
    return dict(url=BASE+path.relative_to(root).as_posix(), sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))


def publish(root, catalog, network):
    data = root/'data'
    data.mkdir(exist_ok=True, parents=True)
    content = encoded(catalog)
    path = data/f'catalog-{hashlib.sha256(content).hexdigest()}.json'
    path.write_bytes(content)
    manifest = dict(schema=1, minAppVersion=18, mapId=catalog['mapId'], geometrySha256=catalog['geometrySha256'],
        date=catalog['date'], revision=catalog['revision'], count=len(catalog['points']),
        catalog=file_info(root,path), network=file_info(root,network))
    (root/'latest.json').write_bytes(encoded(manifest))
    return manifest


def refresh(root):
    manifest = json.loads((root/'latest.json').read_text('utf8'))
    previous = json.loads((root/'data'/Path(manifest['catalog']['url']).name).read_text('utf8'))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    sources = {}
    review = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        def save(name, content, url):
            path = folder/name
            path.write_bytes(content)
            sources[name] = dict(url=url, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
            return path
        cet_rows = cet_data.rows()
        save('cet.json', encoded(cet_rows), cet_data.CET)
        der_rows = der.read_rows(save('der.xml', fetch(der.SOURCE_URL), der.SOURCE_URL))
        url = antt_url(json.loads(fetch(ANTT)))
        antt_path = save('antt.csv', fetch(url), url)
        artesp_path = save('artesp.json', encoded(artesp_data()), ARTESP)
        now = dt.datetime.now(dt.timezone.utc)
        points = [p for p in build_catalog.cet(cet_rows, now) if p]
        points += parse_der(der_rows, previous['points'], today, review)
        points += [p for p in build_catalog.antt(antt_path) if p]
        points += [p for p in build_catalog.artesp(artesp_path) if p
                   and 'MANUTENCAO' not in build_catalog.normalized(p['scope'])]
    points = associate(points, previous['points'])
    enriched, _ = der.enrich(dict(points=points), der_rows, today)
    points = deduplicate(enriched['points'])
    counts = validate(points, previous['points'])
    changed = encoded(points) != encoded(sorted(previous['points'], key=lambda p:p['id']))
    status = dict(checkedAt=dt.datetime.now(dt.timezone.utc).isoformat(), changed=changed, counts=counts, sources=sources, review=review)
    if changed:
        updated = copy.deepcopy(previous)
        updated.update(points=points, date=max(today, previous['date']),
            revision=previous.get('revision',0)+1 if today<=previous['date'] else 1)
        publish(root, updated, root/'data'/Path(manifest['network']['url']).name)
    # The daily audit also keeps the public scheduled repository active.
    (root/'status.json').write_bytes(encoded(status))
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    refresh(Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve())
