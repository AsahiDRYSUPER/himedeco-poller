"""出勤の返事が「日にち＋時間」で揃っていたら、人の手を借りずに出勤を上げて、本人に「上げました」と返す。

やること（一希さんと決めた店のルールどおり）:
    1. 出勤登録の画面で、その子・その日のセルを押して、時間を選んで「登録する」を押す
       （HTTPで送っても保存されないため、人と同じ操作をブラウザで再現する。cast-mypage/open_shift.py と同じ）
    2. 今日からその出勤日の前日までの空いている日に「次回◯日出勤！」を入れる
    3. 姫デコチャットで本人に「上げました」と返す

公開リポジトリで動くので、ログにはキャスト名も本文も出さない（店名・キャストID・日付・結果だけ）。
"""
import re
import sys
from datetime import date, datetime, timedelta, timezone

from heaven_http import BASE, COMMU_IDS, load_credentials
from shift_reply import hhmm

JST = timezone(timedelta(hours=9))


def login(page, shopdir):
    account, password, _ = load_credentials(shopdir)
    page.set_default_timeout(45000)
    page.set_default_navigation_timeout(60000)
    page.goto(f"{BASE}/C1Login.php", wait_until="domcontentloaded")

    # この画面にはログイン欄が2つある（見えているものと、隠れている再ログイン用）。見えている方だけを触る。
    def fill_visible(sels, value):
        for sel in sels:
            loc = page.locator(sel)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    el.fill(value)
                    return True
        return False

    def click_visible(sels):
        for sel in sels:
            loc = page.locator(sel)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    el.click()
                    return True
        return False

    if not fill_visible(('input[name="txt_account"]', 'input[type="text"]'), account):
        return False
    if not fill_visible(('input[name="txt_password"]', 'input[type="password"]'), password):
        return False
    if not click_visible(('input[name="login"]', 'input[type="submit"]', 'button[type="submit"]', 'input[type="image"]')):
        return False
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)
    return "C1Login.php" not in page.url


def cell_text(page, gid, ymd):
    try:
        return (page.locator(f"#msgbox_{gid}_{ymd}").inner_text(timeout=5000) or "").strip()
    except Exception:
        return ""


def goto_cell(page, shopdir, gid, ymd, name=""):
    """その子・その日の欄が出る画面を開く。既定では一部の子しか出ないので、ページ送りで無ければ名前で検索する。"""
    from urllib.parse import quote
    for st in range(1, 8):
        page.goto(f"{BASE}/C9ShukkinShiftList.php?shopdir={shopdir}"
                  f"&list_cnt=100&allDisp=1&start={st}&basedate={ymd}", wait_until="domcontentloaded")
        if page.locator(f"#msgbox_{gid}_{ymd}").count():
            return True
        if not page.locator('input[name="target_msgbox"]').count():
            break
    if name:
        page.goto(f"{BASE}/C9ShukkinShiftList.php?shopdir={shopdir}"
                  f"&serach_girls_name={quote(name)}&basedate={ymd}", wait_until="domcontentloaded")
        if page.locator(f"#msgbox_{gid}_{ymd}").count():
            return True
    return False


def open_one(page, shopdir, gid, ymd, start, end, note="", name="", want_text=""):
    """その子・その日のセルを押して、時間を入れて登録し、読み直して確かめる。"""
    if not goto_cell(page, shopdir, gid, ymd, name):
        return False, "その子・その日の欄が画面に出ていません"
    before = cell_text(page, gid, ymd)
    td = page.locator(f"#msgbox_{gid}_{ymd}").locator("xpath=ancestor::td[1]")
    try:
        td.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    try:
        td.click(timeout=5000, force=True)
    except Exception:
        td.dispatch_event("click")
    page.wait_for_timeout(800)
    if not page.locator("#edit_start_time").count():
        return False, "入力の窓が開きませんでした"
    page.select_option("#edit_start_time", start or "")
    page.select_option("#edit_end_time", end or "")
    if note:
        page.fill("#scheduleText", note)
    page.wait_for_timeout(200)
    # 「登録する」はボタンではなくリンク。人が押すのと同じように押し、無ければ同じ関数を直接呼ぶ。
    link = page.locator('#form_dlg a[href*="doSubmit_ShukkinShiftDialog"], .submitBtn a')
    if link.count():
        try:
            link.first.click(timeout=5000, force=True)
        except Exception:
            page.evaluate(f"doSubmit_ShukkinShiftDialog('{gid}_{ymd}')")
    else:
        page.evaluate(f"doSubmit_ShukkinShiftDialog('{gid}_{ymd}')")
    page.wait_for_timeout(2500)
    goto_cell(page, shopdir, gid, ymd, name)
    after = cell_text(page, gid, ymd)
    if want_text:
        ok = want_text in after
    else:
        want = f"{start[:2]}:{start[2:]}"
        ok = want in after
    return ok, f"前: {before or '（空）'} → 後: {after or '（空）'}"


def fill_next(page, shopdir, gid, name, shift_ymd, today_ymd):
    """店のルール: 出勤を上げたら、今日からその出勤日の前日までの空いている日に「次回◯日出勤！」を入れる。
    埋まっている日（出勤あり・別の文）は触らない。"""
    d = date(int(shift_ymd[:4]), int(shift_ymd[4:6]), int(shift_ymd[6:]))
    t = date(int(today_ymd[:4]), int(today_ymd[4:6]), int(today_ymd[6:]))
    text = f"次回{d.day}日出勤！"
    done, skipped = [], []
    cur = t
    while cur < d:
        ymd = cur.strftime("%Y%m%d")
        cur += timedelta(days=1)
        if not goto_cell(page, shopdir, gid, ymd, name):
            skipped.append(f"{int(ymd[4:6])}/{int(ymd[6:])}")
            continue
        before = cell_text(page, gid, ymd)
        if before and "－" not in before and not before.startswith("次回"):
            skipped.append(f"{int(ymd[4:6])}/{int(ymd[6:])}")
            continue
        if before.startswith(text):
            done.append(f"{int(ymd[4:6])}/{int(ymd[6:])}")
            continue
        ok, _ = open_one(page, shopdir, gid, ymd, "", "", note=text, name=name, want_text=text)
        (done if ok else skipped).append(f"{int(ymd[4:6])}/{int(ymd[6:])}")
    return text, done, skipped


def upload(shopdir, gid, name, shifts, today):
    """shifts: [(date, "12:00", "20:00")] → [(date, ok, msg, fill)]"""
    from playwright.sync_api import sync_playwright
    out = []
    today_ymd = today.strftime("%Y%m%d")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        try:
            if not login(page, shopdir):
                return [(d, False, "ログインできませんでした", "") for d, _, _ in shifts]
            for d, s, e in sorted(shifts):
                ymd = d.strftime("%Y%m%d")
                fill = ""
                try:
                    ok, msg = open_one(page, shopdir, gid, ymd, hhmm(s), hhmm(e), name=name)
                    if ok:
                        text, done, skipped = fill_next(page, shopdir, gid, name, ymd, today_ymd)
                        fill = f"「{text}」→ {'・'.join(done) if done else 'なし'}" + (f"（触らず {'・'.join(skipped)}）" if skipped else "")
                except Exception as ex:
                    ok, msg = False, type(ex).__name__
                out.append((d, ok, msg, fill))
        finally:
            ctx.close()
            browser.close()
    return out


def yobi(name):
    """呼びかけに使う名前。「二階堂さん」のように「さん」で終わる源氏名は、重ねない。"""
    n = re.sub(r"[\[【(（].*?[\]】)）]", "", name or "").strip()
    return n if n.endswith("さん") else n + "さん"


def reply_text(name, shifts):
    parts = [f"{d.day}日 {s}〜{e}" for d, s, e in shifts]
    return f"{yobi(name)}、ありがとう！ {'・'.join(parts)}で上げておいたよ🙌 当日よろしくね😊"


def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(t or ""))).strip()


def reply(cli, shopdir, gid, text):
    """姫デコチャットで本人に返す（cast-mypage/chat_send.py と同じ手順）。送ったあと読み直して確かめる。"""
    import json
    ref = f"{BASE}/himedecochat/talk?girls_id={gid}&mid={COMMU_IDS[shopdir]}"
    cli.s.get(ref, timeout=30)
    r = cli.s.post(BASE + "/himedecochat/api/send_message",
                   data=json.dumps({"talk_message": text, "girls_id": int(gid)}, ensure_ascii=False).encode("utf-8"),
                   headers={"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest", "Referer": ref},
                   timeout=30)
    r.encoding = "utf-8"
    try:
        if r.json().get("result") != 0:
            return False
    except Exception:
        return False
    t = cli.s.post(BASE + "/himedecochat/api/new_list", json={"girls_id": int(gid)},
                   headers={"X-Requested-With": "XMLHttpRequest", "Referer": ref}, timeout=30)
    talks = (t.json().get("talk") or []) if t.json().get("result") == 0 else []
    last = talks[-1] if talks else {}
    return clean(text)[:20] in clean(last.get("body"))


def handle(cli, shopdir, label, gid, name, shifts, now=None):
    """上げる → 「次回◯日出勤！」 → 本人に返す。返す: {"ok", "replied", "detail"}"""
    now = now or datetime.now(JST)
    results = upload(shopdir, gid, name, shifts, now.date())
    parts, all_ok = [], True
    for d, ok, msg, fill in results:
        all_ok = all_ok and ok
        s, e = next((s, e) for dd, s, e in shifts if dd == d)
        parts.append(f"{d.day}日 {s}〜{e} {'✅' if ok else '✗ ' + msg}" + (f"（{fill}）" if fill else ""))
        print(f"  {label} {gid} {d.month}/{d.day} {s}〜{e}: {'OK' if ok else 'NG'} {msg}" + (f" / {fill}" if fill else ""))
    replied = False
    if all_ok and cli is not None:
        try:
            replied = reply(cli, shopdir, gid, reply_text(name, shifts))
        except Exception as ex:
            print(f"  {label} {gid}: 返信で {type(ex).__name__}")
        print(f"  {label} {gid}: 返信 {'OK' if replied else 'NG'}")
    return {"ok": all_ok, "replied": replied, "detail": "／".join(parts)}


def selftest(shopdir="s_matikado"):
    """ブラウザでログインして出勤登録の画面が開けるかだけ確かめる（書き込みはしない）。"""
    from playwright.sync_api import sync_playwright
    today = datetime.now(JST).strftime("%Y%m%d")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(locale="ja-JP").new_page()
        if not login(page, shopdir):
            print("✗ ログインできませんでした")
            return 1
        page.goto(f"{BASE}/C9ShukkinShiftList.php?shopdir={shopdir}&list_cnt=100&allDisp=1&start=1&basedate={today}",
                  wait_until="domcontentloaded")
        n = page.locator('input[name="target_msgbox"]').count()
        cells = page.locator('[id^="msgbox_"]').count()
        browser.close()
    print(f"✅ ログインでき、出勤登録の画面が開けました（人数の欄 {n} / マス {cells}）")
    return 0 if cells else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest(sys.argv[-1] if sys.argv[-1] != "--selftest" else "s_matikado"))
    print("watch_chat.py から呼ばれます（単体では動かしません）")
