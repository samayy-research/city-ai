"""Verified coordination-email request endpoint for Vercel."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler


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
    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("success"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return False


class handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 2 <= size <= 100000:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            name, email = text(data.get("name"), 120), text(data.get("email"), 254)
            address, message = text(data.get("projectAddress"), 300), text(data.get("message"), 1200)
            route, team = text(data.get("route"), 200), text(data.get("team"), 200)
            token = text(data.get("turnstileToken"), 2048)
            if not all((name, valid_email(email), address, message, route, team, token)):
                raise ValueError("Complete all required fields and verification.")
            remote_ip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if not verify_turnstile(token, remote_ip):
                self.send_json(400, {"error": "Verification expired or failed. Please try again."})
                return
            city_email = os.environ.get("CITY_COORDINATION_EMAIL", "")
            if not city_email:
                raise RuntimeError("The City request form has not been configured yet.")
            subject = f"Coordination request: {route}"
            email_body = (
                "City coordination request\n\n"
                f"Recommended group: {team}\nProject route: {route}\n\n"
                f"Name: {name}\nEmail: {email}\nProject address or parcel: {address}\n\n"
                f"Request:\n{message}"
            )
            mailto_url = "mailto:" + urllib.parse.quote(city_email) + "?" + urllib.parse.urlencode(
                {"subject": subject, "body": email_body}
            )
            self.send_json(200, {"mailtoUrl": mailto_url})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(503, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Unexpected server error."})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "POST, OPTIONS")
        self.end_headers()
