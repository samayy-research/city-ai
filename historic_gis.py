"""Bounded parcel-polygon screening through the City's public map proxy.

Uses the same public alias, cookies and Referer as the map. No GIS token or
browser dependency is needed. Findings are research, never applicant facts.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request

MAP_URL = 'https://maps.coj.net/landdevelopmentreview/'
PROXY = MAP_URL + 'duval.ashx?HTTPS://CMVXDWVZDA.QQQ/Z1'
SCHEMA_VERSION = 5
LAYERS = {40:'Archaeological Sensitivity', 41:'Cemeteries', 42:'National Register Historic Districts/Properties', 43:'Local Landmarks',
          44:'Local Historic Districts', 45:'Florida Master Site File - Historic Structures'}


def normalized_address(value):
    value = re.split(r'[,;]|\bJACKSONVILLE\b|\bFLORIDA\b', value.upper())[0]
    value = re.split(r'\b(?:SUITE|STE|UNIT|APT)\b|#', value)[0]
    value = re.sub(r'[^A-Z0-9\s\'-]', '', value)
    words = value.split()
    replacements = {'NORTH':'N','SOUTH':'S','EAST':'E','WEST':'W', 'STREET':'ST','AVENUE':'AVE',
                    'BOULEVARD':'BLVD','ROAD':'RD','DRIVE':'DR','LANE':'LN','COURT':'CT','PLACE':'PL',
                    'TERRACE':'TER','PARKWAY':'PKWY','HIGHWAY':'HWY'}
    words = [replacements.get(word, word) for word in words]
    if len(words) > 3 and words[1] in {'N','S','E','W','NE','NW','SE','SW'}:
        words.append(words.pop(1))
    return ' '.join(words)


def _json(opener, path='', parameters=None):
    request = urllib.request.Request(PROXY + path, data=urllib.parse.urlencode({'f':'json', **(parameters or {})}).encode(),
                                     headers={'Referer':MAP_URL, 'Content-Type':'application/x-www-form-urlencoded',
                                              'User-Agent':'JaxProjectAdvisor/1.0 (public GIS screening)'})
    with opener.open(request, timeout=8) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname != 'maps.coj.net':
            raise ValueError('Unsupported GIS redirect')
        raw = response.read(6_000_001)
    if len(raw) > 6_000_000:
        raise ValueError('GIS response too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('error') or data.get('exceededTransferLimit'):
        raise ValueError('GIS query failed or returned incomplete results')
    return data


def _features(opener, layer, parameters):
    result = _json(opener, f'/{layer}/query', parameters)
    if not isinstance(result.get('features'), list):
        raise ValueError('Missing GIS features')
    return result


def screening_checks(parcels):
    """Keep each map category independent; only High archaeology is flagged."""
    rows=[]
    for layer,title in LAYERS.items():
        checks=[check for parcel in parcels for check in parcel.get('checks',[]) if check.get('layer')==layer]
        found=any(any(match.get('MODEL','').strip().casefold()=='high' for match in check.get('matches',[])) if layer==40 else check.get('count',0)>0 for check in checks)
        complete=bool(parcels) and len(checks)==len(parcels) and all(check.get('query_status')=='success' for check in checks)
        rows.append({'layer':layer,'title':title + ' (High only)' if layer==40 else title,
                     'status':'identified' if found else 'not_identified' if complete else 'requires_review',
                     'complete':complete})
    return rows


def has_historic_flag(finding):
    return (finding.get('local_status') == 'designated'
            or any(row.get('status') == 'identified' for row in finding.get('screening_checks',[]))
            or any(level.casefold() == 'high' for level in finding.get('archaeology',{}).get('levels',[]))
            or any(check.get('layer') in (41,42,43,44,45) and check.get('count',0)>0 for parcel in finding.get('parcels',[]) for check in parcel.get('checks',[])))


def lookup_historic(location):
    result = {'query':location, 'source_url':MAP_URL, 'source_title':'City historic GIS screening',
              'schema_version':SCHEMA_VERSION,
              'historic_flag':False, 'historic_status':'requires_review',
              'screening_checks':screening_checks([]),
              'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'status':'requires_review', 'local_status':'requires_review', 'parcels':[]}
    number = re.fullmatch(r'\s*(\d{6})[- ]?(\d{4})\s*', location)
    address = normalized_address(location)
    if not number and not re.match(r'^\d+\s+[A-Z]', address):
        return {**result, 'reason':'A street address or real estate number is needed for the historic map check.'}
    where = "RE = '" + number[1] + ' ' + number[2] + "'" if number else "LONGNAME = '" + address.replace("'", "''") + "'"
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(urllib.request.Request(MAP_URL, headers={'User-Agent':'JaxProjectAdvisor/1.0'}), timeout=8) as response:
            if urllib.parse.urlsplit(response.geturl()).hostname != 'maps.coj.net':
                raise ValueError('Unsupported map redirect')
            response.read(1000)
        metadata = _json(opener)
        names = {layer['id']:layer['name'] for layer in metadata.get('layers', [])}
        if names.get(1) != 'Parcels' or any(names.get(key) != name for key, name in LAYERS.items()):
            raise ValueError('City layer configuration changed')
        parcels = _features(opener, 1, {'where':where,'outFields':'RE,LONGNAME','returnGeometry':'true','outSR':'3857'})
        if not parcels['features']:
            return {**result, 'reason':'No exact parcel match was found. Confirm the address or provide the real estate number.'}
        if len(parcels['features']) > 4:
            return {**result, 'reason':'The address matches too many parcels for a reliable automatic check.'}

        def check(parcel, layer):
            geometry = parcel.get('geometry')
            if not isinstance(geometry, dict) or not geometry.get('rings'):
                return {'layer':layer,'title':LAYERS[layer],'query_status':'failed'}
            try:
                hits = _features(opener, layer, {'where':'1=1','geometry':json.dumps(geometry),
                    'geometryType':'esriGeometryPolygon','inSR':'3857','spatialRel':'esriSpatialRelIntersects',
                    'outFields':'*','returnGeometry':'false'})['features']
                if len(hits) > 20:
                    raise ValueError('Too many historic records')
                matches = [{key:str(value)[:500] for key,value in hit.get('attributes', {}).items()
                            if value is not None and any(word in key.upper() for word in ('NAME','DISTRICT','SITEID','ORDINANCE','ADDRESS','LANDMARK','MODEL'))}
                           for hit in hits]
                return {'layer':layer,'title':LAYERS[layer],'query_status':'success','count':len(hits),'matches':matches}
            except (OSError, ValueError, KeyError, TypeError):
                return {'layer':layer,'title':LAYERS[layer],'query_status':'failed'}

        with ThreadPoolExecutor(max_workers=8) as executor:
            jobs = [(parcel, [executor.submit(check, parcel, layer) for layer in LAYERS]) for parcel in parcels['features']]
            for parcel, futures in jobs:
                checks = [future.result() for future in futures]
                local = [item for item in checks if item['layer'] in (43,44)]
                status = 'designated' if any(item.get('count',0) for item in local) else 'not_identified' if all(item['query_status']=='success' for item in local) else 'requires_review'
                result['parcels'].append({'parcel':parcel['attributes'].get('RE',''),
                    'address':parcel['attributes'].get('LONGNAME',''), 'local_status':status, 'checks':checks})
        statuses = {parcel['local_status'] for parcel in result['parcels']}
        result['screening_checks'] = screening_checks(result['parcels'])
        result['local_status'] = statuses.pop() if len(statuses)==1 else 'requires_review'
        complete = all(check['query_status']=='success' for parcel in result['parcels'] for check in parcel['checks'])
        contextual = any(check.get('count',0) for parcel in result['parcels'] for check in parcel['checks'] if check['layer'] in (42,45))
        archaeology_checks = [check for parcel in result['parcels'] for check in parcel['checks'] if check['layer']==40]
        levels = sorted({match['MODEL'].strip() for check in archaeology_checks for match in check.get('matches',[]) if match.get('MODEL','').strip()})
        high_sensitivity = any(level.casefold() == 'high' for level in levels)
        cemetery_found = any(row['layer']==41 and row['status']=='identified' for row in result['screening_checks'])
        result['archaeology'] = {'status':'identified' if levels else 'not_identified' if all(check['query_status']=='success' for check in archaeology_checks) else 'requires_review',
                                 'flagged':high_sensitivity,
                                 'levels':levels, 'complete':all(check['query_status']=='success' for check in archaeology_checks),
                                 'source_url':MAP_URL+'images/HELP_LayerInformation.pdf'}
        result['historic_flag'] = has_historic_flag(result)
        result['historic_status'] = 'flagged' if result['historic_flag'] else 'not_identified' if complete else 'requires_review'
        result['status'] = 'requires_review' if len(result['parcels'])>1 or not complete or high_sensitivity or cemetery_found or (contextual and result['local_status']!='designated') else result['local_status']
        result['reason'] = ('Multiple parcels match; all were checked. Confirm the project parcel. ' if len(result['parcels'])>1 else '') + (
            'The parcel intersects a mapped local historic designation; City staff confirm requirements.' if result['local_status']=='designated' else
            'No local historic district or landmark intersection was identified in the queried parcels.' if result['local_status']=='not_identified' else
            'Local historic status could not be established reliably.')
        if contextual:
            result['reason'] += ' National Register or historic-inventory records were also found; these alone do not establish a local COA requirement.'
        if high_sensitivity:
            result['reason'] += ' The parcel intersects a High archaeological sensitivity zone. This is a predictive model, not a confirmed archaeological site or local historic designation; City staff should confirm implications for any ground disturbance.'
        if cemetery_found:
            result['reason'] += ' A mapped cemetery feature intersects the parcel; confirm its relevance to the proposed work with City staff.'
        if not complete:
            result['reason'] += ' Some historic layers could not be checked; those findings remain unresolved.'
        if result['historic_flag']:
            result['reason'] = 'Historic screening flagged: ' + '; '.join(row['title'] for row in result['screening_checks'] if row['status']=='identified') + '. ' + result['reason']
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, 'reason':'The City historic GIS check is unavailable or incomplete. Historic Preservation should confirm status.'}


def current_historic(project):
    finding = project.get('research', {}).get('historic', {})
    return finding if finding.get('query') == project['fields']['location']['value'] else {}
