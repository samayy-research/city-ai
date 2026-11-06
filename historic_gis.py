"""Bounded parcel-polygon screening through the City's public map proxy.

Uses the same public alias, cookies and Referer as the map. No GIS token or
browser dependency is needed. Findings are research, never applicant facts.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.cookiejar
import difflib
import json
import re
import urllib.parse
import urllib.request

MAP_URL = 'https://maps.coj.net/landdevelopmentreview/'
PROXY = MAP_URL + 'duval.ashx?HTTPS://CMVXDWVZDA.QQQ/Z1'
SCHEMA_VERSION = 7
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


STREET_TYPES = {'ST', 'AVE', 'BLVD', 'RD', 'DR', 'LN', 'CT', 'PL', 'TER', 'PKWY', 'HWY', 'CIR', 'WAY'}
DIRECTIONS = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'}


def address_parts(value):
    words = normalized_address(value).split()
    if len(words) < 2 or not words[0].isdigit():
        return None
    number, body = words[0], words[1:]
    direction = body.pop(0) if body and body[0] in DIRECTIONS else ''
    if body and body[-1] in DIRECTIONS:
        trailing = body.pop()
        if direction and direction != trailing:
            return None
        direction = trailing
    suffix = body.pop() if body and body[-1] in STREET_TYPES else ''
    return (number, ' '.join(body), suffix, direction) if body else None


def select_address(location, features):
    """Select only a clearly distinct official address; never guess a street suffix.

Number, supplied direction and supplied suffix must agree. An omitted suffix can
be completed from the GIS; spelling changes remain explicitly unconfirmed.
"""
    query = address_parts(location)
    if not query:
        return {'address_match': 'not_found'}
    ranked = {}
    for feature in features:
        address = feature.get('attributes', {}).get('LONGNAME', '')
        if not isinstance(address, str):
            continue
        parts = address_parts(address)
        if not parts or parts[0] != query[0] or (query[2] and query[2] != parts[2]) or (query[3] and query[3] != parts[3]):
            continue
        score = difflib.SequenceMatcher(None, query[1], parts[1]).ratio()
        if score >= .78:
            ranked[normalized_address(address)] = score
    if not ranked:
        return {'address_match': 'not_found'}
    best = max(ranked.values())
    choices = sorted(address for address, score in ranked.items() if score == 1) if best == 1 else sorted(
        address for address, score in ranked.items() if best - score < .08)
    if len(choices) != 1:
        return {'address_match': 'ambiguous', 'candidate_addresses': choices[:5], 'address_confirmation_required': True}
    chosen = choices[0]
    return {'resolved_address': chosen, 'address_match': 'completed' if best == 1 else 'spelling_suggestion',
            'address_confirmation_required': best < 1, 'address_match_score': round(best, 3)}


def nearest_address(location, features):
    """Suggest the closest street number on the same, unambiguous street.

    This is address-number proximity, not a geographic distance measurement.
    Results always require confirmation before they can describe the project site.
    """
    query = address_parts(location)
    if not query:
        return {'address_match': 'not_found'}
    candidates = {}
    for feature in features:
        address = feature.get('attributes', {}).get('LONGNAME', '')
        if not isinstance(address, str):
            continue
        parts = address_parts(address)
        if not parts or parts[1] != query[1] or (query[2] and query[2] != parts[2]) or (query[3] and query[3] != parts[3]):
            continue
        distance = abs(int(parts[0]) - int(query[0]))
        if distance <= 100:
            candidates[normalized_address(address)] = (distance, parts[2:])
    if not candidates:
        return {'address_match': 'not_found'}
    # An omitted suffix/direction must not let us choose a different street.
    if len({street for _, street in candidates.values()}) > 1:
        return {'address_match': 'ambiguous', 'candidate_addresses': sorted(candidates)[:5],
                'address_confirmation_required': True}
    distance = min(value[0] for value in candidates.values())
    choices = sorted(address for address, value in candidates.items() if value[0] == distance)
    if len(choices) != 1:
        return {'address_match': 'ambiguous', 'candidate_addresses': choices[:5],
                'address_confirmation_required': True}
    return {'resolved_address': choices[0], 'address_match': 'nearest',
            'address_confirmation_required': True, 'street_number_difference': distance}


def find_parcels(opener, location, number):
    address = normalized_address(location)
    where = "RE = '" + number[1] + ' ' + number[2] + "'" if number else "LONGNAME = '" + address.replace("'", "''") + "'"
    params = {'where': where, 'outFields': 'RE,LONGNAME', 'returnGeometry': 'true', 'outSR': '3857'}
    parcels = _features(opener, 1, params)
    if parcels['features'] or number:
        addresses = sorted({feature.get('attributes', {}).get('LONGNAME', '') for feature in parcels['features']})
        return parcels, {'address_match': 'parcel' if number else 'exact',
                         'resolved_address': addresses[0] if len(addresses) == 1 else '',
                         'address_confirmation_required': False}
    parts = address_parts(location)
    if not parts:
        return parcels, {'address_match': 'not_found'}
    # One bounded number-based query tolerates misspelled first street words too.
    # _json rejects exceededTransferLimit: partial searches cannot prove uniqueness.
    candidates = _features(opener, 1, {'where': "LONGNAME LIKE '" + parts[0] + " %'",
        'outFields': 'RE,LONGNAME', 'returnGeometry': 'false', 'resultRecordCount': 201})['features']
    if len(candidates) > 200:
        return parcels, {'address_match': 'too_many', 'address_confirmation_required': True}
    match = select_address(location, candidates)
    if match.get('address_match') == 'not_found':
        # Bound the fallback to the same street and a nearby number range.
        # Do not override an ambiguous same-number spelling/suffix match.
        lower = max(1, int(parts[0]) - 100)
        upper = int(parts[0]) + 100
        street = parts[1].replace("'", "''")
        numbers = ' OR '.join("LONGNAME LIKE '" + str(value) + " %'" for value in range(lower, upper + 1))
        nearby = _features(opener, 1, {'where':
            "LONGNAME LIKE '% " + street + "%' AND (" + numbers + ")",
            'outFields': 'RE,LONGNAME', 'returnGeometry': 'false', 'resultRecordCount': 201})['features']
        if len(nearby) > 200:
            return parcels, {'address_match': 'too_many', 'address_confirmation_required': True}
        match = nearest_address(location, nearby)
    if not match.get('resolved_address'):
        return parcels, match
    params['where'] = "LONGNAME = '" + match['resolved_address'].replace("'", "''") + "'"
    return _features(opener, 1, params), match


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
        parcels, address_match = find_parcels(opener, location, number)
        result.update(address_match)
        if not parcels['features']:
            if result.get('address_match') == 'ambiguous':
                return {**result, 'reason':'More than one official address could match: ' + '; '.join(result['candidate_addresses']) + '. Confirm the street address or provide the real estate number.'}
            return {**result, 'reason':'No unique parcel address could be established. Confirm the street address or provide the real estate number.'}
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
        if result.get('address_match') == 'nearest':
            result['reason'] = 'Closest available street-number match: ' + result['resolved_address'] + '; this is a nearby address, not a verified match to the project site. ' + result['reason']
        if result.get('address_match') in {'completed', 'spelling_suggestion'}:
            result['reason'] = 'Screened the official GIS address ' + result['resolved_address'] + '. ' + result['reason']
        if result.get('address_confirmation_required'):
            result['status'] = 'requires_review'
            result['historic_status'] = 'requires_review'
            result['reason'] = 'Please confirm that you mean ' + result['resolved_address'] + '; these screening results are for that suggested address. ' + result['reason']
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, 'reason':'The City historic GIS check is unavailable or incomplete. Historic Preservation should confirm status.'}


def current_historic(project):
    finding = project.get('research', {}).get('historic', {})
    return finding if finding.get('query') == project['fields']['location']['value'] else {}
