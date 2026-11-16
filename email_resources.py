"""Official references for email preparation; these do not determine routing."""
import re

RESOURCES = [
    {"id": "coa", "title": "Jacksonville Certificate of Appropriateness process", "url": "https://www.jacksonville.gov/departments/planning-department/community-planning-division/historic-preservation-section/coa-process", "description": "Exterior and site work at locally designated historic properties; City staff confirm applicability."},
    {"id": "national", "title": "National Register of Historic Places", "url": "https://dos.fl.gov/historical/preservation/national-register/", "description": "National historic recognition. Listing alone does not establish a local COA requirement."},
    {"id": "clg", "title": "Florida Certified Local Governments", "url": "https://dos.fl.gov/historical/preservation/certified-local-governments/", "description": "Preservation assistance and programs for local governments; background information rather than an applicant permit step."},
    {"id": "markers", "title": "Florida Historical Marker Map", "url": "https://markers.flheritage.com/home/map", "description": "Historical marker locations and context; this map does not determine local historic designation or permit requirements."},
    {"id": "zoning", "title": "Jacksonville Zoning Code - Chapter 656", "url": "https://library.municode.com/fl/jacksonville/codes/code_of_ordinances?nodeId=TITXVIILAUS_CH656ZOCO", "description": "Land and building use regulations; confirm the applicable district and proposed use with City staff."},
    {"id": "downtown", "title": "Downtown Overlay and Design Standards", "url": "https://dia.jacksonville.gov/resources/downtown-overlay-zone-design-standards", "description": "Downtown use and design guidance, if the address is within the applicable overlay boundary."},
    {"id": "ddrb", "title": "Downtown Development Review Board", "url": "https://www.jacksonville.gov/departments/boards-and-commissions/downtown-development-review-board", "description": "Downtown review board information; City staff confirm whether project review is needed."},
]


def relevant_resources(active, context=""):
    """Select references from reported context, never infer a mapped boundary."""
    selected = set()
    if any(step["id"] == "use" for step in active):
        selected.add("zoning")
    if any(step["id"] == "historic" for step in active):
        selected.update(("coa", "national", "markers"))
    if re.search(r"\bdowntown\b", context, re.I):
        selected.update(("downtown", "ddrb"))
    return [resource for resource in RESOURCES if resource["id"] in selected]
