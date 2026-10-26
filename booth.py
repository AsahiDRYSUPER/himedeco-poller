"""個室の頼みが来た時に、管理表の「ブース管理表」へ自動で入れる。

管理表に入れた Apps Script（ウェブアプリ）を呼ぶ。空いているブースを探して書き込むのは向こうの仕事（中身は非公開側）。
鍵つきのURLは金庫 BOOTH_URL。無ければ何もしない（今までどおり通知だけ）。
希望の場所を読む言葉は鍵つき（sealed/ops_booth.enc の "prefs"）。開けない時も自動では取らない（通知だけ）。
公開リポジトリなので、ログに名前は出さない。
"""
import os
import re

import requests

import secret_box

BOOTH_URL = os.environ.get("BOOTH_URL", "").strip()
# 希望の場所の読み方（鍵つき）：[{"words": [...], "value": 管理表に渡す値}]。上から順に見て最初に合ったもの（2026-10-26）
PREFS = [r for r in ((secret_box.data("ops_booth") or {}).get("prefs") or []) if isinstance(r, dict) and r.get("value")]
# 管理表の利用者の頭文字（店ごと）
PREFIX = {"cg_kirakira": "K", "s_matikado": "M", "mrs_orange": "O", "venus_okayama": "V", "potya_reen": "P",
          "torori_angel": "T", "undercover": "U"}


def short_name(name):
    """管理表の書き方に合わせる：「試験ぽぽ」→「ぽぽ」（名字＋かなの名前なら、かなの名前だけ）。"""
    n = re.sub(r"[\[【(（].*?[\]】)）]", "", name or "").strip()
    n = re.sub(r"(?<=[ぁ-んァ-ヶー一-龠])(RSP|RS|SP)$", "", n)
    m = re.match(r"^[一-龠々]{1,3}([ぁ-んァ-ヶー]{1,6})$", n)
    return m.group(1) if m else n


def pref_of(text):
    """希望の場所（言葉は鍵つき）。無ければ空。空白は無視して比べる"""
    t = re.sub(r"\s+", "", text or "")
    for r in PREFS:
        if any(str(w) and re.sub(r"\s+", "", str(w)) in t for w in (r.get("words") or [])):
            return str(r["value"])
    return ""


def enabled(shopdir):
    return bool(BOOTH_URL) and bool(PREFS) and shopdir in PREFIX


def _hours(hhmm):
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def book(shopdir, name, shifts, pref=""):
    """shifts: [(date, "14:00", "26:00"), …]
    返す: {"ok_all": bool, "items": [{"day": date, "ok": bool, "booth": "A1", "already": bool, "error": "…"}]}"""
    items = []
    for d, s, e in shifts:
        payload = {"shop": PREFIX[shopdir], "name": short_name(name), "date": d.isoformat(),
                   "start": _hours(s), "end": _hours(e), "pref": pref}
        try:
            r = requests.post(BOOTH_URL, json=payload, timeout=60)
            j = r.json()
        except Exception as ex:
            j = {"ok": False, "error": type(ex).__name__}
        items.append({"day": d, "ok": bool(j.get("ok")), "booth": j.get("booth") or "",
                      "already": bool(j.get("already")), "error": str(j.get("error") or "")})
    return {"ok_all": bool(items) and all(i["ok"] for i in items), "items": items}


def summary(res):
    """「8日 A1・9日 A1」の形"""
    return "・".join(f'{i["day"].day}日 {i["booth"] if i["ok"] else "取れず（" + (i["error"] or "?") + "）"}' for i in res["items"])


def check():
    """鍵つきURLが通って、管理表が読めるかだけ確かめる（書き込みなし）。名前や部屋はログに出さない。"""
    from datetime import date, timedelta
    if not BOOTH_URL:
        print("BOOTH_URL がありません")
        return False
    d = (date.today() + timedelta(days=1)).isoformat()
    try:
        j = requests.post(BOOTH_URL, json={"shop": "K", "name": "ためし", "date": d, "start": 12, "end": 13, "dry": True},
                          timeout=90).json()
    except Exception as ex:
        print("つながらない:", type(ex).__name__)
        return False
    if j.get("ok"):
        print("鍵が通って、管理表も読めた")
        return True
    if j.get("error") in ("full", "no slot"):
        print("鍵は通った（明日のその時間は空きなし）")
        return True
    print("だめ:", j.get("error"))
    return False


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(0 if check() else 1)
