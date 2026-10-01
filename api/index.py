"""Vercel entry point for the application page."""

from http.server import BaseHTTPRequestHandler

from app import add_security_headers, page_html


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = page_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        add_security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        add_security_headers(self)
        self.end_headers()
