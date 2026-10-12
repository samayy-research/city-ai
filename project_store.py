"""Durable local SQLite or shared Upstash REST project storage (standard library only)."""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

RETENTION_SECONDS = 7 * 24 * 60 * 60


def project_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("The saved project reference is invalid.")
    return value


def new_id() -> str:
    return secrets.token_hex(32)


def redis_command(command: list) -> object:
    url, token = os.environ.get("UPSTASH_REDIS_REST_URL", ""), os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if not url.startswith("https://") or not token:
        raise RuntimeError("Configure UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN for saved projects on Vercel.")
    request = urllib.request.Request(url.rstrip("/"), data=json.dumps(command).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read())
    if "error" in result:
        raise RuntimeError("Saved-project storage is temporarily unavailable.")
    return result.get("result")


def use_redis() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("UPSTASH_REDIS_REST_URL"))


@contextmanager
def connection():
    db_path = os.environ.get("PROJECT_DB_PATH") or str(Path(__file__).with_name(".project-state.sqlite3"))
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, record TEXT NOT NULL, expires REAL NOT NULL)")
        conn.execute("DELETE FROM projects WHERE expires < ?", (time.time(),))
        conn.commit()
        with conn:
            yield conn
    finally:
        conn.close()


def load(identifier: str) -> dict:
    identifier = project_id(identifier)
    if use_redis():
        raw = redis_command(["GET", "jax:project:" + identifier])
    else:
        with connection() as conn:
            row = conn.execute("SELECT record FROM projects WHERE id = ? AND expires > ?", (identifier, time.time())).fetchone()
        raw = row[0] if row else None
    if not raw:
        raise ValueError("This saved project has expired or is unavailable. Start a new project.")
    return json.loads(raw)


def save(identifier: str, record: dict, expected_revision: int = -1) -> None:
    identifier = project_id(identifier)
    raw = json.dumps(record)
    if use_redis():
        script = "local r=redis.call('GET',KEYS[1]); local v=-1; if r then v=cjson.decode(r).project.revision end; if v~=tonumber(ARGV[1]) then return 0 end; redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3]); return 1"
        saved = redis_command(["EVAL", script, 1, "jax:project:" + identifier, expected_revision, raw, RETENTION_SECONDS]) == 1
    else:
        with connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM projects WHERE id = ?", (identifier,)).fetchone()
            revision = json.loads(row[0])["project"]["revision"] if row else -1
            saved = revision == expected_revision
            if saved:
                conn.execute("INSERT OR REPLACE INTO projects VALUES (?, ?, ?)", (identifier, raw, time.time() + RETENTION_SECONDS))
    if not saved:
        raise ValueError("This project was updated by another request. Refresh to load the latest details, then retry.")
