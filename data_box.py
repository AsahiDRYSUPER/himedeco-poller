"""dataブランチ（公開）に出すファイルの鍵かけ。2026-10-25

形はボードの todo.enc.json と同じ：AES-256-GCM、鍵＝金庫の TODO_KEY を base64 で戻したもの、AAD なし。
中身は {"iv": base64, "ct": base64, "generated_at": ISO}。ファイル名は X.json → X.enc.json。
読む時は移行のため「X.enc.json を試し、無ければ X.json」の順（しばらく両方に対応）。書く時は X.enc.json だけ（平文は書かない）。
鍵が無い時は書かない（呼ぶ側は「今回は書かない」に倒す。publish_data.sh が前のものを引き継ぐ）。

使い方（publish_data.sh から）：python3 data_box.py seal-file <平文のファイル> <name>
  → 同じフォルダに <name>.enc.json を作る（切り替え直後に、前の平文を鍵つきに移すため）。鍵が無ければ 2 で終わる。
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
_warned = set()


def _out_dir():
    return Path(os.environ.get("OUT_DIR", "out"))


def _warn_once(tag, msg):
    if tag not in _warned:
        _warned.add(tag)
        print(msg, flush=True)


def key():
    """金庫の TODO_KEY（base64）を戻したもの。無い・壊れている時は None。"""
    k = (os.environ.get("TODO_KEY") or "").strip()
    if not k:
        return None
    try:
        b = base64.b64decode(k)
    except Exception:
        return None
    return b if len(b) in (16, 24, 32) else None


def ready():
    return key() is not None


def enc_name(name):
    """"shops" / "shops.json" / "shops.enc.json" → "shops"（呼び名だけにそろえる）"""
    n = str(name)
    for suf in (".enc.json", ".json"):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def seal(obj, generated_at=None):
    """中身（JSONにできるもの）に鍵をかけて、置く形（dict）にする。鍵が無ければ None。"""
    k = key()
    if k is None:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    return {"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode(),
            "generated_at": generated_at or datetime.now(JST).isoformat(timespec="seconds")}


def unseal(env):
    """置く形（dict）→ 中身。鍵が無い・開けない時は例外。"""
    k = key()
    if k is None:
        raise RuntimeError("鍵（TODO_KEY）が無い")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = AESGCM(k).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
    return json.loads(raw.decode("utf-8"))


def local_path(name, out_dir=None):
    return Path(out_dir or _out_dir()) / f"{enc_name(name)}.enc.json"


def write(name, obj, out_dir=None, generated_at=None):
    """out/<name>.enc.json に鍵をかけて書く。書けたら True。鍵が無ければ書かずに False（ログに1行）。
    同じ名前の平文が out/ に残っていたら消す（公開に拾われないように）。"""
    n = enc_name(name)
    env = seal(obj, generated_at)
    if env is None:
        _warn_once("nokey:" + n, f"data_box: 鍵（TODO_KEY）が無いので {n} は書かない（前の公開のものを引き継ぐ）")
        return False
    p = local_path(n, out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(env), encoding="utf-8")
    plain = p.with_name(f"{n}.json")
    try:
        if plain.exists():
            plain.unlink()
    except Exception:
        pass
    return True


def read_local(name, out_dir=None):
    """手元（out/）にあれば読む：<name>.enc.json → <name>.json。返す (有ったか, 中身)。開けない時は (True, None)。"""
    n = enc_name(name)
    p = local_path(n, out_dir)
    if p.exists():
        try:
            return True, unseal(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            _warn_once("badlocal:" + n, f"data_box: 手元の {n}.enc.json を開けない（鍵 TODO_KEY を確かめる）")
            return True, None
    q = p.with_name(f"{n}.json")
    if q.exists():
        try:
            return True, json.loads(q.read_text(encoding="utf-8"))
        except Exception:
            return True, None
    return False, None


def fetch(name, base=None, timeout=20):
    """dataブランチから読む：<name>.enc.json → 無ければ（404の時だけ）<name>.json。
    返す (状態, 中身)。状態は "ok"／"missing"（どちらも404＝まだ一度も書いていない）／"error"（それ以外。中身は None）。"""
    import requests
    n = enc_name(name)
    b = (base or PUBLIC_RAW).rstrip("/")
    t = int(time.time())
    try:
        r = requests.get(f"{b}/{n}.enc.json", params={"t": t}, timeout=timeout)
    except Exception:
        return "error", None
    if r.status_code == 200:
        try:
            return "ok", unseal(r.json())
        except Exception:
            _warn_once("badremote:" + n, f"data_box: 公開の {n}.enc.json を開けない（鍵 TODO_KEY を確かめる）")
            return "error", None
    if r.status_code != 404:
        return "error", None
    try:
        r = requests.get(f"{b}/{n}.json", params={"t": t}, timeout=timeout)
    except Exception:
        return "error", None
    if r.status_code == 200:
        try:
            return "ok", r.json()
        except Exception:
            return "error", None
    return ("missing", None) if r.status_code == 404 else ("error", None)


def read(name, out_dir=None, base=None, timeout=20):
    """手元 → dataブランチ（enc → plain）の順に読む。返す (状態, 中身)。
    状態は "ok"／"missing"（どこにも無い＝まだ一度も書いていない）／"error"（有るのに開けない・読めない。中身は None）。
    読んで足して書き戻す所はこれを使い、"error" の時は書かない（前の記録を空で上書きしないため）。"missing" の時だけ空から始める。"""
    n = enc_name(name)
    p = local_path(n, out_dir)
    if p.exists():
        try:
            return "ok", unseal(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            _warn_once("badlocal:" + n, f"data_box: 手元の {n}.enc.json を開けない（鍵 TODO_KEY を確かめる）")
            return "error", None
    q = p.with_name(f"{n}.json")
    if q.exists():
        try:
            return "ok", json.loads(q.read_text(encoding="utf-8"))
        except Exception:
            return "error", None
    try:
        return fetch(n, base=base, timeout=timeout)
    except Exception:
        return "error", None


def load(name, default=None, out_dir=None, base=None, timeout=20):
    """手元 → dataブランチ（enc → plain）の順に読む。読めなければ default。"""
    found, v = read_local(name, out_dir)
    if found:
        return v if v is not None else default
    state, v = fetch(name, base=base, timeout=timeout)
    return v if state == "ok" and v is not None else default


def shift_key(girl_id):
    """裏姫デコの「次の出勤日」のキー：HMAC-SHA256(鍵=TODO_KEY, "shift:"+id) の頭16文字。鍵が無ければ None。
    cast-mypage 側（ページを作る所）も同じ式にそろえること。"""
    k = key()
    if k is None:
        return None
    return hmac.new(k, ("shift:" + str(girl_id)).encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _seal_file(src, name):
    p = Path(src)
    obj = json.loads(p.read_text(encoding="utf-8"))
    env = seal(obj)
    if env is None:
        print(f"data_box: 鍵（TODO_KEY）が無いので {enc_name(name)} を鍵つきに移せない（平文のまま引き継ぐ）")
        return 2
    (p.parent / f"{enc_name(name)}.enc.json").write_text(json.dumps(env), encoding="utf-8")
    print(f"data_box: {enc_name(name)} を鍵つきに移した")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "seal-file":
        try:
            raise SystemExit(_seal_file(a[1], a[2]))
        except SystemExit:
            raise
        except Exception as e:
            print(f"data_box: 鍵つきに移せない（{type(e).__name__}）")
            raise SystemExit(1)
    print("使い方: python3 data_box.py seal-file <平文のファイル> <name>")
    raise SystemExit(64)
