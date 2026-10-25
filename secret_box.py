"""鍵つきの中身（sealed/*.enc）を開ける。中身はここでは読めない（2026-10-25）。
鍵は金庫の KNOWLEDGE_KEY。鍵が無い・開けない時は default を返す（呼ぶ側は「使わない」に倒す）。"""
import base64
import json
import os
from pathlib import Path

DIR = Path(__file__).with_name("sealed")
_cache = {}


def _key():
    k = (os.environ.get("KNOWLEDGE_KEY") or "").strip()
    try:
        return base64.b64decode(k) if k else None
    except Exception:
        return None


def ready():
    return _key() is not None


def text(name, default=None):
    if name in _cache:
        return _cache[name]
    key, p = _key(), DIR / f"{name}.enc"
    if key is None or not p.exists():
        return default
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        if not _cache.get("_warned"):
            _cache["_warned"] = True
            print("secret_box: 部品 cryptography が無いので鍵つきの中身を開けない（pip install cryptography）")
        return default
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        v = AESGCM(key).decrypt(base64.b64decode(d["iv"]), base64.b64decode(d["ct"]), name.encode()).decode("utf-8")
    except Exception:
        return default
    _cache[name] = v
    return v


def data(name, default=None):
    t = text(name)
    try:
        return json.loads(t) if t is not None else default
    except Exception:
        return default
