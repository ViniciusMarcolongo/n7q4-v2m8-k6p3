"""Apply dated, site-specific reviews with provenance. Unknown never excludes motorcycles."""
import copy
import datetime as dt
from urllib.parse import urlsplit

IDENTITY = ('id', 'source', 'lat', 'lon', 'description', 'scope', 'segment', 'directions', 'offset')
VEHICLES = {'UNKNOWN', 'CAR_AND_MOTORCYCLE', 'CAR_ONLY'}
CAPTURE = {'UNKNOWN', 'FRONT', 'REAR', 'BOTH'}


def validate(value):
    if value.get('vehicles') not in VEHICLES or value.get('capture') not in CAPTURE:
        raise ValueError('Unknown enforcement classification')
    if value.get('reviewSource', 'OFFICIAL') not in {'OFFICIAL', 'USER'}:
        raise ValueError('Unknown review source')
    url = urlsplit(value.get('sourceUrl', ''))
    if url.scheme != 'https' or not url.hostname or url.username or not value.get('sourceName'):
        raise ValueError('Review must identify its source')
    dt.date.fromisoformat(value['checkedOn'])
    if len(value['sourceName']) > 160 or len(value['sourceUrl']) > 2000 or len(value.get('note', '')) > 1200:
        raise ValueError('Oversized review')


def apply_reviews(points, document, today, audit=None):
    if document.get('schema') != 1:
        raise ValueError('Unsupported review schema')
    report = audit if audit is not None else []
    reviews = {}
    for record in document['reviews']:
        identity, evidence = record['identity'], record['enforcement']
        if set(identity) != set(IDENTITY) or identity['id'] in reviews:
            raise ValueError('Incomplete or duplicate review identity')
        validate(evidence)
        reviews[identity['id']] = record
    result = copy.deepcopy(points)
    for point in result:
        # Rebuild from the reviewed ledger, never inherit a claim from a changed site.
        point.pop('enforcement', None)
        record = reviews.get(point['id'])
        if record is None:
            continue
        if any(point.get(key) != record['identity'][key] for key in IDENTITY):
            report.append(dict(id=point['id'], reason='Enforcement review identity changed; motorcycle alerts retained'))
            continue
        evidence = copy.deepcopy(record['enforcement'])
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(evidence['checkedOn'])).days
        if not 0 <= age <= 180:
            evidence.update(vehicles='UNKNOWN', capture='UNKNOWN')
            report.append(dict(id=point['id'], reason='Enforcement review expired or future-dated; motorcycle alerts retained'))
        point['enforcement'] = evidence
    return result
