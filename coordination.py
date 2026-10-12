"""Prepare concise email drafts from the saved authoritative project plan."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import project_store
from project_workflow import build_brief, email_draft

DEPARTMENT_EMAILS = {
    "Zoning / Certificate of Use (COU)": "Zoning@coj.net",
    "Building Inspection Division / Permit Coordinator": "BIDDocuments@coj.net",
    "Development Services (site and civil review)": "ReviewGrp@coj.net",
    "Concurrency and Mobility Management System Office (CMMSO)": "CMMSO@coj.net",
}


def text(value: object, maximum: int) -> str:
    return value.strip()[:maximum] if isinstance(value, str) else ""


def valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def verify_turnstile(token: str, remote_ip: str) -> bool:
    secret = os.environ.get("TURNSTILE_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("The City request form has not been configured yet.")
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    request = urllib.request.Request("https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=urllib.parse.urlencode(payload).encode(), method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.loads(response.read()).get("success"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def prepare_coordination(data: dict, remote_ip: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("A coordination request is required.")
    name, email = text(data.get("name"), 120), text(data.get("email"), 254)
    team, token = text(data.get("team"), 200), text(data.get("turnstileToken"), 2048)
    if not name or not valid_email(email) or not token:
        raise ValueError("Complete your name, email and security verification.")
    if not verify_turnstile(token, remote_ip):
        raise ValueError("Verification expired or failed. Please try again.")
    record = project_store.load(data.get("projectId"))
    project = record["project"]
    brief = build_brief(project)
    if team == "Overall City coordination":
        recipient = os.environ.get("CITY_COORDINATION_EMAIL", "")
    elif team in DEPARTMENT_EMAILS:
        recipient = DEPARTMENT_EMAILS[team]
    else:
        raise ValueError("Choose a department from the project action plan.")
    if not valid_email(recipient):
        raise RuntimeError("The City coordination inbox has not been configured yet.")
    draft = email_draft(project, brief, team, name, email, text(data.get("projectAddress"), 300), text(data.get("note"), 500))
    # Ignore any client checklist, route, recipient or fabricated project details.
    draft["mailtoUrl"] = "mailto:" + urllib.parse.quote(recipient) + "?" + urllib.parse.urlencode(
        {"subject": draft["subject"], "body": draft["body"]}, quote_via=urllib.parse.quote)
    return draft
