"""即ヒメ（ヘブンの待機表示）を、CTIの本日スケジュールに合わせて直す。
CTIを読む → 店ごとに即ヒメ登録の画面を見る → 直す決まり（鍵つき sealed/ops_sokuhime.enc）に当てはまる子だけ接客中にする。
決まりが開けない時は直さない（ログに1行・生きている印に error）。時間を付けた記録は名前を含むので暗号化（sokuhime.enc.json）。
押し方は人と同じ（ブラウザ）。押したあと読み直して、本当に変わったかを確かめる。
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
import data_box     # 生きている印（sokuhime_status）は鍵つきで dataブランチへ（2026-10-25）
import ops_hours    # 動く時間は鍵つきの時間帯の決まり（2026-10-26）
import secret_box   # 直す決まり（店ごとの分数）は鍵つき（2026-10-25）

# 読むのは HTTP（軽い。7店とも通る）。ブラウザは「押す店」だけ開く。
# ブラウザで続けて何店もログインすると、ヘブンの前の壁（Cloudflare）に "Sorry, you have been blocked" と止められる（10/10 に4店目で発生）ので、
# 押す必要がある店だけ・間を空けて開く
BROWSER_GAP_SEC = 8


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
STATUS_NAME = "sokuhime_status"   # 毎回の「生きている印」（名前なし）。鍵つき out/sokuhime_status.enc.json。ボードと見張りが読む
SHOPS = {
    "cg_kirakira": "キラキラ学園", "s_matikado": "街角レディ", "mrs_orange": "オレンジな気持ち",
    "venus_okayama": "VENUS", "potya_reen": "ぽちゃりーん", "torori_angel": "とろ〜りAngel",
    "undercover": "UNDERCOVER",
}
# 直す決まり（鍵つき sealed/ops_sokuhime.enc。正本は非公開側）。開けない時は None ＝直さない
_OPS = secret_box.data("ops_sokuhime") or {}
LEAD_MIN = dict(_OPS.get("lead_min") or {})             # 店ごとの分数（意味は鍵つきの決まりの説明）
LEAD_DEFAULT = _OPS.get("lead_default")                  # 上に無い店
LAST_TAKE = dict(_OPS.get("last_take") or {})           # 店ごとの分数（意味は鍵つきの決まりの説明）
LAST_TAKE_DEFAULT = _OPS.get("last_take_default")        # 上に無い店
REASONS = dict(_OPS.get("reasons") or {})               # 理由の記号 → 記録に出す言葉（無ければ記号のまま）
# 動く時間帯（0:00からの分。翌日は24時〜）。鍵つきの "hours" の "fix"。開けない時は None ＝直さない
OPEN_MIN, CLOSE_MIN = ops_hours.span("fix", dict(_OPS.get("hours") or {})) or (None, None)
CLOSE_CODES = ("idle_close", "close")                    # 受付終了にする理由の記号


def ops_ready():
    """直す決まりが開けているか（数と時間帯がそろっているか）"""
    return (isinstance(LEAD_DEFAULT, int) and isinstance(LAST_TAKE_DEFAULT, int)
            and isinstance(OPEN_MIN, int) and isinstance(CLOSE_MIN, int))


def why_text(parts):
    """理由の記号の並び [(記号, 値)] → 記録に出す文。言葉は鍵つきの "reasons"（{min} などを差し込む）。無ければ記号のまま"""
    out = []
    for code, kw in parts:
        t = REASONS.get(code)
        if isinstance(t, str) and t:
            try:
                out.append(t.format(**kw))
                continue
            except Exception:
                pass
        out.append((" " if out else "") + code + ("(" + ",".join(f"{k}={v}" for k, v in kw.items()) + ")" if kw else ""))
    return "".join(out)


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
    # ヘブンの即ヒメの箱は名前が「◯◯◯◯【優…」のように途中で切れる。閉じていない【 から後ろも外す（10/11）
    t = re.sub(r"[\[【(（][^\]】)）]*$", "", t)
    # ヶ／ケ・ヵ／カ の違いは同じとみなす
    t = t.replace("ヶ", "ケ").replace("ヵ", "カ")
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
# 決まり（鍵つき）に沿って、接客中にするか・終わりの時刻を決める。理由は記号で返す（言葉は why_text）。
def decide(box, person, now_min, lead, last=None):
    """返す: (終了の分, [(理由の記号, 値)]) か None。person は cti_schedule の1人分（無ければ None）。
    lead・last は決まりの分数（鍵つき。last が無ければ決まりの既定）。"""
    if last is None:
        last = LAST_TAKE_DEFAULT
    if box["serving"]:
        return None                                   # 接客中の子には触らない
    if person is None or lead is None:
        return None
    work = person.get("work")
    if work and now_min < work[0]:
        return work[0], [("pre", {})]
    for b in person.get("bookings", []):
        if "終了" in b["flags"]:
            continue
        if b["s"] <= now_min < b["e"]:
            return b["e"], [("serving_in" if "入室" in b["flags"] else "serving", {})]
        if now_min < b["s"] <= now_min + lead:
            return b["e"], [("soon", {"min": b["s"] - now_min})]
    remaining = [b for b in person.get("bookings", []) if "終了" not in b["flags"] and b["e"] > now_min]
    if not remaining and last is not None:
        he = shift_end_min(box) or (work[1] if work else None)
        if he and now_min < he <= now_min + last:
            return he, [("idle_close", {"end": hhmm(he), "min": he - now_min})]
    return None


def shift_end_min(box):
    """ヘブンの箱の出勤「12:00-17:00」の終わりの分（翌日は +24h）。読めなければ None。"""
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", box.get("shift") or "")
    if not m:
        return None
    s_, e_ = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    return e_ + 24 * 60 if e_ <= s_ else e_


def chain_end(person, end_min, shopdir=None):
    """決まり（鍵つき）に沿って、終わりの時刻を後ろの予約へつなげる。返す: (終了の分, つないだ本数)。"""
    gap = LAST_TAKE.get(shopdir, LAST_TAKE_DEFAULT)
    e, n = end_min, 0
    if gap is None:
        return e, n
    for b in sorted((person or {}).get("bookings", []), key=lambda x: x["s"]):
        if "終了" in b["flags"] or b["e"] <= e:
            continue
        if b["s"] - e < gap:
            e, n = max(e, b["e"]), n + 1
        else:
            break
    return e, n


def final_end(person, box, end_min, shopdir=None):
    """決まり（鍵つき）に沿って終わりの時刻を決める。返す: (終了の分, 付け足す理由 [(記号, 値)])。
    退勤はヘブンの箱の出勤の終わり。読めなければCTIの出勤の終わり。"""
    end_min, n = chain_end(person, end_min, shopdir)
    parts = [("chain", {"n": n, "end": hhmm(end_min)})] if n else []
    he = shift_end_min(box)
    work = (person or {}).get("work")
    if he is None and work:
        he = work[1]
    if he is None:
        return end_min, parts
    last = LAST_TAKE.get(shopdir, LAST_TAKE_DEFAULT)
    if last is None or he - end_min > last:
        return end_min, parts
    return max(he, end_min), parts + [("close", {"end": hhmm(he), "min": last})]


def timed_fix(person, now_min):
    """「入室」なのに「時間付け」が無い予約（終了していない・まだ終わっていない）→ その予約。無ければ None。"""
    if person is None:
        return None
    for b in person.get("bookings", []):
        if "入室" in b["flags"] and "時間付け" not in b["flags"] and "終了" not in b["flags"] and b["e"] > now_min:
            return b
    return None


def aliases(name):
    """CTIの表記名は、複数の店に出ている子だと「◯◯【業界未経験】,△△RSP」のように店ごとの名前がカンマでつながる
    （女子一覧の「所属店舗(女子名)」と同じ）。カンマで分けて、どの名前でも合うようにする。"""
    out = []
    for part in re.split(r"[,、，]", name or ""):
        n = norm(part)
        if n and n not in out:
            out.append(n)
    return out or [norm(name)]


def match(boxes, people):
    """ヘブンの箱 → CTIの人。名前を正規化して突き合わせる（カンマ区切りの別名も・前方一致も許す）。"""
    by = {}
    for p in people:
        for a in aliases(p["name"]):
            by.setdefault(a, p)
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
    try:
        btn.click(timeout=8000)
    except Exception:
        btn.evaluate("e => e.click()")     # 何かがかぶって押せない時は、ボタンに直接（10/11 ある子で30秒待って失敗）
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
        page.wait_for_timeout(2000)        # 画面の描き直しが遅い時があるので、もう一度だけ読む
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


# ---------- 生きている印と、失敗の知らせ ----------
def load_status():
    """手元 → dataブランチ（sokuhime_status.enc.json → 無ければ移行前の sokuhime_status.json）の順"""
    try:
        v = data_box.load(STATUS_NAME, default={}, out_dir=OUT_DIR, base=PUBLIC_RAW, timeout=15)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def write_status(now, **fields):
    """毎回書く（鍵つき）。ボードが「最終確認 ◯分前」を出し、止まっていれば赤くする。"""
    st = load_status()
    st.update({"at": now.isoformat(timespec="seconds")}, **fields)
    data_box.write(STATUS_NAME, st, OUT_DIR, generated_at=now.isoformat(timespec="seconds"))
    return st


def notify_failures(now, st, new):
    """CTIかヘブンで押せなかった子がいたら、スマホに知らせる（30分に1回まで。名前は入れない。中身はボードで）。
    既定では送らない（環境変数 SOKUHIME_NTFY=1 の時だけ送る）。押せなかった子はボードの「即ヒメ」で見られる。"""
    if os.environ.get("SOKUHIME_NTFY", "").strip() != "1":
        return
    fails = [e for e in new if e.get("ok") is False or e.get("cti_ok") is False]
    if not fails:
        return
    last = st.get("notified_at", "")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < 1800:
        return
    try:
        from watch_chat import post_ntfy
        post_ntfy("即ヒメ：押せなかった子がいます", f"{len(fails)}件（ヘブン {sum(1 for e in fails if e.get('ok') is False)}・CTI {sum(1 for e in fails if e.get('cti_ok') is False)}）。ボードの「即ヒメ」で中身を確認してください", {"Tags": "warning"})
        st["notified_at"] = now.isoformat(timespec="seconds")
        data_box.write(STATUS_NAME, st, OUT_DIR, generated_at=now.isoformat(timespec="seconds"))
    except Exception as e:
        print("通知できず:", type(e).__name__)


# ---------- 本体 ----------
def run(now=None, dry=False, only=None):
    now = now or datetime.now(JST)
    nm = now_minutes(now)
    if not ops_ready():
        # 直す決まり（鍵つき）が開けない：直さない。見張り・ボードに分かるよう生きている印に error を残す
        print("即ヒメ: 直す決まり（鍵つき）が開けないので直さない（金庫の KNOWLEDGE_KEY を確かめる）")
        if not dry:
            write_status(now, open=True, error="直す決まり（鍵つき）が開けない", set=0, heaven_fail=0, cti_targets=0, cti_ok=0, cti_fail=0)
        return 0
    if not (OPEN_MIN <= nm < CLOSE_MIN):
        print(f"即ヒメ: 動く時間の外（{now.strftime('%H:%M')}）なので何もしない")
        write_status(now, open=False, set=0, heaven_fail=0, cti_targets=0, cti_ok=0, cti_fail=0, error="")
        return 0
    import cti_schedule
    t0 = time.time()
    try:
        cti = cti_schedule.CTI().open()
    except Exception as e:
        write_status(now, open=True, error=f"CTIを開けない {type(e).__name__}", seconds=int(time.time() - t0))
        raise
    try:
        return _run(cti, now, nm, dry, only, t0)
    except Exception as e:
        write_status(now, open=True, error=f"途中で止まった {type(e).__name__}", seconds=int(time.time() - t0))
        raise
    finally:
        # 夜遊び（YOASOBI）の空き状況の写しと配車の連絡。即ヒメ直しが開いた CTI を使い回す（ログインを増やさない）。失敗しても即ヒメには響かせない（2026-10-25）
        try:
            import cti_yoasobi
            cti_yoasobi.handle(cti, now, dry=dry)
        except Exception as e:
            print("夜遊び（空き状況の写し・配車の連絡）で失敗:", type(e).__name__)
        cti.close()


def _run(cti, now, nm, dry, only, t0=None):
    import cti_schedule
    people, head = cti.read(now)
    print(f"即ヒメ: CTI {head} → {cti_schedule.summary(people)}")
    try:
        # 新着んの並び替え（si9_sort.py）がCTIの当欠を使う。名前を含むので公開の out/ には置かない（この実行の中だけ）
        cache = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "cti_people.json"
        cache.write_text(json.dumps({"at": now.isoformat(), "people": [{"shop": p["shop"], "name": p["name"], "absent": bool(p.get("absent"))} for p in people]},
                                    ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("  CTIの控えを残せない:", type(e).__name__)
    unknown = sorted({b for b in cti_schedule.LAST_BADGES if b not in cti_schedule.SHOP_BADGE}) if hasattr(cti_schedule, "LAST_BADGES") else []
    if not ops_ready():
        # 直す決まり（鍵つき）が開けない：直さない。見張り・ボードに分かるよう生きている印に error を残す
        print("即ヒメ: 直す決まり（鍵つき）が開けないので直さない（金庫の KNOWLEDGE_KEY を確かめる）")
        if not dry:
            write_status(now, open=True, error="直す決まり（鍵つき）が開けない", seconds=int(time.time() - (t0 or time.time())),
                         set=0, heaven_fail=0, cti_targets=0, cti_ok=0, cti_fail=0)
        return 0
    if dry:
        badge_check(people, now)
    entries = load_log() if not dry else []
    new = []
    # ブラウザの土台はCTIのもの（cti.pw）を使い回す（Playwright を二重に起動すると止まる）
    browser = cti.pw.chromium.launch(headless=True)
    browser_logins = []
    done_rids = set()           # この回でCTIの時間付けを押した予約
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
        if dry and only:
            # 突き合わせに失敗した名前の形を見る（頭1文字＋長さ＋記号だけ。本名は出さない）
            def shape(nm):
                return nm[:1] + "…" + str(len(norm(nm))) + ("【" if "【" in nm else "") + ("," if "," in nm else "") + ("(" if "(" in nm or "（" in nm else "")
            matched = {id(pz) for pz in pm.values() if pz is not None}
            print("    CTIにいてヘブンの箱に合わなかった名前の形:", [shape(pz["name"]) for pz in mine if id(pz) not in matched and pz["shop"] == shopdir])
            print("    ヘブンの箱でCTIに合わなかった名前の形:", [shape(b["name"]) for b in boxes if pm.get(b["id"]) is None])
            # 1店だけ見る時は、箱ごとの判断の材料を出す（名前は頭1文字だけ）
            for b in boxes:
                pz = pm.get(b["id"])
                print(f"    箱 {b['name'][:1]}… 出勤{b['shift']} {'接客中' + b['end'] if b['serving'] else ('待機中' if b['waiting'] else '状態なし')}"
                      f" | CTI: {'なし' if pz is None else (('出勤' + hhmm(pz['work'][0]) + '-' + hhmm(pz['work'][1]) if pz.get('work') else '出勤?') + ' 予約' + str([(hhmm(x['s']) + '-' + hhmm(x['e']) + '/' + '/'.join(x['flags'])) for x in pz['bookings']]))}")
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
                         "end": end_text, "why": why_text([("timed", {})] + fwhy),
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
                    done_rids.add(tb["rid"])
                    entry.update(ok=ok, note=note, cti_ok=cti_ok, cti_note=cti_note)
                    print(f"    時間付け {label} ＊＊ ヘブン:{ok} CTI:{cti_ok} {str(cti_note)[:120]}")
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
            if not any(c in CLOSE_CODES for c, _ in why):
                end_min, fwhy = final_end(person, b, end_min, shopdir)
                why = why + fwhy
            end_text = time_for_form(end_min, hours)
            entry = {"at": now.isoformat(timespec="minutes"), "shop": label, "name": b["name"], "id": b["id"],
                     "before": "待機中" if b["waiting"] else "（状態なし）", "end": end_text, "why": why_text(why),
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
    # ヘブンの即ヒメに箱が無い子（出勤をテキストで入れている子・箱の名前が合わない子）でも、CTIの時間付けだけは押す
    label_of = {v: k for k, v in SHOPS.items()}
    for person in people:
        tb = timed_fix(person, nm)
        if tb is None or tb["rid"] in done_rids:
            continue
        label = SHOPS.get(person["shop"], "店不明")
        entry = {"at": now.isoformat(timespec="minutes"), "shop": label, "name": person["name"], "id": "", "kind": "時間付け",
                 "before": "ヘブンに箱なし", "end": hhmm(tb["e"]), "why": why_text([("timed_cti", {})]),
                 "cti": [(hhmm(x["s"]) + "-" + hhmm(x["e"]) + ("/".join([""] + x["flags"]) if x["flags"] else "")) for x in person["bookings"]],
                 "work": (hhmm(person["work"][0]) + "-" + hhmm(person["work"][1])) if person.get("work") else ""}
        if dry:
            entry.update(ok=None, note="見るだけ", cti_ok=None, cti_note="見るだけ")
        else:
            try:
                cti_ok, cti_note = cti.mark_timed(tb["rid"], now=now)
            except Exception as e:
                cti_ok, cti_note = False, f"CTIで押せなかった {type(e).__name__} {str(e)[:80]}"
            entry.update(ok=None, note="ヘブンに箱なし", cti_ok=cti_ok, cti_note=cti_note)
            print(f"    時間付け(CTIだけ) {label} ＊＊ CTI:{cti_ok} {str(cti_note)[:100]}")
            done_rids.add(tb["rid"])
        new.append(entry)
    if dry:
        for e in new:
            print("   ", e["shop"], "＊" * min(len(e["name"]), 4), e["before"], "→ 接客中", e["end"], e["why"], (f"＋CTIを時間付けに（確かめ: {e.get('cti_ok')} {e.get('cti_note')}）" if e.get("kind") == "時間付け" else ""))
        return 0
    if not dry:
        st = write_status(now, open=True, error="", seconds=int(time.time() - (t0 or time.time())), set=len(new),
                          heaven_fail=sum(1 for e in new if e.get("ok") is False),
                          cti_targets=sum(1 for e in new if e.get("kind") == "時間付け"),
                          cti_ok=sum(1 for e in new if e.get("cti_ok")), cti_fail=sum(1 for e in new if e.get("cti_ok") is False))
        notify_failures(now, st, new)
    if new:
        save_log(entries + new, now)
        print(f"即ヒメ: {len(new)}人に時間を付けた（失敗 {sum(1 for e in new if e['ok'] is False)}、CTIの時間付け {sum(1 for e in new if e.get('cti_ok'))}/{sum(1 for e in new if e.get('kind') == '時間付け')}）")
    else:
        print("即ヒメ: 直す子はいなかった")
    return 0


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    if only and "all" in only:          # 全店を今すぐ（selftest の live_shop=all）
        only = None
    try:
        raise SystemExit(run(dry=dry, only=only))
    except Exception as e:
        print(f"即ヒメ: 失敗 {type(e).__name__}: {str(e)[:200]}")
        raise SystemExit(1)
