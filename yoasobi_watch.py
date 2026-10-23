"""夜遊びヘブン（YOASOBI HEAVEN・外国人向けサイト）の多言語チャットを見張る。

なぜ: 外国人のお客さんの問い合わせは、初回返信が5分以内でないと他店へ流れる（ヘブン 2026-10-19）。
      受付が返しているが、判断が要るもの（値引き・乗り換え）や深夜は止まる。来た時だけ動く仕組みの「読む→分ける→知らせる」の部分。
      一希さん 2026-10-22「全店の夜遊びヘブンのトリガーを組みたい。見回りでなく来た時だけ反応する仕組み」。

どう動くか:
    1. 店舗IDでヘブンの管理画面にHTTPでログインし、そのクッキーをブラウザ（Playwright）に渡して
       TravelerLogin（夜遊びの管理画面への入口）→ チャット管理画面（mng.yoasobi-heaven.com、Twilioのチャット）を開く
    2. 会話リストで「自分:」で始まらない行＝お客さんの発言で終わっている＝未返信（ボットの返事も「自分:」になる）
    3. 未返信のスレッドを開いて、店が最後に返してから後のお客さんの発言を読み、
       A 定型（ホテル・エリア・料金・支払い・入室）／B 判断（予約・指名・日時・変更・値引き）／C 分からない に分ける
    4. ntfy で知らせる（Aには定型文の案を添える）。送信は変数 AUTO_YOASOBI=on の時だけ（まだ未実装。最初はoff）
    5. 知らせたスレッドは yoasobi_state.json（スレッドの印と時刻だけ。名前・本文は入れない）に残し、同じものを何度も知らせない

公開リポジトリで動くので、お客さんの名前・本文はログに出さない（出すのは店名と件数だけ）。
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import ai_reply      # お客さんへの返事を Claude で考える（2026-10-23）

from heaven_http import BASE, HeavenClient, load_credentials

JST = timezone(timedelta(hours=9))
STATE = Path(os.environ.get("YOASOBI_STATE", "out/yoasobi_state.json"))
STATE.parent.mkdir(parents=True, exist_ok=True)
PUBLIC_RAW = "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller"
SEND_NAME = os.environ.get("YOASOBI_SEND_NAME", "").strip()     # 1通だけ送る時：相手のスレッド名（会話リストの名前）
SEND_TEXT = os.environ.get("YOASOBI_SEND_TEXT", "").strip()
MNG = "https://mng.yoasobi-heaven.com"
SHOPS = [s for s in os.environ.get("YOASOBI_SHOPS", "").replace(" ", "").split(",") if s] or ["cg_kirakira"]   # 変数が空なら キラ学（2026-10-23 空で店なしになり見張りが空回りした）
LABELS = {"cg_kirakira": "キラ学", "s_matikado": "街角", "mrs_orange": "オレンジ", "potya_reen": "ぽちゃ"}
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
AUTO = os.environ.get("AUTO_YOASOBI", "off").strip().lower() in ("on", "1", "true", "yes")
RENOTIFY_MIN = int(os.environ.get("YOASOBI_RENOTIFY_MIN", "180"))   # 同じ未返信を、何分たったらもう一度知らせるか
LOOKBACK_DAYS = 3                                                    # 何日前までの未返信を見るか（古いものは返さない。一希さん 10/19）

# 分け方の言葉（お客さんの発言に含まれていたら）
B_WORDS = ("予約", "book", "reserv", "指名", "available", "出勤", "working", "明日", "今日", "tonight", "tomorrow",
           "割引", "discount", "cheaper", "安く", "値引", "負け", "まけて", "高い", "expensive", "不公平", "unfair", "deal",
           "no way", "can't", "cannot", "できない", "無理", "延長", "キャンセル", "cancel", "変更", "change", "時から",
           ":00", ":30", "来られ", "来れ", "行ける", "いける", "可以", "預約", "预约", "예약", "180", "分で", "min for")
A_WORDS = ("ホテル", "hotel", "東横", "apa", "アパ", "ダイワ", "ロイネット", "提携", "エリア", "area", "倉敷", "部屋", "room",
           "フロント", "支払", "現金", "cash", "クレジット", "credit", "card", "料金", "price", "いくら", "how much", "営業",
           "何時まで", "飯店", "酒店", "호텔", "房間", "房间", "付款", "현금")
TEMPLATES = {
    "hotel_ng": "申し訳ございません。◯◯はご派遣できないホテルです。",
    "hotel_ok": "◯◯はご派遣可能です！女の子ご到着の際にお出迎えをお願いしているホテルになります。",
    "biz_hotel": "ビジネスホテルは、事前にチェックインを済ませてお部屋番号をご連絡ください。",
    "door": "女の子が到着したらフロントへ電話をして扉を開けてあげてください。",
    "area": "遠方エリアになるので難しいです。岡山市内のラブホテル・ビジネスホテルでしたら大丈夫です。",
    "price": "外国籍のお客様はどなたもこの料金でご案内しております。60分30,000円／90分45,000円／120分60,000円です。",
    "pay": "お支払いは当日、女の子に現金（日本円）でお願いいたします。",
}


def log(*a):
    print(*a, flush=True)


def load_state():
    """記録を読み戻す：手元 → dataブランチ（見張りの回）→ main（cronの回）の順。"""
    try:
        if STATE.exists():
            return json.loads(STATE.read_text(encoding="utf-8"))
        for url in (f"{PUBLIC_RAW}/data/yoasobi_state.json", f"{PUBLIC_RAW}/main/yoasobi_state.json"):
            r = requests.get(f"{url}?t={int(time.time())}", timeout=30)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"seen": {}}


def save_state(st):
    cut = (datetime.now(JST) - timedelta(days=14)).isoformat()
    st["seen"] = {k: v for k, v in st.get("seen", {}).items() if v >= cut}
    st["at"] = datetime.now(JST).isoformat()
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def tkey(shop, name, at):
    return hashlib.sha1(f"{shop}|{name}|{at}".encode("utf-8")).hexdigest()[:12]


def post_ntfy(title, body, tags="earth_asia", priority="high"):
    if not NTFY_TOPIC:
        log("通知先が未設定（NTFY_TOPIC）")
        return False
    h = {"Title": title.encode("utf-8"), "Tags": tags, "Priority": priority, "Click": f"{MNG}/heaven-chat-mng/home"}
    try:
        requests.post(f"{NTFY_HOST}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=h, timeout=20).raise_for_status()
        return True
    except Exception as e:
        log("通知を送れませんでした:", type(e).__name__)
        return False


def classify(text):
    t = (text or "").lower()
    if any(w.lower() in t for w in B_WORDS):
        return "B"
    if any(w.lower() in t for w in A_WORDS):
        return "A"
    return "C"


def suggest(text):
    t = (text or "").lower()
    out = []
    if any(w in t for w in ("倉敷", "遠方", "市外", "アパート", "自宅", "apartment", "my house", "my home")):
        out.append(TEMPLATES["area"])
    if any(w in t for w in ("フロント", "扉", "door", "開けて", "入室", "let you in", "let her in")):
        out.append(TEMPLATES["door"])
    if any(w in t for w in ("料金", "price", "いくら", "how much", "cost", "fee", "charge", "多少钱", "多少錢", "얼마")):
        out.append(TEMPLATES["price"])
    if any(w in t for w in ("支払", "現金", "cash", "credit card", "クレジット", "pay by", "how to pay", "payment", "支付", "付款", "결제")):
        out.append(TEMPLATES["pay"])
    if any(w in t for w in ("ホテル", "hotel", "飯店", "酒店", "호텔")) and not out:
        out.append("（ホテル名を見て）" + TEMPLATES["hotel_ok"] + "／" + TEMPLATES["hotel_ng"])
    return out


def open_chat(shopdir):
    """店舗IDでHTTPログイン → クッキーをブラウザに渡す → 夜遊びの管理画面 → チャット管理画面。(playwright, browser, chat_page) を返す。"""
    from playwright.sync_api import sync_playwright

    account, password, direct = load_credentials(shopdir)
    cli = HeavenClient(account, password, direct=direct)
    cli.login_and_select(shopdir)
    cookies = []
    for c in cli.s.cookies:
        cookies.append({"name": c.name, "value": c.value, "domain": c.domain or "newmanager.cityheaven.net",
                        "path": c.path or "/"})
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1300, "height": 1000})
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    page.set_default_timeout(45000)
    page.goto(f"{BASE}/TravelerLogin?yoasobi_manager=shop&shopdir={shopdir}", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    host = page.url.split("/")[2] if "//" in page.url else page.url
    if "yoasobi-heaven" not in page.url:
        log(f"{LABELS.get(shopdir, shopdir)}: 店舗IDでは夜遊びの管理画面に入れませんでした（今の場所: {host}）")
        b.close(); p.stop()
        return None
    try:
        with ctx.expect_page(timeout=20000) as pw_:
            page.click("a[href*='heaven-chat-mng']")
        chat = pw_.value
    except Exception:
        chat = page
        chat.goto(f"{MNG}/heaven-chat-mng/home", wait_until="domcontentloaded")
    chat.wait_for_load_state("domcontentloaded")
    chat.wait_for_timeout(3000)
    try:
        btn = chat.locator("button:has-text('ログイン'), a:has-text('ログイン')").first
        if btn.count() and btn.is_visible():
            btn.click()
            chat.wait_for_timeout(5000)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < 60:
        t = chat.evaluate("() => document.body.innerText") or ""
        if "会話リスト" in t or "これ以上の会話はありません" in t:
            break
        chat.wait_for_timeout(1000)
    else:
        log(f"{LABELS.get(shopdir, shopdir)}: チャット管理画面が開きませんでした")
        b.close(); p.stop()
        return None
    for _ in range(20):
        if chat.evaluate("() => /\\d{2}\\/\\d{2} \\d{2}:\\d{2}/.test(document.body.innerText)"):
            break
        chat.wait_for_timeout(1000)
    return p, b, chat


ROWS_JS = """() => { const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const cands = Array.from(document.querySelectorAll('div, li, a')).filter(e => vis(e) && e.children.length >= 1 && e.children.length <= 6);
  const seen = new Set(); const out = [];
  for (const e of cands) { const t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
    if (!/\\d{2}\\/\\d{2} \\d{2}:\\d{2}/.test(t) || t.length > 400 || seen.has(t)) continue;
    const r = e.getBoundingClientRect(); if (r.width < 150 || r.height > 140 || r.height < 20) continue;
    if (Array.from(e.children).some(c => /\\d{2}\\/\\d{2} \\d{2}:\\d{2}/.test(c.innerText || '') && (c.innerText || '').trim().length > 40 && c.children.length >= 1)) continue;
    seen.add(t); out.push(t); }
  return out; }"""

MSGS_JS = """() => { const vis = e => !!(e.offsetWidth || e.offsetHeight); const out = [];
  for (const e of document.querySelectorAll('div, p, span')) { if (!vis(e) || e.children.length > 3) continue;
    const t = (e.innerText || '').trim(); if (!t || t.length > 1200) continue;
    const r = e.getBoundingClientRect(); if (r.x < 380) continue;
    out.push({t, x: Math.round(r.x), y: Math.round(r.y)}); }
  return out.slice(-400); }"""


def parse_row(t, now):
    """会話リストの1行 → {name, preview, at(datetime), mine(店が最後か)}"""
    m = re.search(r"(\d{2})/(\d{2}) (\d{2}):(\d{2})\s*\d*$", t)
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    yr = now.year if (mo, d) <= (now.month, now.day) else now.year - 1
    at = datetime(yr, mo, d, h, mi, tzinfo=JST)
    head = t[: m.start()].strip()
    name = head.split(" ")[0]
    preview = head[len(name):].strip()
    return {"name": name, "preview": preview, "at": at, "mine": preview.startswith("自分:"), "gone": "退会" in preview,
            "row": t}


def read_thread(chat, row_text):
    """行を押してスレッドを開き、店が最後に返してから後のお客さんの発言をつなげて返す。"""
    hit = chat.evaluate("""(t) => { const vis = e => !!(e.offsetWidth || e.offsetHeight); const head = t.slice(0, 40);
        const els = Array.from(document.querySelectorAll('div, li, a')).filter(e => vis(e) && (e.innerText || '').trim().replace(/\\s+/g, ' ').startsWith(head));
        const e = els.sort((a, b) => a.innerText.length - b.innerText.length)[0];
        if (!e) return false; e.scrollIntoView({block: 'center'}); e.click(); return true; }""", row_text)
    if not hit:
        return "", ""
    chat.wait_for_timeout(3000)
    msgs = chat.evaluate(MSGS_JS)
    items, cur = [], []
    for m in sorted(msgs, key=lambda z: (z["y"], z["x"])):
        if re.match(r"^(既読|未読)?\s*•?\s*\d{4}/\d{2}/\d{2} \d{2}:\d{2}$", m["t"]):
            if cur:
                side = "店" if max(z["x"] for z in cur) > 800 else "客"
                items.append((side, " / ".join(dict.fromkeys(z["t"] for z in cur))))
            cur = []
        else:
            cur.append(m)
    tail = []
    for side, text in reversed(items):
        if side == "店":
            break
        tail.append(text)
    tail.reverse()
    # 画面の飾り（既読・ボット表示中・ピン留め・日時）を外し、翻訳で同じ文が2回出る分を間引く
    parts = []
    for text in tail:
        for seg in text.split(" / "):
            seg = re.sub(r"(既読|未読|•|ボット表示中|前回のログイン: [^/]+|自分に返信|返信 ピン留め|\d{4}/\d{2}/\d{2} \d{2}:\d{2})", " ", seg)
            seg = re.sub(r"\s+", " ", seg).strip()
            if len(seg) < 2:
                continue
            if any(seg in q for q in parts):
                continue
            parts = [q for q in parts if q not in seg]
            parts.append(seg)
    # 直近のやりとり（AIに渡す文脈。古い順に8件まで。画面の飾りは外す）
    hist = []
    for side, text in items[-8:]:
        t = re.sub(r"(既読|未読|•|ボット表示中|前回のログイン: [^/]+|自分に返信|返信 ピン留め|\d{4}/\d{2}/\d{2} \d{2}:\d{2})", " ", text)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            hist.append(f"[{side}] {t[:300]}")
    return " / ".join(parts)[:900], "\n".join(hist)


def _type_and_send(chat, text):
    """開いているスレッドの入力欄（見えていて readOnly でない textarea。MUI は同じ場所に高さ0の影の textarea もある）に
    本文を入れ、入力欄の右にある送信のアイコンを押す。返す: (送れたか, 押したもの)。2026-10-23 調べた作りに合わせた。"""
    put = chat.evaluate("""(txt) => { const vis = e => !!(e.offsetWidth || e.offsetHeight);
        const ta = Array.from(document.querySelectorAll('textarea')).find(t => vis(t) && !t.readOnly && !t.disabled && t.getBoundingClientRect().height > 5);
        if (!ta) return null;
        ta.focus();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, txt); ta.dispatchEvent(new Event('input', {bubbles: true})); ta.dispatchEvent(new Event('change', {bubbles: true}));
        const r = ta.getBoundingClientRect(); return {x: r.x + r.width, y: r.y + r.height / 2, len: ta.value.length}; }""", text)
    if not put:
        return False, "入力欄なし"
    chat.wait_for_timeout(800)
    how = chat.evaluate("""(pos) => { const vis = e => !!(e.offsetWidth || e.offsetHeight);
        const btns = Array.from(document.querySelectorAll('button, [role=button], input[type=submit]')).filter(vis);
        let e = btns.find(b => /送信|send/i.test((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + (b.value || '')));
        if (!e) { const near = btns.map(b => { const r = b.getBoundingClientRect(); return {b, dy: Math.abs(r.y + r.height / 2 - pos.y), dx: r.x - pos.x}; })
                    .filter(o => o.dy < 30 && o.dx > -10 && o.dx < 200).sort((a, b) => a.dx - b.dx); e = near.length ? near[0].b : null; }
        if (!e) return null; e.click(); return ((e.innerText || e.getAttribute('aria-label') || e.title || '').trim() || 'icon') + '@' + Math.round(e.getBoundingClientRect().x); }""", put)
    if not how:
        return False, "送信ボタンなし"
    chat.wait_for_timeout(4000)
    msgs = chat.evaluate(MSGS_JS)
    head = re.sub(r"\s+", " ", text)[:20]
    sent = any(head in re.sub(r"\s+", " ", m["t"]) and m["x"] > 800 for m in msgs)
    return sent, how


def send_in_thread(chat, text):
    """いま開いているスレッドに1通送り、画面に出たかで確かめる。返す: (送れたか, 押したもの)"""
    return _type_and_send(chat, text)


def send_one(shopdir, name, text):
    """会話リストの名前でスレッドを開き、1通送って、送れたかを読み直して確かめる（名前・本文はログに出さない）。"""
    opened = open_chat(shopdir)
    if not opened:
        return False
    p, b, chat = opened
    label = LABELS.get(shopdir, shopdir)
    try:
        rows = chat.evaluate(ROWS_JS)
        row = next((t for t in rows if t.split(" ")[0] == name), None)
        if not row:
            log(f"{label}: 送り先のスレッドが会話リストに見つかりません")
            return False
        chat.evaluate("""(t) => { const vis = e => !!(e.offsetWidth || e.offsetHeight); const head = t.slice(0, 40);
            const els = Array.from(document.querySelectorAll('div, li, a')).filter(e => vis(e) && (e.innerText || '').trim().replace(/\\s+/g, ' ').startsWith(head));
            const e = els.sort((a, b) => a.innerText.length - b.innerText.length)[0]; if (e) { e.scrollIntoView({block: 'center'}); e.click(); } }""", row)
        chat.wait_for_timeout(3000)
        sent, how = _type_and_send(chat, text)
        log(f"{label}: 送信 {'OK' if sent else 'NG（画面に出ていない）'}（押したもの: {how}）")
        try:
            chat.click("text=会話リスト", timeout=3000)
        except Exception:
            pass
        return sent
    finally:
        b.close(); p.stop()


def watch_shop(shopdir, st, now):
    opened = open_chat(shopdir)
    if not opened:
        return {"ok": False}
    p, b, chat = opened
    label = LABELS.get(shopdir, shopdir)
    try:
        rows = [parse_row(t, now) for t in chat.evaluate(ROWS_JS)]
        rows = [r for r in rows if r]
        cut = now - timedelta(days=LOOKBACK_DAYS)
        pending = [r for r in rows if not r["mine"] and not r["gone"] and r["at"] >= cut]
        n_notified = {"A": 0, "B": 0, "C": 0}
        for r in pending:
            k = tkey(shopdir, r["name"], r["at"].isoformat())
            last = st["seen"].get(k)
            if last and datetime.fromisoformat(last) > now - timedelta(minutes=RENOTIFY_MIN):
                continue
            text, hist = "", ""
            try:
                text, hist = read_thread(chat, r["row"])
            except Exception as e:
                log(f"{label}: スレッドを読めませんでした {type(e).__name__}")
            latest = text or r["preview"]
            age = int((now - r["at"]).total_seconds() // 60)
            body = f"{r['name']}（{age}分前）\n{latest[:400]}"
            # まず Claude に聞く（決まり＝yoasobi_knowledge.md）。A で決まりの中なら、AUTO_YOASOBI=on の時だけそのまま送る。
            # それ以外は下書きを付けて知らせる。Claude が使えない時は今まで通りの仕分けだけ（2026-10-23）
            data, why = (ai_reply.ask_customer(label, r["name"], hist, latest) if ai_reply.enabled() else (None, "off"))
            sent, title = False, ""
            if data:
                cat = data.get("category") or "C"
                can, w2 = ai_reply.check_customer(data)
                reply = (data.get("reply") or "").strip()
                if can and AUTO:
                    try:
                        sent, how = send_in_thread(chat, reply)
                    except Exception as e:
                        sent, how = False, type(e).__name__
                    log(f"{label}: AIの返事を送信 {'OK' if sent else 'NG'}（{how}）")
                if sent:
                    title, body = f"🤖 夜遊び {label} AIが返しました {cat}", body + f"\n\n→ 送った返事：\n{reply}"
                elif data.get("action") == "none" and not reply:
                    title, body = f"🌏 夜遊び {label} 返事は不要 {cat}", body + f"\n\n→ {data.get('reason') or ''}"
                else:
                    title = f"🌏 夜遊び {label} 未返信 {cat}（AIの下書きあり）"
                    body += f"\n\n→ AIの下書き（{'定型・AUTO_YOASOBIがoffなので送っていない' if can else 'お店の判断が要るので送っていない'}）：\n{reply or '（無し）'}"
                if data.get("owner_note"):
                    body += f"\n📝 {data['owner_note']}"
            else:
                cat = classify(latest)
                if why not in ("off",):
                    body += f"\n（AIが使えない：{why}）"
                if cat == "A":
                    tips = suggest(latest)
                    body += "\n\n→ 定型で返せそう：\n" + ("\n".join("・" + x for x in tips) if tips else "・（ホテル・エリア・料金・支払いの定型）")
                elif cat == "B":
                    body += "\n\n→ 判断が要る（予約・指名・日時・変更・値引き）。受付が返す。値引き・乗り換えは卍へ"
                else:
                    body += "\n\n→ 読み切れない。中身を見て返してください"
                title = f"🌏 夜遊び {label} 未返信 {cat}"
            ok = post_ntfy(title, body, priority=("default" if sent else ("urgent" if age >= 5 else "high")))
            if ok:
                st["seen"][k] = now.isoformat()
                n_notified[cat] += 1
            try:
                chat.click("text=会話リスト", timeout=3000)
                chat.wait_for_timeout(1500)
            except Exception:
                pass
        log(f"{label}: 会話 {len(rows)}件 / 未返信（{LOOKBACK_DAYS}日以内） {len(pending)}件 / 今回知らせた A{n_notified['A']} B{n_notified['B']} C{n_notified['C']}")
        return {"ok": True, "rows": len(rows), "pending": len(pending)}
    finally:
        try:
            chat.click("text=会話リスト", timeout=2000)
        except Exception:
            pass
        b.close(); p.stop()


def main():
    now = datetime.now(JST)
    if os.environ.get("YOASOBI_PROBE_NAME", "").strip():
        return 0 if probe_thread(SHOPS[0], os.environ["YOASOBI_PROBE_NAME"].strip()) else 1
    if SEND_NAME and SEND_TEXT:
        ok = send_one(SHOPS[0], SEND_NAME, SEND_TEXT)
        return 0 if ok else 1
    st = load_state()
    res = {}
    for s in SHOPS:
        try:
            res[s] = watch_shop(s, st, now)
        except Exception as e:
            log(f"{LABELS.get(s, s)}: 失敗 {type(e).__name__} {str(e)[:160]}")
            res[s] = {"ok": False}
    save_state(st)
    return 0 if all(v.get("ok") for v in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
