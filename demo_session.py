"""Signed conversation context carried in browser RAM; no session database or disk writes.

Signing prevents the client from manufacturing trusted project/source state. The
payload is not encrypted and belongs only in the TLS request body, never in URLs.
"""
import base64
import copy
import hashlib
import hmac
import json
import time
import zlib

MAX_TOKEN_CHARS=85_000
MAX_UNPACKED_BYTES=600_000


def signing_key(secret: str) -> bytes:
    if not secret:raise ValueError("The demo session cannot be signed without the model configuration.")
    return hmac.digest(secret.encode(),b"jax-demo-state-v1",hashlib.sha256)


def pack(record: dict, secret: str) -> str:
    state=copy.deepcopy(record)
    state.pop("result",None)
    state["history"]=state["history"][-16:]
    state["project"]["corrections"]=state["project"]["corrections"][-25:]
    for field in state["project"]["fields"].values():
        field["evidence"]=field["evidence"][-4:]
    envelope={"kind":"jax-demo-v1","expires":int(time.time())+6*60*60,"record":state}
    raw=json.dumps(envelope,separators=(",",":")).encode()
    if len(raw)>MAX_UNPACKED_BYTES:raise ValueError("This demo conversation is too long. Start another project.")
    payload=base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
    signature=hmac.digest(signing_key(secret),payload.encode(),hashlib.sha256).hex()
    token=payload+"."+signature
    if len(token)>MAX_TOKEN_CHARS:raise ValueError("This demo conversation is too long. Start another project.")
    return token


def unpack(token: object, secret: str) -> dict:
    if not isinstance(token,str) or len(token)>MAX_TOKEN_CHARS:
        raise ValueError("The demo context is invalid. Refresh to start a new project.")
    try:
        payload,signature=token.rsplit(".",1)
        expected=hmac.digest(signing_key(secret),payload.encode(),hashlib.sha256).hex()
        if not hmac.compare_digest(signature,expected):raise ValueError("invalid signature")
        compressed=base64.urlsafe_b64decode(payload+"="*(-len(payload)%4))
        decoder=zlib.decompressobj()
        raw=decoder.decompress(compressed,MAX_UNPACKED_BYTES+1)
        if len(raw)>MAX_UNPACKED_BYTES or not decoder.eof:raise ValueError("invalid size")
        envelope=json.loads(raw)
        if envelope["kind"]!="jax-demo-v1" or envelope["expires"]<time.time():raise ValueError("expired")
        return envelope["record"]
    except (ValueError,KeyError,TypeError,zlib.error):
        raise ValueError("This demo conversation expired or is invalid. Refresh to start a new project.") from None
