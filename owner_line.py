"""サブ卍が女の子に「卍に伝えた」と返した時に、卍さん（一希さん）の LINE に公式LINE「AI卍」から知らせる（2026-11-25）。

一希さん「卍に伝えとくねと言われても、どうやって俺に伝えてる？」「LINEで送られてくる形に」。
- 窓口の URL は鍵つきの yoasobi_templates の "owner_ask_url"（公開のコードに書かない）。無ければ何もしない
- 名乗りは GitHub Actions の身分証（OIDC、aud=owner-ask）。鍵は持たない。ワークフローに id-token: write が要る
- 同じ連絡で2通にならないよう、連絡ごとに決まった key を渡す（LINE 側で同じ key は1回だけ）
- ログには結果だけを出す（名前・本文は出さない。公開リポジトリのため）
"""
import os
import uuid

import requests

AUD = "owner-ask"


def _url():
    try:
        import secret_box
        return ((secret_box.data("yoasobi_templates") or {}).get("owner_ask_url") or "").strip()
    except Exception:
        return ""


def _token():
    u, t = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not u or not t:
        return ""
    try:
        r = requests.get(u + "&audience=" + AUD, headers={"Authorization": "bearer " + t}, timeout=15)
        return (r.json() or {}).get("value") or ""
    except Exception:
        return ""


def note(text, dedup):
    """知らせる。送れたら True。dedup は連絡ごとに決まった文字（姫デコのメッセージIDなど）"""
    url, tok = _url(), _token()
    if not url or not tok or not (text or "").strip():
        print("卍さんへのLINE：送らない（" + ("窓口なし" if not url else "身分証なし" if not tok else "空") + "）")
        return False
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, "owner-note:" + str(dedup)))
    try:
        r = requests.post(url, json={"op": "note", "text": text[:1500], "key": key},
                          headers={"Authorization": "Bearer " + tok}, timeout=20)
        print(f"卍さんへのLINE：HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print("卍さんへのLINE：送れなかった", type(e).__name__)
        return False
