"""即ヒメの「待機中なのに本当は接客中」を直す（一希さん 2026-10-09）。

何をするか（約5分ごと。営業時間 9:00〜翌2:00 の間だけ）：
  1. CTIの本日スケジュールを読む（cti_schedule.py。読むだけ）
  2. 店ごとにヘブン管理画面の「即ヒメ登録」（C9StandbyGirlList.php）を開き、1人ずつ見る
  3. 直すのは「接客中になっていない子」だけ。接客中の子には触らない（待機中に戻すこともしない）
       ・出勤前（今がCTIの出勤開始より前）             → 接客中、終了＝出勤開始の時刻
       ・今まさに仕事の最中（「終了」が付いていない箱）  → 接客中、終了＝その箱の終了時刻
       ・60分以内（とろ〜りは30分以内）に仕事が始まる    → 接客中、終了＝その箱の終了時刻
       ・仮予約も仕事として数える。「終了」が付いた箱は終わった仕事
  3c. 追加（10/10 一希さん）：付ける終了時刻が、ヘブンの退勤時刻の60分前（とろ〜りは30分前）を過ぎていれば、
       次の仕事は取れない＝完売なので、終了時刻は退勤時刻にして、ヘブンで「受付終了」にする
       （例：オレンジ 沙織さん 退勤17:00・仕事が16:09終了 → 16:09でなく17:00→受付終了）
  3b. 追加（10/10 一希さん）：箱に「入室」が付いているのに「時間付け」が無い予約は、まだ入室後の時間付けが済んでいない。
       → ヘブンの終了時刻をCTIの箱の終了時刻に合わせ（接客中の子でもこの時だけは時刻を直す）、
         CTIのその予約のプレイ状況を「時間付け」にして保存する（CTIに書くのはこれだけ）
  4. 時間を付けた子だけ記録に残す（out/sokuhime.enc.json。名前を含むので暗号化。ボードで見る）

送り方は人と同じ（ブラウザ）：接客中ボタン（img.servingEndTime）を押す → 出てくる窓で時（#servingEndHourList）・分（#servingEndMinuteList）を選ぶ → OK（#popup_ok）。
裏からフォームを送る形（HTTP POST）は 10/10 の本番1回目で保存されなかった（出勤上げと同じ）ので使わない。
押した後にもう一度ページを読み、本当に接客中になったかを確かめる（なっていなければ「失敗」と記録）。
"""
import base64
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from heaven_http import BASE, COMMU_IDS, HeavenClient, load_credentials
from shift_auto import login as browser_login

# 読むのは HTTP（軽い。7店とも通る）。ブラウザは「押す店」だけ開く。
# ブラウザで続けて何店もログインすると、ヘブンの前の壁（Cloudflare）に "Sorry, you have been blocked" と止められる（10/10 に4店目で発生）ので、
# 押す必要がある店だけ・間を空けて開く
BROWSER_GAP_SEC = 20


def heaven_login(page, shopdir):
    """ブラウザでログインする。店舗IDが無い店（グループIDで入る店）は、入ったあとに店を選ぶ。"""
    _, _, direct = load_credentials(shopdir)
    ok = browser_login(page, shopdir)
    if not direct:
        page.goto(f"{BASE}/C1GroupLogin.php?commuId={COMMU_IDS[shopdir]}&login=1", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
    if not ok and "C1Login.php" in page.url:
        # 画面に出ている言葉だけ（IDやパスワードは出ない。数字は伏せる）
        txt = re.sub(r"\d", "＊", re.sub(r"\s+", " ", page.evaluate("() => document.body.innerText") or ""))[:240]
        raise RuntimeError(f"ログインできない（いまの場所 {page.url.replace(BASE, '')[:60]} 画面: {txt}）")

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
LOG_FILE = OUT_DIR / "sokuhime.enc.json"
SHOPS = {
    "cg_kirakira": "キラキラ学園", "s_matikado": "街角レディ", "mrs_orange": "オレンジな気持ち",
    "venus_okayama": "VENUS", "potya_reen": "ぽちゃりーん", "torori_angel": "とろ〜りAngel",
    "undercover": "UNDERCOVER",
}
LEAD_MIN = {"torori_angel": 30}        # 仕事が始まる何分前から接客中にするか（それ以外は60分）
LEAD_DEFAULT = 60
LAST_TAKE = {"torori_angel": 30}        # 退勤の何分前まで仕事を取れるか（とろ〜りは30分前まで。他は60分前まで）
LAST_TAKE_DEFAULT = 60
OPEN_MIN, CLOSE_MIN = 9 * 60, 26 * 60  # 動く時間帯：9:00〜翌2:00（0:00からの分）
KEEP_DAYS = 3                           # 記録を残す日数


# ---------- 時刻 ----------
def now_minutes(now):
    """その営業日の 0:00 からの分。深夜 0〜5時は前の日の続き（24時〜）として数える。"""
    v = now.hour * 60 + now.minute
    return v + 24 * 60 if now.hour < 5 else v


def hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def norm(s):
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"[\[【(（].*?[\]】)）]", "", t)
    return t.replace(" ", "").replace("　", "").replace("…", "")


# ---------- ヘブンの即ヒメ登録の画面を読む ----------
def parse_standby(html):
    """1人分の箱 → {id, name, shift, serving(接客中か), end(接客終了の時刻の文字), waiting(待機中か)}。
    ついでにフォームの隠し項目と、時の選択肢（送る時刻の形を決める）も返す。"""
    s = BeautifulSoup(html, "html.parser")
    boxes = []
    # 箱の表は sokuhimegirlbox2（待機中の子）のほか、sokuhimegirlbox・sokuhimegirlbox3（接客中の子）がある（10/10 下調べ10）。
    # 接客中の子の接客中ボタンは img.servingEndTimeUpdate（name=今の終了時刻）。待機中の子は img.servingEndTime
    for t in s.find_all("table", class_=re.compile(r"^sokuhimegirlbox")):
        img = t.find("img", class_="servingEndTime") or t.find("img", class_="servingEndTimeUpdate")
        if img is None or not img.get("id"):
            continue
        name_td = t.find("td", style=re.compile(r"width:\s*105px"))
        name = name_td.get_text(" ", strip=True) if name_td else ""
        shift = ""
        for td in t.find_all("td"):
            m = re.search(r"(\d{1,2}:\d{2})\s*[～〜~]\s*(\d{1,2}:\d{2})", td.get_text(" ", strip=True))
            if m and td.get("colspan"):
                shift = f"{m.group(1)}-{m.group(2)}"
                break
        wait = t.find("img", class_="waitingUpdate")
        boxes.append({
            "id": img["id"], "name": name, "shift": shift, "table": " ".join(t.get("class") or []),
            "serving": "sekkyaku_on" in (img.get("src") or ""), "end": (img.get("name") or "").strip(),
            "waiting": bool(wait is not None and "taiki_on" in (wait.get("src") or "")),
        })
    form = s.find("form", attrs={"name": "sokuhimeForm"}) or next((f for f in s.find_all("form") if f.find("input", {"name": "servingEndTime"})), None)
    hidden = {}
    if form is not None:
        for e in form.find_all("input"):
            if e.get("name"):
                hidden[e["name"]] = e.get("value") or ""
    hours = []
    hl = s.find("input", id="servingEndHourHtmlList")
    if hl is not None:
        hours = re.findall(r'value="([^"]*)"', hl.get("value") or "")
    return boxes, hidden, hours


def time_for_form(end_min, hours):
    """送る「接客終了時刻」の文字。画面の時の選択肢に 24・25… があればそのまま、無ければ 24時間制に畳む。"""
    h, m = end_min // 60, end_min % 60
    if hours and f"{h:02d}" in hours:
        return f"{h:02d}:{m:02d}"
    if hours and str(h) in hours:
        return f"{h}:{m:02d}"
    return f"{h % 24:02d}:{m:02d}"


# ---------- 直すかどうかを決める ----------
def decide(box, person, now_min, lead, last=LAST_TAKE_DEFAULT):
    """返す: (終了の分, 理由) か None。person は cti_schedule の1人分（無ければ None）。
    last＝退勤の何分前まで仕事を取れるか（予約が無くても、それを過ぎていれば受付終了にする。一希さん 10/10）。"""
    if box["serving"]:
        return None                                   # 接客中の子には触らない
    if person is None:
        return None
    work = person.get("work")
    if work and now_min < work[0]:
        return work[0], "出勤前"                     # 出勤前は、出勤の時刻まで接客中にしておく
    for b in person.get("bookings", []):
        if "終了" in b["flags"]:
            continue
        if b["s"] <= now_min < b["e"]:
            return b["e"], "接客中" + ("（入室）" if "入室" in b["flags"] else "")
        if now_min < b["s"] <= now_min + lead:
            return b["e"], f"{b['s'] - now_min}分後に開始"
    # 予約が残っていない子：退勤まで last 分を切っていたら、もう仕事は取れない → 受付終了（終了＝退勤）
    remaining = [b for b in person.get("bookings", []) if "終了" not in b["flags"] and b["e"] > now_min]
    if not remaining:
        he = shift_end_min(box) or (work[1] if work else None)
        if he and now_min < he < now_min + last:
            return he, f"予約なし・退勤{hhmm(he)}まで{he - now_min}分→受付終了"
    return None


def shift_end_min(box):
    """ヘブンの箱の出勤「12:00-17:00」の終わりの分（翌日は +24h）。読めなければ None。"""
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", box.get("shift") or "")
    if not m:
        return None
    s_, e_ = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    return e_ + 24 * 60 if e_ <= s_ else e_


def final_end(person, box, end_min, shopdir=None):
    """終了がヘブンの退勤の60分前（とろ〜り30分前）を過ぎていれば、終了＝退勤 → 受付終了。返す: (終了の分, 付け足す理由)。
    退勤はヘブンの箱の出勤の終わり。読めなければCTIの出勤の終わり。"""
    he = shift_end_min(box)
    work = (person or {}).get("work")
    if he is None and work:
        he = work[1]
    if he is None:
        return end_min, ""
    last = LAST_TAKE.get(shopdir, LAST_TAKE_DEFAULT)
    if he - end_min >= last:
        return end_min, ""
    return max(he, end_min), f"→受付終了（退勤{hhmm(he)}の{last}分前を過ぎて次が入らない）"


def timed_fix(person, now_min):
    """「入室」なのに「時間付け」が無い予約（終了していない・まだ終わっていない）→ その予約。無ければ None。"""
    if person is None:
        return None
    for b in person.get("bookings", []):
        if "入室" in b["flags"] and "時間付け" not in b["flags"] and "終了" not in b["flags"] and b["e"] > now_min:
            return b
    return None


def match(boxes, people):
    """ヘブンの箱 → CTIの人。名前を正規化して突き合わせる（前方一致も許す）。"""
    by = {}
    for p in people:
        by.setdefault(norm(p["name"]), p)
    out = {}
    for b in boxes:
        n = norm(b["name"])
        p = by.get(n)
        if p is None and n:
            cands = [v for k, v in by.items() if k.startswith(n) or n.startswith(k)]
            p = cands[0] if len(cands) == 1 else None
        out[b["id"]] = p
    return out


# ---------- ヘブンに送る（ブラウザで、人と同じ所を押す） ----------
def standby_url(shopdir):
    return f"{BASE}/C9StandbyGirlList.php?shopdir={shopdir}"


def read_standby(page, shopdir):
    page.goto(standby_url(shopdir), wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    return parse_standby(page.content())


def set_serving(page, shopdir, box, end_text):
    """接客中ボタン → 時・分を選ぶ → OK。返す: (本当に接客中になったか, メモ)。"""
    hh, mm = end_text.split(":")
    btn = page.query_selector(f'img.servingEndTime[id="{box["id"]}"], img.servingEndTimeUpdate[id="{box["id"]}"]')
    if btn is None:
        return False, "接客中ボタンが見つからない"
    btn.click()
    try:
        page.wait_for_selector("#servingEndHourList", state="visible", timeout=8000)
    except Exception:
        return False, "時間の入力窓が開かない"
    page.select_option("#servingEndHourList", hh)
    page.select_option("#servingEndMinuteList", mm)
    page.wait_for_timeout(300)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        page.click("#popup_ok")
    page.wait_for_timeout(1000)
    boxes, _, _ = read_standby(page, shopdir)
    after = next((b for b in boxes if b["id"] == box["id"]), None)
    if after is None:
        return False, "押した後に箱が見つからない"
    if after["serving"]:
        return True, after["end"]
    return False, "押したが接客中にならなかった"


# ---------- 記録（名前を含むので暗号化） ----------
def _key():
    k = os.environ.get("TODO_KEY", "")
    return base64.b64decode(k) if k else None


def load_log():
    key = _key()
    if key is None:
        return []
    try:
        src = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else requests.get(f"{PUBLIC_RAW}/sokuhime.enc.json", timeout=20).text
        enc = json.loads(src)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = AESGCM(key).decrypt(base64.b64decode(enc["iv"]), base64.b64decode(enc["ct"]), None)
        return json.loads(raw).get("entries", [])
    except Exception:
        return []


def save_log(entries, now):
    key = _key()
    if key is None:
        return
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    entries = [e for e in entries if e.get("at", "") >= cutoff][-600:]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    payload = {"generated_at": now.isoformat(), "entries": entries}
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps({"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode(),
                                    "generated_at": now.isoformat()}), encoding="utf-8")


# ---------- 店の印の確かめ（見るだけの時だけ） ----------
def badge_check(people, now):
    """CTIの店の印ごとに、その子たちの名前がヘブンのどの店の出勤一覧にあるかを数える（名前は出さない）。"""
    from collections import Counter
    import cti_schedule
    combos = Counter(cti_schedule.ROW_BADGES)
    for (shop, badges), n in sorted(combos.items(), key=lambda kv: (str(kv[0][0]), -kv[1]))[:40]:
        print(f"  店の印の当て方: {shop or '?'} ← {list(badges)} ×{n}")
    try:
        from cityheaven_api import SHOPS_API, fetch_shift_list, parse_girls
    except Exception as e:
        print("  ヘブンの出勤一覧が読めない:", type(e).__name__)
        return
    heaven = {}
    for shopdir, info in SHOPS_API.items():
        try:
            heaven[shopdir] = {norm(g["name"]) for g in parse_girls(fetch_shift_list(info["shopid"], info["apikey"], base_day=now.strftime("%Y%m%d")))}
        except Exception as e:
            print(f"  {shopdir}: 出勤一覧が読めない {type(e).__name__}")
    for shop in sorted({p["shop"] or "?" for p in people}):
        names = [norm(p["name"]) for p in people if (p["shop"] or "?") == shop]
        hits = {sd: sum(1 for nme in names if nme in hv) for sd, hv in heaven.items()}
        hits = {k: v for k, v in hits.items() if v}
        print(f"  CTIで {shop} の {len(names)}人 → ヘブンの出勤一覧にいる店: {hits}")


# ---------- 本体 ----------
def run(now=None, dry=False, only=None):
    now = now or datetime.now(JST)
    nm = now_minutes(now)
    if not (OPEN_MIN <= nm < CLOSE_MIN):
        print(f"即ヒメ: 営業時間外（{now.strftime('%H:%M')}）なので何もしない")
        return 0
    import cti_schedule
    cti = cti_schedule.CTI().open()
    try:
        return _run(cti, now, nm, dry, only)
    finally:
        cti.close()


def _run(cti, now, nm, dry, only):
    import cti_schedule
    people, head = cti.read(now)
    print(f"即ヒメ: CTI {head} → {cti_schedule.summary(people)}")
    unknown = sorted({b for b in cti_schedule.LAST_BADGES if b not in cti_schedule.SHOP_BADGE}) if hasattr(cti_schedule, "LAST_BADGES") else []
    if dry:
        badge_check(people, now)
    entries = load_log() if not dry else []
    new = []
    # ブラウザの土台はCTIのもの（cti.pw）を使い回す（Playwright を二重に起動すると止まる）
    browser = cti.pw.chromium.launch(headless=True)
    browser_logins = []
    for shopdir, label in SHOPS.items():
        if only and shopdir not in only:
            continue
        # 店の印が無い子（複数の店に出ている子。印が「4」「1」など）は、どの店でも名前で突き合わせる
        mine = [p for p in people if p["shop"] == shopdir] + [p for p in people if p["shop"] is None]
        if not [p for p in mine if p["shop"] == shopdir]:
            print(f"  {label}: CTIに今日の子がいない")
            continue
        try:
            a, p_, d = load_credentials(shopdir)
            cli = HeavenClient(a, p_, direct=d)
            cli.login_and_select(shopdir)
            boxes, hidden, hours = parse_standby(cli._get(f"/C9StandbyGirlList.php?shopdir={shopdir}").text)
        except Exception as e:
            print(f"  {label}: ヘブンが読めない {type(e).__name__} {str(e)[:120]}")
            continue
        if dry:
            from collections import Counter
            print(f"  {label}: 表の種類 {dict(Counter(b['table'] for b in boxes))}、接客中 {sum(1 for b in boxes if b['serving'])}人")
        pm = match(boxes, mine)
        lead = LEAD_MIN.get(shopdir, LEAD_DEFAULT)
        n_set = n_skip = n_nomatch = 0
        def ensure_page():
            if st["page"] is None:
                if browser_logins:
                    time.sleep(BROWSER_GAP_SEC)
                st["page"] = browser.new_context(locale="ja-JP", viewport={"width": 1300, "height": 1000}).new_page()
                heaven_login(st["page"], shopdir)
                browser_logins.append(shopdir)
                read_standby(st["page"], shopdir)
            return st["page"]

        st = {"page": None}
        for b in boxes:
            person = pm.get(b["id"])
            if person is None and not b["serving"]:
                n_nomatch += 1
            tb = timed_fix(person, nm)
            if tb is not None:
                # 入室なのに時間付けが無い：ヘブンの終了をCTIの終了に合わせ、CTIを時間付けにする
                fe, fwhy = final_end(person, b, tb["e"], shopdir)
                end_text = time_for_form(fe, hours)
                entry = {"at": now.isoformat(timespec="minutes"), "shop": label, "name": b["name"], "id": b["id"], "kind": "時間付け",
                         "before": ("接客中 " + b["end"]) if b["serving"] else ("待機中" if b["waiting"] else "（状態なし）"),
                         "end": end_text, "why": "入室・時間付けがまだ" + fwhy,
                         "cti": [(hhmm(x["s"]) + "-" + hhmm(x["e"]) + ("/".join([""] + x["flags"]) if x["flags"] else "")) for x in person["bookings"]],
                         "work": (hhmm(person["work"][0]) + "-" + hhmm(person["work"][1])) if person.get("work") else ""}
                if dry:
                    # 見るだけでも、CTIの「編集」まで開けるかは確かめる（保存はしない）
                    try:
                        cok, cnote = cti.mark_timed(tb["rid"], save=False, now=now)
                    except Exception as e:
                        cok, cnote = False, f"CTIで押せなかった {type(e).__name__} {str(e)[:80]}"
                    entry.update(ok=None, note="見るだけ", cti_ok=cok, cti_note=cnote)
                else:
                    if b["serving"] and b["end"] == end_text:
                        ok, note = True, "ヘブンは同じ時刻"
                    else:
                        try:
                            ok, note = set_serving(ensure_page(), shopdir, b, end_text)
                        except Exception as e:
                            ok, note = False, f"押せなかった {type(e).__name__} {str(e)[:80]}"
                    try:
                        cti_ok, cti_note = cti.mark_timed(tb["rid"], now=now)
                    except Exception as e:
                        cti_ok, cti_note = False, f"CTIで押せなかった {type(e).__name__} {str(e)[:80]}"
                    entry.update(ok=ok, note=note, cti_ok=cti_ok, cti_note=cti_note)
                    time.sleep(1.0)
                new.append(entry)
                n_set += 1
                continue
            dec = decide(b, person, nm, lead, LAST_TAKE.get(shopdir, LAST_TAKE_DEFAULT))
            if dec is None:
                n_skip += 1
                continue
            end_min, why = dec
            if end_min <= nm:
                n_skip += 1
                continue
            end_min, fwhy = final_end(person, b, end_min, shopdir)
            why += fwhy
            end_text = time_for_form(end_min, hours)
            entry = {"at": now.isoformat(timespec="minutes"), "shop": label, "name": b["name"], "id": b["id"],
                     "before": "待機中" if b["waiting"] else "（状態なし）", "end": end_text, "why": why,
                     "cti": [(hhmm(x["s"]) + "-" + hhmm(x["e"]) + ("/".join([""] + x["flags"]) if x["flags"] else "")) for x in person["bookings"]],
                     "work": (hhmm(person["work"][0]) + "-" + hhmm(person["work"][1])) if person.get("work") else ""}
            if dry:
                entry["ok"] = None
                entry["note"] = "見るだけ"
            else:
                try:
                    ok, note = set_serving(ensure_page(), shopdir, b, end_text)
                except Exception as e:
                    ok, note = False, f"押せなかった {type(e).__name__} {str(e)[:80]}"
                entry["ok"], entry["note"] = ok, note
                time.sleep(1.0)
            new.append(entry)
            n_set += 1
        print(f"  {label}: 箱{len(boxes)} → 直した{n_set}・そのまま{n_skip}・CTIに名前が無い{n_nomatch}" + (f"（時の選択肢 {hours[:3]}…{hours[-2:]}）" if hours else "（時の選択肢が読めない）"))
        if st["page"] is not None:
            st["page"].context.close()
    browser.close()
    if dry:
        for e in new:
            print("   ", e["shop"], "＊" * min(len(e["name"]), 4), e["before"], "→ 接客中", e["end"], e["why"], (f"＋CTIを時間付けに（確かめ: {e.get('cti_ok')} {e.get('cti_note')}）" if e.get("kind") == "時間付け" else ""))
        return 0
    if new:
        save_log(entries + new, now)
        print(f"即ヒメ: {len(new)}人に時間を付けた（失敗 {sum(1 for e in new if not e['ok'])}、CTIの時間付け {sum(1 for e in new if e.get('cti_ok'))}/{sum(1 for e in new if e.get('kind') == '時間付け')}）")
    else:
        print("即ヒメ: 直す子はいなかった")
    return 0


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    try:
        raise SystemExit(run(dry=dry, only=only))
    except Exception as e:
        print(f"即ヒメ: 失敗 {type(e).__name__}: {str(e)[:200]}")
        raise SystemExit(1)
