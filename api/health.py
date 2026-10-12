"""Safe deployment readiness: presence flags, no secrets or upstream API calls."""
import json
from http.server import BaseHTTPRequestHandler

from app import add_security_headers, deployment_readiness


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        readiness=deployment_readiness()
        body=json.dumps(readiness).encode()
        self.send_response(200 if readiness["ready"] else 503)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        add_security_headers(self)
        self.end_headers()
        self.wfile.write(body)
