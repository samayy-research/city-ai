"""Vercel serverless endpoint for the OpenAI-compatible advisor chat."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler

from app import MAX_REQUEST_BYTES, ProviderError, ConfigurationError, add_security_headers, chat_request
from project_store import StorageError


logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        add_security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                raise ValueError("Content-Type must be application/json")
            size = int(self.headers.get("Content-Length", "0"))
            if not 2 <= size <= MAX_REQUEST_BYTES:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            self.send_json(200, chat_request(data))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except ProviderError as exc:
            self.send_json(502, {"error": str(exc)})
        except (ConfigurationError, StorageError) as exc:
            logger.error("Deployment dependency error: %s", exc.code)
            self.send_json(503, {"error":str(exc), "code":exc.code})
        except Exception:
            logger.exception("Unexpected error while processing /api/chat")
            self.send_json(500, {"error": "Unexpected server error."})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "POST, OPTIONS")
        add_security_headers(self)
        self.end_headers()
