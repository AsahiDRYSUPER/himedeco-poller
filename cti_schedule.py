"""CTI（風俗CTIv2）の「本日スケジュール」を読む。書くのは「プレイ状況を時間付けにする」だけ（mark_timed）。

なぜ：ヘブンの即ヒメで「待機中なのに、本当は接客中」の子を自動で直すため（一希さん 2026-10-09）。
      10/10 追加：女子状況が「入室」なのにプレイ状況が「時間付け」でない予約は、ヘブンの終了時刻をCTIの終了時刻に合わせ、
      CTIのプレイ状況を「時間付け」にして保存する（一希さん）。
どう読むか：人と同じようにブラウザでCTIを開いてログインし（金庫 CTI_LOGIN_URL・CTI_PASSWORD）、
  本日スケジュール（#<事業所>/schedule?date=YYYYMMDD）の画面に出ている
    行（.schedule-row）：女の子の名前（.hime-name）・出勤時間（.work-time 例「10:00-翌3:00Up」）・印（.hime-badge）
    予約の箱（.resv-item）：時刻（.resv-time 例「13:25-15:55」「翌0:30-翌2:30」）・印（.mark-badge「入室」「終了」「仮予約」「時間付け」…）
  を読む。箱は行の外に重ねて描かれているので、行との対応は縦の位置で取る（10/9 の下調べ）。
どう書くか（10/10 下調べ9）：箱をダブルクリック → 大きな窓（受領・編集）→「編集」→ 編集の形（.resv-edit-container）の
  「ﾌﾟﾚｲ状況」の select（選択肢に「時間付け」がある。値 6）を時間付けにする → 下の「保存」（.modal-footer .btn-green）。
返す形：[{"shop": shopdir, "name": 名前, "work": (開始分, 終了分), "bookings": [{"s": 開始分, "e": 終了分, "flags": [...], "rid": 箱のID}]}]
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
# 行の印は「待機場所（事務所・街角・寮(セント)・車待機(プラッツ)…）」「出勤・当欠・出確なし」「報酬:◯円」「タグ（ロリ・ギャル…）」と
# 2文字の店の印が混ざっている。店は2文字の印だけで決める（10/10 の見るだけ実行で分かった。「街角」は待機場所であって店ではない）。
# 印→店の対応は、ヘブンの出勤一覧の名前と突き合わせて確認済み（10/10：街角15/15・とろ〜り6/6・VENUS7/7・UC2/2・キラ学20/24・オレンジ15/16・ぽちゃ12/13）
SHOP_BADGE = {"KG": "cg_kirakira", "OR": "mrs_orange", "PO": "potya_reen", "MK": "s_matikado",
              "TO": "torori_angel", "VE": "venus_okayama", "UC": "undercover"}
LAST_BADGES = set()
ROW_BADGES = []          # (店, 行の印の組) 店の印の当て方を確かめる用（名前は入れない）
FLAG_WORDS = ("入室", "終了", "仮予約", "予約", "本", "時間付け")


def badge_shop(badges):
    for b in badges:
        if b in SHOP_BADGE:
            return SHOP_BADGE[b]
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
    const host = it.closest('apo-resv');
    return {y: (r.top + r.bottom) / 2, time: (it.querySelector('.resv-time') || {innerText: ''}).innerText.trim(),
            flags: Array.from(it.querySelectorAll('.mark-badge, .hime-badge')).map(b => b.innerText.trim()),
            text: (it.innerText || '').slice(0, 200), rid: host ? (host.getAttribute('resv-id') || '') : ''};
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
        ROW_BADGES.append((shop, tuple(b for b in r["badges"] if not b.startswith("報酬"))))
        # 出勤時間の末尾「Up」＝その時刻には仕事を終えていたい（上がり）。無ければ受付の締め（一希さん 10/10）
        out.append({"shop": shop, "name": r["name"], "work": span(r["work"]), "up": bool(re.search(r"up\s*$", r["work"] or "", re.I)),
                    "top": r["top"], "bottom": r["bottom"], "bookings": []})
    for it in raw["items"]:
        sp = span(it["time"])
        if sp is None:
            continue
        host = next((o for o in out if o["top"] - 2 <= it["y"] <= o["bottom"] + 2), None)
        if host is None:
            continue
        flags = [f for f in it["flags"] if f in FLAG_WORDS]
        text = it.get("text", "")
        for w in ("入室", "終了", "時間付け"):
            if w in text and w not in flags:
                flags.append(w)
        host["bookings"].append({"s": sp[0], "e": sp[1], "flags": flags, "rid": it.get("rid", "")})
    for o in out:
        o["bookings"].sort(key=lambda b: b["s"])
        del o["top"], o["bottom"]
    return out, raw.get("head", "")


def business_day(now):
    """営業日は 10時〜翌2時。深夜 0〜5時は前の日のスケジュールを見る。"""
    return now - timedelta(hours=5)


class CTI:
    """ブラウザでCTIを開いたまま使う（読む→必要なら時間付けを書く→閉じる）。"""

    def __init__(self):
        self.pw = self.browser = self.page = None

    def open(self):
        from playwright.sync_api import sync_playwright
        password = os.environ.get("CTI_PASSWORD", "").strip("\r\n")
        login_url = os.environ.get("CTI_LOGIN_URL", "").strip()
        if not password or not login_url.startswith("https://cti2.fuzoku-fan.jp/"):
            hint = (f"パスワード{len(password)}字、URL{len(login_url)}字"
                    f"（https始まり:{login_url.startswith('https://')} / cti2を含む:{'cti2.fuzoku-fan.jp' in login_url}）")
            raise RuntimeError("CTI_PASSWORD / CTI_LOGIN_URL が無いか形が違う: " + hint)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1600, "height": 1200}).new_page()
        login(self.page, login_url, password)
        return self

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.pw:
                self.pw.stop()

    def read(self, now=None):
        """本日スケジュールを読む。返す: (女の子の一覧, 見出しの日付)。"""
        page = self.page
        day = business_day(now or datetime.now(JST))
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
        return parse(raw)

    def _close_modals(self):
        """見えている窓（.modal）を、閉じる・OK・キャンセルのボタンか Escape で閉じる。返す: 閉じた窓の見出し（数字は伏せる）。"""
        page = self.page
        out = []
        for _ in range(3):
            info = page.evaluate("""() => {
              const m = Array.from(document.querySelectorAll('.modal, [class*=modal-container], .modal-content, [class*=modal-window], .jqmWindow')).find(e => e.getClientRects().length && e.offsetHeight > 0 && !e.classList.contains('modal-footer'));
              if (!m) return null;
              const head = (m.innerText || '').replace(/\\s+/g, ' ').slice(0, 60);
              const btns = Array.from(m.querySelectorAll('button, a.btn, input[type=button]')).filter(b => b.getClientRects().length).map(b => (b.innerText || b.value || '').trim());
              return {head, btns};
            }""")
            if not info:
                break
            out.append(re.sub(r"\d", "#", info["head"]) + " [" + "/".join(info["btns"][:6]) + "]")
            clicked = False
            for word in ("閉じる", "OK", "キャンセル", "確認", "いいえ"):
                b = page.locator(f".modal button:has-text('{word}'), [class*=modal-container] button:has-text('{word}'), [class*=modal-window] button:has-text('{word}'), .jqmWindow button:has-text('{word}'), .modal .btn-close, [class*=modal-container] .btn-close")
                vis = [b.nth(i) for i in range(b.count()) if b.nth(i).is_visible()]
                if vis:
                    vis[-1].click()
                    clicked = True
                    break
            if not clicked:
                page.keyboard.press("Escape")
            page.wait_for_timeout(800)
        return out

    def _safe_point(self, box):
        """箱の中で、上に何もかぶっていない点を探す（10/11：今の時刻の赤い縦線 .vertical-line が箱の真ん中にかぶって押せなかった）。"""
        page = self.page
        bb = box.first.bounding_box()
        if not bb:
            return None
        for fx in (0.5, 0.25, 0.75, 0.12, 0.88, 0.35, 0.65):
            x, y = bb["x"] + bb["width"] * fx, bb["y"] + bb["height"] * 0.5
            try:
                ok = page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest('apo-resv')); }", [x, y])
            except Exception:
                ok = False
            if ok:
                return x, y
        return bb["x"] + bb["width"] * 0.2, bb["y"] + bb["height"] * 0.5

    def mark_timed_quick(self, rid, save=True, now=None):
        """右クリックの簡易窓で、プレイ状況の「時間付け」にチェック → 更新（一希さん 10/10「右クリックで簡易的に変えれる」）。返す: (できたか, メモ)。"""
        page = self.page
        if not rid:
            return False, "箱のIDが無い"
        page.keyboard.press("Escape")
        try:
            self.read(now)
        except Exception as e:
            return False, f"開き直せない {type(e).__name__}"
        box = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
        if box.count() == 0:
            return False, "箱が見つからない"
        closed = self._close_modals()
        box.first.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
        page.wait_for_timeout(400)
        pt = self._safe_point(box)
        if pt is None:
            return False, "箱が画面に無い"
        page.mouse.click(pt[0], pt[1], button="right")
        # 簡易窓：見えている radio のうち、横の文字が「時間付け」のもの
        found = None
        for _ in range(20):
            page.wait_for_timeout(300)
            found = page.evaluate("""() => {
              const rs = Array.from(document.querySelectorAll('input[type=radio]')).filter(r => r.getClientRects().length);
              const r = rs.find(r => /時間付け/.test(((r.closest('label') || r.parentElement) || {}).innerText || ''));
              return r ? {checked: r.checked, n: rs.length} : null;
            }""")
            if found:
                break
        if not found:
            page.keyboard.press("Escape")
            return False, "簡易窓に時間付けの選択肢が出ない"
        upd = page.locator("button:has-text('更新')").filter(has_text=re.compile(r"^\s*更新\s*$"))
        vis = [upd.nth(i) for i in range(upd.count()) if upd.nth(i).is_visible()]
        if not vis:
            page.keyboard.press("Escape")
            return False, "簡易窓の更新ボタンが見えない"
        if not save:
            page.keyboard.press("Escape")
            return True, "簡易窓で更新の手前まで通った（確かめ）"
        page.evaluate("""() => { const rs = Array.from(document.querySelectorAll('input[type=radio]')).filter(r => r.getClientRects().length);
          const r = rs.find(r => /時間付け/.test(((r.closest('label') || r.parentElement) || {}).innerText || '')); if (r) r.click(); }""")
        page.wait_for_timeout(300)
        page.once("dialog", lambda d: d.accept())
        vis[-1].click()
        page.wait_for_timeout(2500)
        page.keyboard.press("Escape")
        for _ in range(6):
            time.sleep(1.0)
            b2 = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
            txt = b2.first.inner_text() if b2.count() else ""
            if "時間付け" in txt:
                return True, "時間付け（簡易窓）"
        return False, "更新したが時間付けの印が付かない"

    def mark_timed(self, rid, save=True, now=None):
        """まず右クリックの簡易窓で。だめなら大きな窓（編集→保存）で。"""
        try:
            ok, note = self.mark_timed_quick(rid, save=save, now=now)
        except Exception as e:
            ok, note = False, f"簡易窓で押せなかった {type(e).__name__} {str(e)[:60]}"
        if ok:
            return ok, note
        ok2, note2 = self.mark_timed_big(rid, save=save, now=now)
        return ok2, f"{note2}（簡易窓: {note}）"

    def mark_timed_big(self, rid, save=True, now=None):
        """その予約のプレイ状況を「時間付け」にして保存する。返す: (できたか, メモ)。
        人と同じ押し方：箱をダブルクリック → 大きな窓 →「編集」→ ﾌﾟﾚｲ状況 → 保存。他の欄は触らない。
        save=False は確かめ用（保存の手前まで行って閉じる）。"""
        page = self.page
        if not rid:
            return False, "箱のIDが無い"
        # ヘブン側の操作で時間が経っているので、本日スケジュールを開き直してから押す（10/10 15:02 ダブルクリックが30秒待っても押せなかった）
        page.keyboard.press("Escape")
        try:
            self.read(now)
        except Exception as e:
            return False, f"開き直せない {type(e).__name__}"
        box = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
        if box.count() == 0:
            return False, "箱が見つからない"
        # 画面に何かの窓（modal）が開いていたら、先に閉じる（10/10：modal-footer が箱の上にかぶって押せなかった）
        closed = self._close_modals()
        try:
            # 箱を画面の真ん中に持ってくる（端に寄せると、画面の下に固定されたバーの後ろに隠れて押せない）
            box.first.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
            page.wait_for_timeout(500)
            pt = self._safe_point(box)
            page.mouse.dblclick(pt[0], pt[1])
            page.wait_for_timeout(1500)
            if not page.locator("button.btn-detail:has-text('編集')").count():
                raise RuntimeError("窓が開かない")
        except Exception:
            # 押せない時は、箱の真ん中の座標を直接ダブルクリック（何かが上にかぶっている時用）
            bb = box.first.bounding_box()
            if not bb:
                return False, "箱が画面に無い"
            cover = page.evaluate("([x, y]) => { let e = document.elementFromPoint(x, y); const a = []; while (e && a.length < 4) { a.push(e.tagName + '.' + e.className.toString().slice(0, 30)); e = e.parentElement; } return a.join(' < ') + ' @' + Math.round(x) + ',' + Math.round(y) + ' 画面高' + window.innerHeight; }", [bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2])
            page.mouse.dblclick(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            page.wait_for_timeout(500)
            if not page.locator("button.btn-detail:has-text('編集')").count():
                return False, f"箱を押せない（上にあるもの: {cover}／閉じた窓: {closed}）"
        try:
            page.wait_for_selector("button.btn-detail:has-text('編集')", state="visible", timeout=10000)
        except Exception:
            return False, "予約の窓が開かない"
        edits = page.locator("button.btn-detail:has-text('編集')")
        vis = [edits.nth(i) for i in range(edits.count()) if edits.nth(i).is_visible()]
        if not vis:
            return False, "編集ボタンが見えない"
        vis[-1].click()
        try:
            page.wait_for_selector(".resv-edit-container select", state="visible", timeout=10000)
        except Exception:
            return False, "編集の形が開かない"
        sel = page.locator(".resv-edit-container select").filter(has=page.locator("option", has_text="時間付け"))
        if sel.count() == 0:
            page.keyboard.press("Escape")
            return False, "ﾌﾟﾚｲ状況の欄が見つからない"
        value = sel.first.evaluate("s => Array.from(s.options).find(o => /時間付け/.test(o.text)).value")
        btn = page.locator(".modal-footer button.btn-green:has-text('保存')")
        if btn.count() == 0:
            page.keyboard.press("Escape")
            return False, "保存ボタンが見つからない"
        if not save:
            page.keyboard.press("Escape")
            return True, "保存の手前まで通った（確かめ）"
        sel.first.select_option(value)
        page.wait_for_timeout(300)
        page.once("dialog", lambda d: d.accept())
        btn.first.click()
        page.wait_for_timeout(3000)
        page.keyboard.press("Escape")
        # 保存できたかは、箱に「時間付け」の印が付いたかで確かめる
        for _ in range(6):
            time.sleep(1.0)
            b2 = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
            txt = b2.first.inner_text() if b2.count() else ""
            if "時間付け" in txt:
                return True, "時間付け"
        return False, "保存したが時間付けの印が付かない"


def read_today(now=None):
    """CTIにログインして本日スケジュールを読むだけ（開いて・読んで・閉じる）。"""
    c = CTI().open()
    try:
        return c.read(now)
    finally:
        c.close()


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
