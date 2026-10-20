"""Live, bounded research of City guidance and official Duval property records.

No model-supplied URL is fetched. Property classification is external evidence, not
the applicant's confirmed suite use or a zoning/occupancy determination.
"""
from __future__ import annotations

import difflib
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from historic_gis import lookup_historic, SCHEMA_VERSION

PAO = "https://paopropertysearch.coj.net"
CITY = "https://www.jacksonville.gov"
PAGES = {
    "fees": ("City fee schedule", CITY + "/departments/finance/city-fees"),
    "historic": ("Historic Preservation — search by address", CITY + "/departments/planning-department/community-planning-division/historic-preservation-section/search-by-address"),
    "cou": ("COU and converting-use guidance", CITY + "/departments/public-works/building-inspection-division/certificates-of-use-and-converting-use-building-permits"),
    "bid": ("Building Inspection / current code edition", CITY + "/departments/public-works/building-inspection-division"),
    "forms": ("Current BID forms and checklists", CITY + "/departments/public-works/building-inspection-division/checklists,-forms-and-documents"),
    "site": ("Site development prerequisites", CITY + "/departments/public-works/development-services-division/review-group-(fka-dmg)/land-development-procedures-manual/site-development-plan-review"),
    "ldpm": ("Land Development Procedures Manual", CITY + "/departments/public-works/development-services-division/review-group-(fka-dmg)/land-development-procedures-manual"),
    "mobility": ("City mobility fee guidance", CITY + "/departments/planning-department/transportation-planning/concurrency-and-mobility-management-system-office"),
    "professionals": ("Converting-use plans and licensed professionals", CITY + "/departments/public-works/building-inspection-division/faqs"),
}
_cache = {}
_lock = threading.Lock()


class Page(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.inputs = {}; self.rows = []; self.links = []; self.headings = []
        self.fragments = []; self.skip = 0; self.row = None; self.cell = None; self.heading = None; self.anchor = None
        self.feed(html)
        self.fetched_at = checked_at()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}: self.skip += 1
        if tag == "input" and attrs.get("name") and attrs.get("type") == "hidden":
            self.inputs[attrs["name"]] = attrs.get("value", "")
        if tag == "tr": self.row = []
        if tag in {"td", "th"}: self.cell = []
        if tag in {"h1", "h2", "h3", "h4"}: self.heading = []
        if tag == "a": self.anchor = [attrs.get("href", ""), []]

    def handle_data(self, data):
        if self.skip: return
        text = " ".join(data.split())
        if not text: return
        self.fragments.append(text)
        if self.cell is not None: self.cell.append(text)
        if self.heading is not None: self.heading.append(text)
        if self.anchor is not None: self.anchor[1].append(text)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.skip = max(0, self.skip - 1)
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is not None: self.row.append(" ".join(self.cell))
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.row: self.rows.append((list(self.headings), self.row))
            self.row = None
        if tag in {"h1", "h2", "h3", "h4"} and self.heading is not None:
            self.headings.append(" ".join(self.heading)); self.heading = None
        if tag == "a" and self.anchor is not None:
            self.links.append((self.anchor[0], " ".join(self.anchor[1]))); self.anchor = None

    @property
    def text(self): return "\n".join(self.fragments)


def fetch(url: str, opener=None, data=None) -> Page:
    # Allow only the two explicit public sources, including redirects.
    if urllib.parse.urlsplit(url).hostname not in {"paopropertysearch.coj.net", "www.jacksonville.gov"}:
        raise ValueError("Unsupported research source.")
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
        headers={"User-Agent": "JaxProjectAdvisor/1.0 (preliminary public-record lookup)",
                 **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    open_request = opener.open if opener else urllib.request.urlopen
    with open_request(request, timeout=8) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname not in {"paopropertysearch.coj.net", "www.jacksonville.gov"}:
            raise ValueError("Unsupported research redirect.")
        html = response.read(2_000_001)
        if len(html) > 2_000_000: raise ValueError("Source exceeded the research size limit.")
        return Page(html.decode("utf-8", "replace"))


def cached(key: str, loader, seconds=3600):
    with _lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < seconds: return item[1]
    result = loader()
    with _lock: _cache[key] = (time.monotonic(), result)
    return result


def checked_at(): return datetime.now(timezone.utc).isoformat(timespec="seconds")


def street_parts(address: str):
    value = re.sub(r"\b(?:suite|unit|ste|apt|#)\s*[\w-]+.*$", "", address, flags=re.I)
    value = re.split(r"[,;]|\bJacksonville\b", value, flags=re.I)[0]
    match = re.match(r"\s*(\d{1,6})\s+([A-Za-z][A-Za-z .'-]+)", value)
    if not match: return None
    name = re.sub(r"\b(?:boulevard|blvd|street|st|avenue|ave|road|rd|drive|dr|lane|ln|court|ct)\.?$", "", match[2].strip(), flags=re.I).strip()
    return match[1], name.upper()


def lookup_property(address: str, use_cache: bool=True) -> dict:
    parts = street_parts(address)
    if not parts: return {"status": "needs_address", "query": address}
    def lookup():
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        form = fetch(PAO + "/Basic/Search.aspx", opener)
        number, street = parts
        # Search by the first word; rank the returned full addresses, including minor typos.
        data = {**form.inputs, "ctl00$cphBody$tbStreetNumber": number, "ctl00$cphBody$tbStreetName": street.split()[0],
                "ctl00$cphBody$bSearch": "Search"}
        results = fetch(PAO + "/Basic/Results.aspx", opener, data)
        candidates = []
        for _, cells in results.rows:
            joined = " ".join(cells)
            match = re.search(r"\b" + re.escape(number) + r"\s+([A-Z][A-Z0-9 .'-]+)", joined.upper())
            parcel = re.search(r"\b(\d{6})[- ]?(\d{4})\b", joined)
            if not match or not parcel: continue
            candidate_street = re.split(r"\b(?:JACKSONVILLE|FL|BLVD|AVE|ST|RD|DR|LN|CT)\b", match[1])[0].strip()
            score = difflib.SequenceMatcher(None, street, candidate_street).ratio()
            if score >= .78:
                candidates.append((score, parcel[1]+parcel[2], joined[:400]))
        if not candidates:
            return {"status":"not_found", "query":address, "checked_at":checked_at()}
        best = max(score for score, _, _ in candidates)
        selected = list(dict.fromkeys(parcel for score, parcel, _ in candidates if score >= best-.03))
        records = []
        for parcel in selected[:3]:
            url = PAO + "/Basic/Detail.aspx?RE=" + parcel
            detail = fetch(url, opener)
            values = {}
            for _, cells in detail.rows:
                if len(cells) == 2 and cells[0].strip() in {"Property Use", "RE #", "Building Type"}:
                    values[cells[0].strip()] = cells[1].strip()
            primary = re.search(r"Primary Site Address\n(.+?)(?:\nJacksonville|\n\n)", detail.text, re.I | re.S)
            site_address = primary[1].replace("\n", " ").strip() if primary else next((label for _, label in detail.links if label.startswith(number+" ")), "")
            if not values.get("Property Use") or not site_address: continue
            # Never include owner identities, valuations or mailing addresses in the model context.
            records.append({"parcel":values.get("RE #",parcel), "address":site_address[:200], "property_use":values["Property Use"],
                            "source_id":"property-"+parcel, "source_title":"Duval Property Appraiser", "source_url":url})
        return {"status":"matched" if len(records)==1 else "ambiguous" if records else "not_found", "query":address,
                "records":records, "match_score":round(best,3), "checked_at":checked_at(),
                "limitation":"Tax-roll building/parcel use does not establish the suite's existing permitted occupancy, zoning permission or current tenant use."}
    try:
        return cached("property:"+address.upper(), lookup) if use_cache else lookup()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {"status":"unavailable", "query":address, "checked_at":checked_at()}


def fee_records(page: Page) -> list[dict]:
    """Keep the fee amount and the section's unit/conditions together, never a total estimate."""
    records = []
    for headings, cells in page.rows:
        if len(cells) != 2: continue
        label, amount = cells
        lower = label.casefold()
        section = headings[-1] if headings else ""
        key, qualification = "", ""
        if "new certification of use" in lower or "relocation of business" in lower:
            key = "cou-new"; qualification = "This is the COU application category, not the total project permit cost."
        elif "mobility fee calculation certificate" in lower and "expedited" not in lower:
            key = "mobility-certificate"; qualification = "This is the calculation-certificate fee, not the project-specific mobility fee."
        elif "de minimis" in lower and "mobility" in lower:
            key = "mobility-de-minimis"; qualification = "This is the review fee; it does not establish eligibility for an exemption."
        elif "new hood systems" in lower:
            key = "hood-review"; qualification = "This is a fire plan-review fee for that category, not the cost of the hood, installation or other permits."
        elif "alterations, tenant build-out" in lower:
            key = "plans-examination"; qualification = "This is a construction-value-based component, not the total permit fee."
        elif "alterations, tenant build-out" in section.casefold() and lower in {"bid permit fee", "minimum bid permit fee", "plan review fee"}:
            key = "alterations-" + re.sub(r"[^a-z]+", "-", lower).strip("-")
            qualification = "Apply the section's cost basis, minimums and other fee components; this is not a project total."
        if not key or not re.search(r"\$\s*\d", amount): continue
        basis_match = re.search(r"for each (.+?)(?:\s+-\s+Section|$)", section, re.I)
        basis = "for each " + basis_match[1] if basis_match and key == "alterations-bid-permit-fee" else ""
        records.append({"id":key, "label":label, "amount":amount, "basis":basis, "section":section, "qualification":qualification,
                        "source_id":"fees", "source_title":PAGES["fees"][0], "source_url":PAGES["fees"][1], "checked_at":page.fetched_at})
    return records


def source_record(key: str) -> dict:
    title, url = PAGES[key]
    try:
        page = cached("page:"+key, lambda: fetch(url))
        if key == "fees":
            return {"id":key, "title":title, "url":url, "status":"available", "fees":fee_records(page), "checked_at":page.fetched_at}
        text = page.text
        # Discard header/navigation; supply compact relevant official text, not an entire website.
        marker = {"cou":"Certificates of Use and Converting Use Building Permits", "bid":"Purpose", "forms":"Public Records Notice", "site":"Site Development Plan Review",
                  "ldpm":"Current LDPM Effective", "mobility":"The CMMSO", "professionals":"WHAT IS A"}.get(key, "")
        position = (text.rfind(marker) if key in {"cou","site"} else text.find(marker)) if marker else -1
        if position < 0: position = text.find(title.split(" /")[0])
        excerpt = text[max(position,0):max(position,0)+6500]
        if key == "professionals":
            position = text.lower().find("how do i obtain a permit for converting use")
            if position >= 0: excerpt = text[position:position+2300]
        return {"id":key, "title":title, "url":url, "status":"available", "excerpt":excerpt, "checked_at":page.fetched_at}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {"id":key, "title":title, "url":url, "status":"unavailable"}


def research_context(project: dict, statement: str, cache_property: bool=True) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    location = project["fields"]["location"]["value"]
    scope = " ".join([statement, *[field["value"] for field in project["fields"].values()] ]).casefold()
    keys = ["fees", "cou", "bid"]
    if any(word in scope for word in ("restaurant", "warehouse", "office", "design", "architect", "contractor")):
        keys.append("professionals")
    if any(word in scope for word in ("mobility", "restaurant", "traffic")): keys.append("mobility")
    if any(word in scope for word in ("site", "parking", "driveway", "drainage", "historic", "exterior", "facade", "façade", "window", "roof", "fence", "sign")): keys.extend(["site", "ldpm", "historic"])
    if any(word in scope for word in ("form", "checklist", "document", "submission")): keys.append("forms")
    # Bound retrieval and run independent public reads concurrently.
    keys = list(dict.fromkeys(keys))[:8]
    previous = project.get("research", {}).get("property", {})
    previous_historic = project.get("research", {}).get("historic", {})
    reuse_historic = False
    try:
        reuse_historic = previous_historic.get('schema_version') == SCHEMA_VERSION and previous_historic.get('query') == location and (datetime.now(timezone.utc) - datetime.fromisoformat(previous_historic['checked_at'])).total_seconds() < (3600 if previous_historic.get('local_status') != 'requires_review' else 300)
    except (KeyError, ValueError, TypeError):
        pass
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {key:executor.submit(source_record,key) for key in keys}
        property_future = executor.submit(lookup_property,location,cache_property) if location != "unknown" else None
        historic_future = executor.submit(lookup_historic, location) if location not in {'unknown','not_sure',''} and not reuse_historic else None
        sources = [future.result() for future in futures.values()]
        property_result = property_future.result() if property_future else previous or {"status":"needs_address"}
        historic = historic_future.result() if historic_future else previous_historic if reuse_historic else {}
    if historic:
        sources.append({'id':'historic-gis','title':'City historic GIS screening','url':historic['source_url'],
                        'status':'available','excerpt':historic['reason'],'checked_at':historic['checked_at']})
    return {"sources":sources, "property":property_result, "historic":historic, "fees":[fee for source in sources for fee in source.get("fees", [])]}


def compose_response(result: dict, context: dict, disclosed_fee_ids: tuple[str, ...] = (), allow_repeated_fee: bool = False) -> tuple[str,list[dict],str]:
    message, question = result.get("message"), result.get("question", "")
    if not isinstance(message,str) or not message.strip() or not isinstance(question,str) or not isinstance(result.get("ready_for_brief"),bool):
        raise ValueError("The advisor returned an invalid reply. Please retry.")
    if "$" in message or "$" in question:
        raise ValueError("The advisor could not verify a quoted fee. Please retry.")
    if not result["ready_for_brief"] and not question.strip():
        raise ValueError("The advisor did not provide a follow-up question. Please retry.")
    if result["ready_for_brief"] and question.strip():
        raise ValueError("The advisor returned inconsistent follow-up state. Please retry.")
    if message.count("?") or question.count("?") > 1:
        raise ValueError("The advisor returned too many follow-up questions. Please retry.")
    available = {source["id"]:{"id":source["id"],"title":source["title"],"url":source["url"]}
                 for source in context["sources"] if source["status"] == "available"}
    for record in context["property"].get("records",[]):
        available[record["source_id"]] = {"id":record["source_id"],"title":record["source_title"] + " — " + record["property_use"],"url":record["source_url"]}
    source_ids = result.get("source_ids", [])
    citations = [available[key] for key in source_ids if isinstance(key,str) and key in available] if isinstance(source_ids,list) else []
    fee = next((row for row in context["fees"] if row["id"] == result.get("fee_id")),None)
    parts = [message.strip()[:1300]]
    disclosed = ""
    if fee and (fee["id"] not in disclosed_fee_ids or allow_repeated_fee):
        parts.append("The City fee schedule lists " + fee["amount"] + " for " + fee["label"] + (", " + fee["basis"] if fee.get("basis") else "") + ". " + fee["qualification"])
        if "fees" in available and not any(source["id"] == "fees" for source in citations): citations.append(available["fees"])
        disclosed = fee["id"]
    if question.strip(): parts.append(question.strip()[:450])
    return "\n\n".join(parts), citations, disclosed


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--sources"]:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as executor:
            records=list(executor.map(source_record,PAGES))
        print(json.dumps([{key:value for key,value in record.items() if key!='excerpt'} for record in records],indent=2))
    else:
        print(json.dumps(lookup_property(" ".join(sys.argv[1:])), indent=2))
