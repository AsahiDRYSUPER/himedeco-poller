"""個室の頼みが来た時に、寮管理表の「ブース管理表」へ自動で入れる（2026-10-05 一希さん「個室の依頼の場合は、個室の確保までがセット」）。

管理表に入れた Apps Script（ウェブアプリ）を呼ぶ。空いているブースを探して書き込むのは向こうの仕事
（中身：メイン作業場/projects/キャスト業務の仕組み化/個室の自動予約/Code.gs）。
鍵つきのURLは金庫 BOOTH_URL。無ければ何もしない（今までどおり通知だけ）。
公開リポジトリなので、ログに名前は出さない。
"""
import os
import re

import requests

BOOTH_URL = os.environ.get("BOOTH_URL", "").strip()
# 管理表の利用者の頭文字（一希さんに教わったもの。とろ〜り・UNDERCOVER はまだ分からないので自動では入れない）
PREFIX = {"cg_kirakira": "K", "s_matikado": "M", "mrs_orange": "O", "venus_okayama": "V", "potya_reen": "P"}


def short_name(name):
    """管理表の書き方に合わせる：「辻むう」→「むう」（名字＋かなの名前なら、かなの名前だけ）。"""
    n = re.sub(r"[\[【(（].*?[\]】)）]", "", name or "").strip()
    n = re.sub(r"(?<=[ぁ-んァ-ヶー一-龠])(RSP|RS|SP)$", "", n)
    m = re.match(r"^[一-龠々]{1,3}([ぁ-んァ-ヶー]{1,6})$", n)
    return m.group(1) if m else n


def pref_of(text):
    """「1階希望」「3階で」などの希望。無ければ空。"""
    if re.search(r"(1|１|一)\s*階", text or ""):
        return "1階"
    if re.search(r"(3|３|三)\s*階", text or ""):
        return "3階"
    return ""


def enabled(shopdir):
    return bool(BOOTH_URL) and shopdir in PREFIX


def _hours(hhmm):
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


def book(shopdir, name, shifts, pref=""):
    """shifts: [(date, "14:00", "26:00"), …]
    返す: {"ok_all": bool, "items": [{"day": date, "ok": bool, "booth": "A7", "already": bool, "error": "…"}]}"""
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
    """「8日 A7・9日 A7・10日 A7」"""
    return "・".join(f'{i["day"].day}日 {i["booth"] if i["ok"] else "取れず（" + (i["error"] or "?") + "）"}' for i in res["items"])


def reply_text(res):
    """本人への一言。部屋が決まっていれば部屋も書く（置き場＝まだ部屋が決まっていない時は、部屋は書かない）。"""
    days = "・".join(f'{i["day"].day}日' for i in res["items"])
    rooms = {i["booth"] for i in res["items"]}
    if rooms and "置き場" not in rooms:
        if len(rooms) == 1:
            return f"個室も取っておいたよ🏠 {days}は {rooms.pop()} です"
        return "個室も取っておいたよ🏠 " + "・".join(f'{i["day"].day}日 {i["booth"]}' for i in res["items"])
    return f"個室も{days}の分、取っておいたよ🏠"
