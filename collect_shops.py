"""共有ボード(千田さんと卍さん用)のデータを、5分ごとに集める: 店ごとの出勤人数・目標・不足、次の出勤日、今日声をかける子(暗号化)。
公開リポジトリ(Actions無料)で動かす。名前など個人の情報は、暗号化した形でしか書き出さない。ログにも名前は出さない。"""
import base64
import hashlib
import json
import os
import re
import unicodedata
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from cityheaven_api import SHOPS_API, fetch_shift_list, parse_girls

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
CONFIG = json.loads(Path("shops_config.json").read_text(encoding="utf-8"))
HOLIDAYS = set(CONFIG.get("holidays", []))
WEEK = "月火水木金土日"
ATT_EXTRA = {}
GIRLS_BY_SHOP = {}
CASTS_MAP = json.loads(os.environ["CASTS_MAP"])  # 金庫(GitHub Secret)から。名前を含むので、リポジトリには置かない

RANK_KEYS = {
    "キラキラ学園": "cg_kirakira", "街角レディ": "s_matikado", "オレンジな気持ち": "mrs_orange",
    "VENUS": "venus_okayama", "ぽちゃりーん": "potya_reen", "とろ〜りAngel": "torori_angel", "UNDERCOVER": "undercover",
}
RANKING_URLS = {
    "weekly": "https://www.cityheaven.net/okayama/shop-ranking-203711/",
    "daily": "https://www.cityheaven.net/okayama/shop-list/ranking_sort_daily/",
}


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Referer": "https://www.cityheaven.net/okayama/",
}


def is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5 or d.strftime("%Y%m%d") in HOLIDAYS


def attendance(shopdir, days):
    info = SHOPS_API[shopdir]
    girls = parse_girls(fetch_shift_list(info["shopid"], info["apikey"], base_day=days[0].strftime("%Y%m%d")))
    GIRLS_BY_SHOP[shopdir] = girls
    today = days[0].strftime("%Y%m%d")
    no_next = 0  # 今日出勤していて、明日以降の出勤が1件も出ていない人数(人数のみ。名前は載せない)
    for g in girls:
        t = next((x for x in g["days"] if x["date"] == today), None)
        if t and t["start_time"] and not any(x["date"] > today and x["start_time"] for x in g["days"]):
            no_next += 1
    ATT_EXTRA[shopdir] = no_next
    out = {}
    for d in days:
        key = d.strftime("%Y%m%d")
        out[key] = sum(1 for g in girls for x in g["days"] if x["date"] == key and x["start_time"])
    return out


def rankings():
    out = {}
    for name, url in RANKING_URLS.items():
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        print("ランキング取得", name, r.status_code, len(r.text), (soup.title.string if soup.title else "")[:40])
        items = []
        for i, li in enumerate(soup.select("li.shop_list"), 1):
            n = li.select_one(".shop_title_shop")
            if not n:
                continue
            title = n.get_text(strip=True)
            ours = next((k for w, k in RANK_KEYS.items() if w in title), None)
            items.append({"rank": i, "name": title, "ours": ours, "rival": "COCKTAIL" in title})
        text = soup.get_text()
        period = re.search(r"\((\d+/\d+～\d+/\d+)\s*集計\)", text)
        total = re.search(r"／\s*全\s*(\d+)\s*件", text.replace("\n", ""))
        out[name] = {"period": period.group(1) if period else "", "total": int(total.group(1)) if total else None, "list": items}
    return out


def clean_name(name: str) -> str:
    """名前の後ろの肩書き(【PREMIUM】など)を外す。"""
    return re.sub(r"[\[【(（].*?[\]】)）]", "", unicodedata.normalize("NFKC", name or "")).strip()


def load_prev_todo():
    """前回書き出した声かけリストを読み戻す(声かけ対象だった子が、あとから出勤を出したかを判定するため)。"""
    key_b64 = os.environ.get("TODO_KEY", "")
    if not key_b64:
        return None
    try:
        local = OUT_DIR / "todo.enc.json"
        if local.exists():
            x = json.loads(local.read_text(encoding="utf-8"))
        else:
            r = requests.get(f"{PUBLIC_RAW}/todo.enc.json?t={int(datetime.now().timestamp())}", timeout=30)
            if r.status_code != 200:
                return None
            x = r.json()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return json.loads(AESGCM(base64.b64decode(key_b64)).decrypt(base64.b64decode(x["iv"]), base64.b64decode(x["ct"]), None).decode("utf-8"))
    except Exception as e:
        print("前回の声かけリストを読めませんでした:", type(e).__name__)
        return None


def todo_list(today: str, prev=None):
    """今日出勤していて、次回(明日以降)の出勤が1件も出ていない子=声かけの対象(status=open)。
    今日の前回の対象だった子が、いま次回の出勤を出していれば、声かけ成功(status=ok、次の出勤日つき)。"""
    prev_by_shop = {x["key"]: x for x in prev["shops"]} if prev and prev.get("date") == today else {}
    out = []
    for shopdir, cfg in CONFIG["shops"].items():
        girls = GIRLS_BY_SHOP.get(shopdir)
        if girls is None:
            if prev_by_shop.get(shopdir):
                out.append(prev_by_shop[shopdir])
            continue
        by_name = {clean_name(g["name"]): g for g in girls}
        rows, seen = [], set()
        for g in girls:
            t = next((x for x in g["days"] if x["date"] == today and x["start_time"]), None)
            if t and not any(x["date"] > today and x["start_time"] for x in g["days"]):
                nm = clean_name(g["name"])
                rows.append({"name": nm, "start": t["start_time"], "end": t["end_time"], "status": "open"})
                seen.add(nm)
        for pr in prev_by_shop.get(shopdir, {}).get("list", []):
            if pr["name"] in seen:
                continue
            g = by_name.get(pr["name"])
            nxt = None
            if g:
                nxt = next((x["date"] for x in sorted(g["days"], key=lambda d: d["date"]) if x["date"] > today and x["start_time"]), None)
            if nxt or pr.get("status") == "ok":
                rows.append({"name": pr["name"], "start": pr["start"], "end": pr["end"], "status": "ok", "next": nxt or pr.get("next")})
            else:
                rows.append({"name": pr["name"], "start": pr["start"], "end": pr["end"], "status": "open"})
        rows.sort(key=lambda r: (r["status"] != "open", r["start"], r["name"]))
        out.append({"key": shopdir, "label": cfg["label"], "list": rows})
    return out


def encrypt_todo(payload: dict):
    """名前を含むので、暗号化して置く(復号の鍵は、合言葉つきのボードの中にだけある)。"""
    key_b64 = os.environ.get("TODO_KEY", "")
    if not key_b64:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(base64.b64decode(key_b64)).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    return {"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode(), "generated_at": payload["generated_at"]}


def norm(s):
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"[\[【(（].*?[\]】)）]", "", t)  # 名前の後ろの肩書き(【PREMIUM】など)は照合から外す
    return t.replace(" ", "").replace("\u3000", "")


def next_shifts(today):
    """裏姫デコのミミ用: キャストごとの「次の出勤日」。名前は載せず、girl_idのハッシュ(短縮)をキーにする。"""
    out = {}
    for slug, c in CASTS_MAP.items():
        girls = GIRLS_BY_SHOP.get(c["shopdir"])
        if girls is None:
            continue
        g = next((x for x in girls if norm(x["name"]) == norm(c["name"])), None)
        if g is None:
            out[hashlib.sha256(("shift:" + c["girl_id"]).encode()).hexdigest()[:16]] = {"next": "", "today": False}
            continue
        work = sorted(x["date"] for x in g["days"] if x["start_time"])
        nxt = next((d for d in work if d > today), "")
        key = hashlib.sha256(("shift:" + c["girl_id"]).encode()).hexdigest()[:16]
        out[key] = {"next": nxt, "today": today in work}
    return out


def main():
    now = datetime.now(JST)
    days = [now + timedelta(days=i) for i in range(7)]
    shops = []
    for shopdir, cfg in CONFIG["shops"].items():
        row = {"key": shopdir, "label": cfg["label"], "target": cfg["target"], "attendance": {}, "no_next": None, "error": ""}
        try:
            row["attendance"] = attendance(shopdir, days)
            row["no_next"] = ATT_EXTRA.get(shopdir)
        except Exception as e:
            row["error"] = f"出勤情報を取得できませんでした({type(e).__name__})"
        shops.append(row)
        print(shopdir, "OK" if not row["error"] else "失敗")
    rank = {}
    data = {
        "generated_at": now.isoformat(timespec="seconds"),
        "days": [{"date": d.strftime("%Y%m%d"), "label": d.strftime("%-m/%-d"), "week": WEEK[d.weekday()], "weekend": is_weekend(d)} for d in days],
        "shops": shops,
        "rank": rank,
        "weekend_note": CONFIG.get("weekend_note", ""),
    }
    today_s = now.strftime("%Y%m%d")
    shifts = {"generated_at": now.isoformat(timespec="seconds"), "shifts": next_shifts(today_s)}
    todo_shops = todo_list(today_s, load_prev_todo())
    todo_enc = encrypt_todo({"generated_at": now.isoformat(timespec="seconds"), "date": today_s, "shops": todo_shops})
    (OUT_DIR / "shops.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "next_shifts.json").write_text(json.dumps(shifts, ensure_ascii=False), encoding="utf-8")
    if todo_enc:
        (OUT_DIR / "todo.enc.json").write_text(json.dumps(todo_enc), encoding="utf-8")
        allrows = [r for x in todo_shops for r in x["list"]]
        print("声かけリスト: 暗号化して書き出し(件数のみ) 未:", sum(1 for r in allrows if r["status"] == "open"), "名 / 成功:", sum(1 for r in allrows if r["status"] == "ok"), "名")
    failed = sum(1 for s in shops if s["error"])
    print("共有ボード用データを書き出しました(店舗の取得失敗:", failed, "件)")
    return 0 if failed < len(shops) else 1


if __name__ == "__main__":
    raise SystemExit(main())
