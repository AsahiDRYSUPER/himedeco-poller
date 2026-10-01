"""キャストからの「頼みごと」（個室・待機場所・迎えなど）を、済むまで店舗状況ボードに残すための記録。

2026-10-01 一希さん：街角 鎌倉りのんさんの「10/3 12〜20時、個室をおさえてもらえると」で、出勤は自動で上がったが
個室が取れていなかった（頼みごとはスマホへの通知だけで、一希さんが休みの日に誰も気づかなかった）。
→ 見つけた頼みごとをここに貯め、ボードの「頼みごと」タブに出す。「済んだ」のチェックは全員で共有。

名前を含むので、声かけリストと同じ鍵（TODO_KEY）で暗号化して out/requests.enc.json に置き、
publish_data.sh が dataブランチへ出す。ログには件数だけを出す（公開リポジトリのため）。
"""
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
OUT = Path(os.environ.get("OUT_DIR", "out")) / "requests.enc.json"
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
KEEP_DAYS = 30


def _key():
    k = os.environ.get("TODO_KEY", "")
    return base64.b64decode(k) if k else None


def load():
    """返す: (items, ok)。まだ一度も書いていない時は ([], True)。読めなかった時は ([], False)。"""
    key = _key()
    if not key:
        return [], False
    try:
        if OUT.exists():
            x = json.loads(OUT.read_text(encoding="utf-8"))
        else:
            r = requests.get(f"{PUBLIC_RAW}/requests.enc.json?t={int(datetime.now().timestamp())}", timeout=30)
            if r.status_code == 404:
                return [], True
            r.raise_for_status()
            x = r.json()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        plain = AESGCM(key).decrypt(base64.b64decode(x["iv"]), base64.b64decode(x["ct"]), None)
        return json.loads(plain.decode("utf-8")).get("items", []), True
    except Exception as e:
        print("頼みごとの記録を読めませんでした:", type(e).__name__)
        return [], False


def save(items):
    key = _key()
    if not key:
        return False
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    now = datetime.now(JST)
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y/%m/%d %H:%M:%S")
    items = sorted((i for i in items if str(i.get("at", "")) >= cut), key=lambda i: str(i.get("at", "")), reverse=True)[:300]
    payload = {"generated_at": now.isoformat(timespec="seconds"), "items": items}
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode(),
                               "generated_at": payload["generated_at"], "count": len(items)}), encoding="utf-8")
    return True


def record(new_items):
    """新しい頼みごとを足して保存する（同じ連絡は最初の記録を残す）。"""
    if not new_items:
        return
    items, ok = load()
    if not ok and not OUT.exists():
        print(f"頼みごと: 前の記録が読めないため、今回の {len(new_items)} 件は保存を見送りました（通知は送っています）")
        return
    by = {str(i["id"]): i for i in items}
    added = 0
    for n in new_items:
        if str(n["id"]) not in by:
            by[str(n["id"])] = n
            added += 1
    if save(list(by.values())):
        print(f"頼みごと: 新しく {added} 件を記録（合計 {len(by)} 件）")
