"""姫デコチャットを5分ごとに見張り、キャストから新しい連絡が来たらスマホへ通知する(ntfy)。

なぜ必要か:
    姫デコチャットの「返事待ち」は、これまでボードを開いたときにしか分からなかった。
    連絡に気づくのが遅れると、次回の出勤を出してくれた子を待たせてしまう。

どう判断するか:
    「そのやりとりの最後の発言がキャストかどうか」で見る。ヘブンの既読フラグ(opened_flg)は、
    誰かが管理画面でその子のやりとりを開いた時点で立つので、返事をしたかどうかの判断には使えない。

通知の重複を防ぐ:
    一度通知したメッセージのIDを out/chat_seen.json に残し、dataブランチで持ち回る。
    初回(まだファイルが無い)は、通知せずに現状をそのまま記録するだけ。
    → 一希さんの指示(2026-09-21)「今まで溜まっている分は返信不要。今日から新しく来たものだけ」を、これで満たす。

公開リポジトリで動くので、キャスト名やメッセージの中身はログに出さない(出すのは店名と件数だけ)。
chat_seen.json に入れるのもメッセージのID(ただの数字)だけで、名前も本文も入れない。
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from heaven_http import BASE, HeavenClient, LoginError, load_credentials

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
SEEN_FILE = OUT_DIR / "chat_seen.json"

SHOPS = {
    "cg_kirakira": "キラキラ学園", "s_matikado": "街角レディ", "mrs_orange": "オレンジな気持ち",
    "venus_okayama": "VENUS", "potya_reen": "ぽちゃりーん", "torori_angel": "とろ〜りAngel",
    "undercover": "UNDERCOVER",
}
TOP_N = 20                      # 1店舗あたり、上から何人分のやりとりを見るか
MAX_NOTIFY = 8                  # 1回にスマホへ送る通知の上限(それを超えたらまとめて1通)
SEEN_KEEP = 400                 # 覚えておくメッセージIDの数
BOARD_URL = os.environ.get("BOARD_URL", "https://union-boards-4k7q.pages.dev/shops-08eee48153")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")


def clean_name(name: str) -> str:
    """【PREMIUM】などの肩書きを名前から外す。"""
    return re.sub(r"[\[【(（].*?[\]】)）]", "", str(name or "")).strip()


def load_seen():
    """前回までに通知したメッセージIDを読み戻す。初回はNone(通知せずに記録だけする合図)。"""
    try:
        if SEEN_FILE.exists():
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")).get("ids", []))
        r = requests.get(f"{PUBLIC_RAW}/chat_seen.json?t={int(time.time())}", timeout=30)
        if r.status_code != 200:
            return None
        return set(r.json().get("ids", []))
    except Exception as e:
        print("前回の記録を読めませんでした:", type(e).__name__)
        return None


def save_seen(ids):
    """新しい順に SEEN_KEEP 件だけ残して保存する(名前も本文も入れない)。"""
    keep = sorted(ids, reverse=True)[:SEEN_KEEP]
    SEEN_FILE.write_text(json.dumps({"ids": keep, "at": datetime.now(JST).isoformat()}, ensure_ascii=False), encoding="utf-8")


def pending_for_shop(shopdir: str):
    """その店で「店がまだ返事をしていない」やりとりを返す。[{id, name, at, body}]"""
    account, password, direct = load_credentials(shopdir)
    cli = HeavenClient(account, password, direct=direct)
    cli.login_and_select(shopdir)

    r = cli._get(f"/C8HimedecoChat.php?shopdir={shopdir}")
    if "C1Login.php" in r.url:
        raise LoginError("姫デコチャットの画面を開けませんでした")

    girls = []
    for block in BeautifulSoup(r.text, "html.parser").select(".accordion_header"):
        m = re.search(r"girls_id=(\d+)", str(block))
        if not m:
            continue
        nm = block.select_one("p.name")
        girls.append((int(m.group(1)), clean_name(nm.get_text(strip=True) if nm else "")))
        if len(girls) >= TOP_N:
            break

    out = []
    for gid, name in girls:
        try:
            t = cli.s.post(
                BASE + "/himedecochat/api/new_list",
                json={"girls_id": gid},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/C8HimedecoChat.php?shopdir={shopdir}"},
                timeout=30,
            )
            j = t.json()
        except Exception:
            continue
        talks = j.get("talk") or [] if j.get("result") == 0 else []
        if not talks:
            continue
        last = talks[-1]
        if last.get("sent_from_flg") != 2:       # 最後が店の発言 = 返事済み
            continue
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(last.get("body") or ""))).strip()
        if not body:
            continue
        out.append({"id": int(last.get("id") or 0), "name": name, "at": str(last.get("create_date") or ""), "body": body})
    return out


def notify(items):
    """スマホへ通知を送る。ntfyのトピック名は金庫(GitHub Secret)から受け取る。"""
    if not NTFY_TOPIC:
        print("通知先が未設定のため、送信は省略しました(NTFY_TOPIC)")
        return
    url = f"{NTFY_HOST}/{NTFY_TOPIC}"
    common = {"Click": BOARD_URL, "Tags": "speech_balloon"}

    def post(title, body, extra=None):
        h = {**common, "Title": title}
        h.update(extra or {})
        h = {k: v.encode("utf-8") for k, v in h.items()}
        try:
            requests.post(url, data=body.encode("utf-8"), headers=h, timeout=20).raise_for_status()
        except Exception as e:
            print("通知を送れませんでした:", type(e).__name__)

    if len(items) > MAX_NOTIFY:
        shops = {}
        for x in items:
            shops[x["shop"]] = shops.get(x["shop"], 0) + 1
        post("姫デコチャットに新しい連絡", f"{len(items)}件 / " + "、".join(f"{k} {v}件" for k, v in shops.items()),
             {"Priority": "high"})
        return
    for x in items:
        post(f'{x["shop"]} {x["name"]}', x["body"][:400])


def main():
    prev = load_seen()
    first_run = prev is None
    seen = set() if first_run else set(prev)

    fresh, all_ids, errors = [], set(), []
    for shopdir, label in SHOPS.items():
        try:
            items = pending_for_shop(shopdir)
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}")
            continue
        for x in items:
            all_ids.add(x["id"])
            if x["id"] not in seen:
                fresh.append({**x, "shop": label})
        print(f"{label}: 返事待ち {len(items)}件")

    if errors:
        print("確認できなかった店:", " / ".join(errors))

    # 見られなかった店の分を消してしまわないよう、前回の記録も残したまま足す
    save_seen(all_ids | seen)

    if first_run:
        print(f"初回のため、いまの{len(all_ids)}件は通知せずに記録しました(ここから先の新しい連絡だけ通知します)")
        return
    if not fresh:
        print("新しい連絡はありません")
        return
    fresh.sort(key=lambda x: x["at"])
    print(f"新しい連絡 {len(fresh)}件 → 通知します")
    notify(fresh)


if __name__ == "__main__":
    main()
