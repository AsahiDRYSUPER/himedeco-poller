"""CTI（風俗CTIv2）の「本日スケジュール」を読む（読むだけ。CTIは何も書き換えない）。

なぜ：ヘブンの即ヒメで「待機中なのに、本当は接客中」の子を自動で直すため（一希さん 2026-10-09）。
どう読むか：人と同じようにブラウザでCTIを開いてログインし（金庫 CTI_LOGIN_URL・CTI_PASSWORD）、
  本日スケジュール（#<事業所>/schedule?date=YYYYMMDD）の画面に出ている
    行（.schedule-row）：女の子の名前（.hime-name）・出勤時間（.work-time 例「10:00-翌3:00Up」）・店の印（.hime-badge「KG」「OR」…）
    予約の箱（.resv-item）：時刻（.resv-time 例「13:25-15:55」「翌0:30-翌2:30」）・印（.mark-badge「入室」「終了」「仮予約」…）
  を読む。箱は行の外に重ねて描かれているので、行との対応は縦の位置で取る（10/9 の下調べ）。
返す形：[{"shop": shopdir, "name": 名前, "work": (開始分, 終了分), "bookings": [{"s": 開始分, "e": 終了分, "flags": ["入室"...]}]}]
  分は「その日の 0:00 からの分」。翌0:30 は 24*60+30。
公開リポジトリなので、名前や時刻はログに出さない（出すのは店ごとの人数と件数だけ）。
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
OFFICE = os.environ.get("CTI_OFFICE", "T5GFow")
OFFICE_URL = f"https://cti2.fuzoku-fan.jp/office/#{OFFICE}"
# 行の店の印（CTIの hime-badge の文字）→ ヘブンの shopdir。10/9 の下調べで見えたのは KG・OR・UC・VE・ぽちゃ。
# 街角・とろ〜りの印はまだ見ていないので、文字の一部で当てる（読めなかった印は LAST_BADGES に残して、ログで分かるようにする）
SHOP_BADGE = {"KG": "cg_kirakira", "OR": "mrs_orange", "UC": "undercover", "VE": "venus_okayama", "ぽちゃ": "potya_reen",
              "PO": "potya_reen", "MC": "s_matikado", "MA": "s_matikado", "ML": "s_matikado", "TO": "torori_angel", "TA": "torori_angel"}
SHOP_HINT = (("ぽちゃ", "potya_reen"), ("街", "s_matikado"), ("とろ", "torori_angel"), ("トロ", "torori_angel"),
             ("キラ", "cg_kirakira"), ("オレ", "mrs_orange"), ("アンカバ", "undercover"), ("UNDER", "undercover"), ("VENUS", "venus_okayama"))
LAST_BADGES = set()


def badge_shop(badges):
    for b in badges:
        if b in SHOP_BADGE:
            return SHOP_BADGE[b]
    for b in badges:
        for hint, shop in SHOP_HINT:
            if hint.lower() in b.lower():
                return SHOP_BADGE.setdefault(b, shop)
    return None

READ_JS = r"""() => {
  const rows = Array.from(document.querySelectorAll('.schedule-row')).map(rw => {
    const r = rw.getBoundingClientRect();
    const name = rw.querySelector('.hime-name'); const wt = rw.querySelector('.work-time');
    const badges = Array.from(rw.querySelectorAll('.hime-cell .hime-badge, .hime-cell .mark-badge')).map(b => b.innerText.trim());
    return {top: r.top, bottom: r.bottom, name: name ? name.innerText.trim() : '', work: wt ? wt.innerText.trim() : '', badges};
  }).filter(x => x.name);
  const items = Array.from(document.querySelectorAll('.resv-item')).map(it => {
    const r = it.getBoundingClientRect();
    return {y: (r.top + r.bottom) / 2, time: (it.querySelector('.resv-time') || {innerText: ''}).innerText.trim(),
            flags: Array.from(it.querySelectorAll('.mark-badge, .hime-badge')).map(b => b.innerText.trim()), text: (it.innerText || '').slice(0, 200)};
  });
  const head = (document.body.innerText.match(/\d{4}年\d{2}月\d{2}日\([^)]*\)/) || [''])[0];
  return {rows, items, head};
}"""


def minutes(t):
    """「13:25」→ 805、「翌0:30」→ 1470。読めなければ None。"""
    m = re.match(r"^(翌)?\s*(\d{1,2}):(\d{2})$", (t or "").strip())
    if not m:
        return None
    v = int(m.group(2)) * 60 + int(m.group(3))
    if m.group(1):
        v += 24 * 60
    elif v < 5 * 60:                      # 「2:30」のように翌の印が無い深夜も翌日扱い（営業は10時〜翌2時）
        v += 24 * 60
    return v


def span(t):
    m = re.match(r"^(翌?\s*\d{1,2}:\d{2})\s*[-〜~]\s*(翌?\s*\d{1,2}:\d{2})", (t or "").strip())
    if not m:
        return None
    s, e = minutes(m.group(1)), minutes(m.group(2))
    if s is None or e is None:
        return None
    if e <= s:
        e += 24 * 60
    return s, e


def login(page, login_url, password):
    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("input[type=password], a:has-text('レポート')", timeout=40000)
    if page.query_selector("input[type=password]"):
        boxes = [b for b in page.query_selector_all("input[type=password]") if b.is_visible()]
        if not boxes:
            raise RuntimeError("パスワードの欄が見えない")
        boxes[-1].fill(password)
        btn = page.query_selector("button:has-text('ログイン'), button[type=submit], input[type=submit]")
        if btn:
            btn.click()
        else:
            boxes[-1].press("Enter")
        page.wait_for_selector("a:has-text('レポート')", timeout=40000)
    if "/office/" not in page.url:
        page.goto(f"{OFFICE_URL}/schedule", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("a:has-text('レポート')", timeout=40000)


def parse(raw):
    """画面から読んだ生の形 → 店ごとの女の子と予約。"""
    rows = sorted(raw["rows"], key=lambda r: r["top"])
    out = []
    for r in rows:
        LAST_BADGES.update(b for b in r["badges"] if b and len(b) <= 6)
        shop = badge_shop(r["badges"])
        out.append({"shop": shop, "name": r["name"], "work": span(r["work"]), "top": r["top"], "bottom": r["bottom"], "bookings": []})
    for it in raw["items"]:
        sp = span(it["time"])
        if sp is None:
            continue
        host = next((o for o in out if o["top"] - 2 <= it["y"] <= o["bottom"] + 2), None)
        if host is None:
            continue
        flags = [f for f in it["flags"] if f in ("入室", "終了", "仮予約", "予約", "本", "時間付け")]
        text = it.get("text", "")
        if "入室" in text and "入室" not in flags:
            flags.append("入室")
        if "終了" in text and "終了" not in flags:
            flags.append("終了")
        host["bookings"].append({"s": sp[0], "e": sp[1], "flags": flags})
    for o in out:
        o["bookings"].sort(key=lambda b: b["s"])
        del o["top"], o["bottom"]
    return out, raw.get("head", "")


def read_today(now=None):
    """CTIにログインして本日スケジュールを読む。返す: (女の子の一覧, 見出しの日付)。"""
    from playwright.sync_api import sync_playwright
    password = os.environ.get("CTI_PASSWORD", "").strip("\r\n")
    login_url = os.environ.get("CTI_LOGIN_URL", "").strip()
    if not password or not login_url.startswith("https://cti2.fuzoku-fan.jp/"):
        # 中身は出さない。どちらがおかしいかだけ分かるようにする
        hint = (f"パスワード{len(password)}字、URL{len(login_url)}字"
                f"（https始まり:{login_url.startswith('https://')} / cti2を含む:{'cti2.fuzoku-fan.jp' in login_url}）")
        raise RuntimeError("CTI_PASSWORD / CTI_LOGIN_URL が無いか形が違う: " + hint)
    now = now or datetime.now(JST)
    # 営業日は 10時〜翌2時。深夜 0〜5時は前の日のスケジュールを見る
    day = now - timedelta(hours=5)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1600, "height": 1200}).new_page()
        try:
            login(page, login_url, password)
            page.evaluate(f"location.hash = '#{OFFICE}/schedule?date={day.strftime('%Y%m%d')}'")
            raw = None
            for _ in range(30):
                time.sleep(0.5)
                raw = page.evaluate(READ_JS)
                if raw["rows"] and raw["head"].startswith(day.strftime("%Y年%m月%d日")):
                    time.sleep(1.0)
                    raw2 = page.evaluate(READ_JS)
                    if len(raw2["items"]) == len(raw["items"]):
                        raw = raw2
                        break
            if not raw or not raw["rows"]:
                raise RuntimeError("本日スケジュールの行が出ない")
        finally:
            browser.close()
    return parse(raw)


def summary(people):
    by = {}
    for p in people:
        k = p["shop"] or "?"
        by.setdefault(k, [0, 0])
        by[k][0] += 1
        by[k][1] += len(p["bookings"])
    return "、".join(f"{k} {v[0]}人/{v[1]}件" for k, v in sorted(by.items()))


if __name__ == "__main__":
    import sys
    people, head = read_today()
    print(head, "→", summary(people))
    if "--dump" in sys.argv:          # 手元で確かめる時だけ（名前が出るので、ログには出さない）
        for p in people:
            print(p["shop"], p["name"], p["work"], [(b["s"], b["e"], b["flags"]) for b in p["bookings"]])
