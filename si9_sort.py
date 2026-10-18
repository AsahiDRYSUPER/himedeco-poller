"""新着ん（si9.jp）の女の子の並び替えを、お店の決まりで自動に保つ（一希さん 2026-10-18「要は脱属人化」）。

いつ：朝 8:00〜翌 2:00、約5分ごと（collect_loop.py の即ヒメ直しのあと）。
      スタッフが 8:30〜9:00 に来た時にはもう並んでいるように、朝は 8 時の回から並べる（一希さん 10/18）。
何を：
  1. ヘブン管理画面の「即ヒメ登録」を読む（店舗IDでHTTP。読むだけ。9:00 より前は読まない）
  2. CTIの本日スケジュールの「当欠」（即ヒメ直しが読んだものを使う。無ければ自分で読む。読むだけ）
  3. 新着んにログインして、スケジュール（今日から7日）と今の並びを読む
  4. 決まりで並べた案が今の並びと違う時だけ、「並び順の保存」→ 読み直して確かめる →「サイトへ並び替えを更新」→ 7サイト
     （前に送ってから10分以上あいている時だけ。送っている途中なら何もしない）
決まり（Addness 98564629-4105-41ba-931a-56f3455199f5）：
  ① 待機中（即ヒメ「◯◯〜待機中」）：出勤の早い順
  ② 接客中と出勤前を混ぜて、空く時刻（即ヒメ「次回◯◯〜」。出勤前で表示が無ければ出勤時刻）の早い順
  ③ 受付終了と、その日の仕事が終わった子：出勤の早い順
  ④ 今日休みになった子（CTIの「当欠」）
  ⑤ 今日出勤しない子：先の出勤が上がっている子（近い順）→ 上がっていない子（今の並びのまま）
  同じ段・同じ時刻の中は、今の並びで上にいる方を先にする（並びが毎回揺れない）。ピン留めの子は先頭のまま触らない。
公開リポジトリなので、ログにも記録にも女の子の名前は出さない（人数と段ごとの数だけ）。
切り替え：リポジトリの変数 SI9_SORT_SHOPS（例 "cg_kirakira"。空なら何もしない）
          SI9_SORT_MODE（"send"＝保存して送る／それ以外＝案を作って数を出すだけ）
"""
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
BASE = "https://si9.jp"
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
STATUS_FILE = OUT_DIR / "si9_status.json"     # 名前なし（時刻・人数・送った結果の数）。ボードと次の回が読む
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
# 即ヒメ直しが読んだCTIの当欠（名前を含むので、公開の out/ には置かない。この実行の中だけの置き場）
CTI_CACHE = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "cti_people.json"
CTI_CACHE_MAX_SEC = 15 * 60

# ヘブンの店 → (新着んの「店を選ぶ」画面の文字, 新着んの店番号)。まずキラ学だけ（1日見てから広げる）
SHOPS = {"cg_kirakira": ("素人専門キラキラ学園", "896")}
DAY_CUT_H = 6                       # 営業日の区切り：0:00〜5:59 は前の日の続き（24:00〜29:59）
START_MIN, END_MIN = 8 * 60, 26 * 60  # 動く時間：8:00〜翌2:00
BOX_FROM = 9 * 60                   # 即ヒメの箱を見るのは 9:00 から（即ヒメ直しと同じ。それより前は誰も待機していない）
MIN_GAP_SEC = 10 * 60               # 送るのは、前に送ってから10分以上あけて
SEND_WAIT_SEC = 150                 # 送った後、終わるのを待つ長さ（終わらなければ、次の回で「送っている途中」を見て待つ）


_NAMES = set()     # 読んだ女の子の名前（ログに出す前に伏せる用。外には出さない）


def scrub(text):
    """ログに出す文から、読んだ女の子の名前を伏せる（公開リポジトリのログなので）"""
    t = str(text)
    for n in sorted(_NAMES, key=len, reverse=True):
        if len(n) >= 2:
            t = t.replace(n, "＊＊")
    return t


# ---------- 時刻 ----------
def bd(h, m):
    """営業日の分（6:00〜翌6:00）。0:00〜5:59 は 24:00〜29:59 として扱う"""
    v = int(h) * 60 + int(m)
    return v + 1440 if v < DAY_CUT_H * 60 else v


def biz_minutes(now):
    return bd(now.hour, now.minute)


def biz_date(now):
    """営業日の日付（0:00〜5:59 は前の日）"""
    return (now - timedelta(hours=DAY_CUT_H)).date()


def span(s, e):
    """出勤の始まり・終わり（営業日の分）。終わりが始まり以下なら次の日"""
    s, e = bd(*s.split(":")), bd(*e.split(":"))
    if e <= s:
        e += 1440
    return s, e


def fm(v):
    return f"{(v // 60) % 24:02d}:{v % 60:02d}" if v is not None else "--"


# ---------- 名前 ----------
def norm(n):
    """突き合わせ用の名前。【新入生】などの飾り（途中で切れた飾りも）・空白・「…」を外す。ヶ/ケ は同じとみなす"""
    n = unicodedata.normalize("NFKC", n or "")
    n = re.sub(r"[\[【(（].*", "", n)
    n = n.replace("ヶ", "ケ").replace("ヵ", "カ")
    n = re.sub(r"\s+", "", n).replace("…", "")
    return re.sub(r"[.．]+$", "", n)


# ---------- ヘブンの即ヒメ登録の画面 ----------
TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[～〜~]\s*(\d{1,2}):(\d{2})")


def parse_standby(html):
    """1人分の箱 → {short: 突き合わせ用の名前, status: waiting/next/closed/none, shift: (始, 終), next: 空く時刻}。
    箱の作り（10/17 キラ学で確認）：名前＝幅105pxの欄（長い名前は「…」で切れる）・出勤＝colspan の欄「12:00～ 2:00」・
    状態＝赤い字の欄「16:26～待機中」「次回17:58～」「受付終了」・コメント＝marquee の欄（ここの数字は見ない）"""
    from bs4 import BeautifulSoup
    out = []
    for t in BeautifulSoup(html, "html.parser").find_all("table", class_=re.compile(r"^sokuhimegirlbox")):
        name_td = t.find("td", style=re.compile(r"width:\s*105px"))
        name = name_td.get_text(" ", strip=True) if name_td is not None else ""
        shift = None
        for td in t.find_all("td"):
            m = TIME_RANGE.search(td.get_text(" ", strip=True))
            if m and td.get("colspan") and "marquee" not in (td.get("class") or []):
                shift = span(f"{m.group(1)}:{m.group(2)}", f"{m.group(3)}:{m.group(4)}")
                break
        red = [td.get_text(" ", strip=True) for td in t.find_all("td", style=re.compile(r"color:\s*#F00", re.I))]
        stxt = " ".join(red)
        w = re.search(r"(\d{1,2}):(\d{2})\s*[～〜~]\s*待機中", stxt)
        nx = re.search(r"次回\s*(\d{1,2}):(\d{2})", stxt)
        if w:
            st = "waiting"
        elif nx:
            st = "next"
        elif "受付終了" in stxt:
            st = "closed"
        else:
            st = "none"
        out.append({"short": norm(name), "status": st, "shift": shift,
                    "since": bd(w.group(1), w.group(2)) if w else None,
                    "next": bd(nx.group(1), nx.group(2)) if nx else None})
    return out


# ---------- 決める（ここは画面に触らない。テストで確かめる） ----------
def match_boxes(girls, boxes):
    """新着んの女の子 → ヘブンの箱。箱の名前は途中で切れているので前方一致。1人にだけ合う時だけ使う"""
    out, used = {}, {}
    for bx in boxes:
        if not bx["short"]:
            continue
        cands = [g["id"] for g in girls if norm(g["name"]).startswith(bx["short"])]
        if len(cands) == 1:
            gid = cands[0]
            used[gid] = used.get(gid, 0) + 1
            out[gid] = bx
    # 2つの箱が同じ子に合った時は、どちらが正しいか分からないので使わない
    return {gid: bx for gid, bx in out.items() if used[gid] == 1}


def match_absent(girls, absent_names):
    """CTIの当欠の名前 → 新着んの女の子の番号。CTIの名前は「◯◯,△△RSP」のように別名がカンマでつながることがある。
    まず完全一致、無ければ3文字以上の前方一致。1人にだけ合う時だけ使う"""
    ids = set()
    gn = [(g["id"], norm(g["name"])) for g in girls]
    for raw in absent_names:
        for part in re.split(r"[,、，]", raw or ""):
            a = norm(part)
            if not a:
                continue
            exact = [gid for gid, n in gn if n == a]
            cands = exact or ([gid for gid, n in gn if len(a) >= 3 and n.startswith(a)])
            if len(cands) == 1:
                ids.add(cands[0])
                break
    return ids


def decide(girls, week, dates, boxes, absent_ids, now):
    """決まりで並べた女の子の番号の並びと、段ごとの人数。
    girls: 新着んの今の並び [{id, name, fixed}] ／ week: {id: {日付文字: [状態, 始, 終]}}（状態 '2'＝出勤）
    dates: スケジュールの画面の日付の並び ['2026-10-18', …] ／ boxes: parse_standby の結果（9:00 より前は空）
    absent_ids: 当欠の子の番号"""
    now_bd = biz_minutes(now)
    today = biz_date(now)
    pos = {g["id"]: i for i, g in enumerate(girls)}
    box_of = match_boxes(girls, boxes) if boxes else {}
    G = {k: [] for k in (1, 2, 3, 4, 5, 6)}
    for g in girls:
        if g.get("fixed"):
            continue
        gid = g["id"]
        days = week.get(gid) or {}
        bx = box_of.get(gid)
        # 今日の出勤：ヘブンの箱（その場で変わる）→ 新着んのスケジュール（ヘブンから毎時取り込み）の順に見る
        shift = bx["shift"] if bx and bx.get("shift") else None
        tcol = days.get(today.isoformat())
        if shift is None and tcol and tcol[0] == "2" and tcol[1] and tcol[2]:
            shift = span(tcol[1], tcol[2])
        if gid in absent_ids:
            G[4].append((pos[gid], gid))
            continue
        if shift is None:
            nxt = None
            for d in dates:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
                c = days.get(d)
                if dd > today and c and c[0] == "2":
                    nxt = ((dd - today).days, bd(*c[1].split(":")) if c[1] else 0)
                    break
            if nxt:
                G[5].append(((nxt[0], nxt[1], pos[gid]), gid))
            else:
                G[6].append((pos[gid], gid))
            continue
        s, e = shift
        st = bx["status"] if bx else "none"
        if st == "waiting":
            G[1].append(((s, pos[gid]), gid))
        elif st == "next" and bx.get("next") is not None:
            G[2].append(((bx["next"], pos[gid]), gid))
        elif st == "closed" or now_bd >= e:
            G[3].append(((s, pos[gid]), gid))
        elif now_bd < s:
            G[2].append(((s, pos[gid]), gid))
        else:
            G[2].append(((now_bd, pos[gid]), gid))      # 出勤中なのに即ヒメに状態が無い子は「今」空いている扱い
    rest = []
    for k in (1, 2, 3, 4, 5, 6):
        rest += [gid for _, gid in sorted(G[k], key=lambda x: x[0])]
    # ピン留めの子は今の位置のまま（先頭に寄せない）。ほかの子でまわりを埋める
    it = iter(rest)
    order = [g["id"] if g.get("fixed") else next(it) for g in girls]
    counts = {"①待機中": len(G[1]), "②接客中・出勤前": len(G[2]), "③終わった": len(G[3]),
              "④当欠": len(G[4]), "⑤先の出勤あり": len(G[5]), "⑤先の出勤なし": len(G[6]),
              "ピン留め": sum(1 for g in girls if g.get("fixed"))}
    return order, counts


def moved(cur, order):
    """今の並びから位置が変わる子の数と、最初に違う順位（1から）"""
    n = sum(1 for a, b in zip(cur, order) if a != b)
    first = next((i + 1 for i, (a, b) in enumerate(zip(cur, order)) if a != b), None)
    return n, first


def in_window(now):
    return START_MIN <= biz_minutes(now) < END_MIN


def gap_ok(now, last_sent_iso):
    if not last_sent_iso:
        return True
    try:
        return (now - datetime.fromisoformat(last_sent_iso)).total_seconds() >= MIN_GAP_SEC
    except Exception:
        return True


# ---------- 記録（名前なし） ----------
def load_status():
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        import requests
        r = requests.get(f"{PUBLIC_RAW}/si9_status.json", timeout=15)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def save_status(st):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


# ---------- 材料を読む ----------
def read_boxes(shopdir):
    from heaven_http import HeavenClient, load_credentials
    a, p, d = load_credentials(shopdir)
    cli = HeavenClient(a, p, direct=d)
    cli.login_and_select(shopdir)
    return parse_standby(cli._get(f"/C9StandbyGirlList.php?shopdir={shopdir}").text)


def read_absent(shopdir, now):
    """CTIの当欠の名前（その店の子と、店の印が無い子）。即ヒメ直しが残したものが新しければそれを、無ければCTIを読む"""
    people = None
    try:
        c = json.loads(CTI_CACHE.read_text(encoding="utf-8"))
        if (now - datetime.fromisoformat(c["at"])).total_seconds() <= CTI_CACHE_MAX_SEC:
            people = c["people"]
    except Exception:
        people = None
    src = "即ヒメ直しの読んだもの"
    if people is None:
        if not os.environ.get("CTI_PASSWORD"):
            return [], "CTIの鍵なし（当欠は見ない）"
        import cti_schedule
        rows, _ = cti_schedule.read_today(now)
        people = [{"shop": p["shop"], "name": p["name"], "absent": bool(p.get("absent"))} for p in rows]
        try:
            CTI_CACHE.write_text(json.dumps({"at": now.isoformat(), "people": people}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        src = "CTIを読んだ"
    return [p["name"] for p in people if p.get("absent") and p.get("shop") in (shopdir, None)], src


def si9_login(page, shop_name):
    uid = os.environ.get("SINCHAKUN_ID", "").strip()
    pw = os.environ.get("SINCHAKUN_PASSWORD", "").strip()
    if not uid or not pw:
        raise RuntimeError("金庫に SINCHAKUN_ID / SINCHAKUN_PASSWORD が無い")
    page.goto(f"{BASE}/index.php", wait_until="domcontentloaded", timeout=60000)
    page.fill("input[name=login_id]", uid)
    page.fill("input[name=login_password]", pw)
    page.click("#loginform button[type=submit]")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(3000)
    if page.query_selector("input[name=login_password]"):
        raise RuntimeError("新着んにログインできない（ログイン画面のまま）")
    if "shopSelect" in page.url:
        ok = page.evaluate("(t) => { const a = Array.from(document.querySelectorAll('a')).find(a => (a.innerText || '').trim() === t); if (!a) return false; a.click(); return true; }", shop_name)
        if not ok:
            raise RuntimeError("新着んの店を選ぶ画面に店が見つからない")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)


WEEK_JS = """() => { const dates = [];
    document.querySelectorAll('div.headerDate[data-id]').forEach(d => { const v = d.getAttribute('data-id'); if (!dates.includes(v)) dates.push(v); });
    const rows = {};
    for (const tr of document.querySelectorAll('tr[data-gid]')) {
      rows[tr.getAttribute('data-gid')] = Array.from(tr.querySelectorAll('td.scheduleBlocks')).slice(0, 7)
        .map(td => [td.getAttribute('data-status') || '', td.getAttribute('data-stime') || '', td.getAttribute('data-etime') || '']);
    }
    const m = (document.body.innerText.match(/全\\s*(\\d+)\\s*人中\\s*(\\d+)\\s*人～\\s*(\\d+)\\s*人/) || []);
    return {dates: dates.slice(0, 7), rows, total: +(m[1] || 0), from: +(m[2] || 0), to: +(m[3] || 0)}; }"""


def _read_all_pages(page):
    """今出ている週を、ページを送りながら全員分読む。返す: (日付の並び, {番号: [7日分]}, 全体の人数)"""
    try:
        if page.evaluate("() => { const s = document.querySelector('select[name=max_row]'); return s ? s.value : ''; }") != "200":
            page.select_option("select[name=max_row]", "200")
    except Exception:
        pass
    r = None
    for _ in range(40):
        page.wait_for_timeout(500)
        r = page.evaluate(WEEK_JS)
        if r["total"] and r["to"] >= min(r["total"], 200) and len(r["rows"]) >= min(r["total"], 200):
            break
    dates, rows = r["dates"], dict(r["rows"])
    for _ in range(10):
        if not r["total"] or r["to"] >= r["total"]:
            break
        cur_to = r["to"]
        if not page.evaluate("() => { const n = Array.from(document.querySelectorAll('.cdp_i.pager')).find(e => (e.innerText || '').trim() === '次へ'); if (!n) return false; n.click(); return true; }"):
            break
        for _ in range(40):
            page.wait_for_timeout(500)
            r = page.evaluate(WEEK_JS)
            if r["to"] > cur_to:
                break
        rows.update(r["rows"])
    return dates, rows, r["total"]


def read_week(page, need_date):
    """スケジュールの画面：{女の子の番号: {日付: [状態, 始, 終]}}・日付の並び・全体の人数。
    画面は30人ずつのページ分け（10/18 試しでは30人しか読めていなかった）→ 200件表示にして、残りはページを送る。
    夜中（0:00〜5:59）に画面の「本日」がもう次の日になっていたら、「前の週へ」で営業日の列も読む"""
    page.goto(f"{BASE}/schedule/list.php", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    dates, rows, total = _read_all_pages(page)
    week = {gid: {dates[i]: c for i, c in enumerate(cols) if i < len(dates)} for gid, cols in rows.items()}
    if dates and need_date not in dates and need_date < dates[0]:
        first = dates[0]
        page.click("#prev_week")
        for _ in range(40):
            page.wait_for_timeout(500)
            if page.evaluate(WEEK_JS)["dates"][:1] != [first]:
                break
        d2, rows2, _ = _read_all_pages(page)
        for gid, cols in rows2.items():
            for i, c in enumerate(cols):
                if i < len(d2) and d2[i] == need_date:
                    week.setdefault(gid, {})[need_date] = c
        if need_date in d2:
            dates = [need_date] + dates
    return week, dates, total


def read_sort(page):
    page.goto(f"{BASE}/girl/sort.php", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    shop_id = page.evaluate("() => (document.querySelector('#shop_id') || {}).value || ''")
    girls = page.evaluate("""() => Array.from(document.querySelectorAll('#girlList li.girlBox')).map(li => ({
        id: (li.querySelector('[name^=girlid]') || {}).value || '',
        name: (li.querySelector('[name^=girlname]') || {}).value || '',
        fixed: li.classList.contains('fixedBox')}))""")
    for g in girls:
        _NAMES.add(g["name"])
        _NAMES.add(norm(g["name"]))
    return shop_id, girls


def is_sorting(page):
    r = page.evaluate("""async () => { try { return JSON.stringify(await $.ajax({type: 'POST', url: '/ajax/girl/data.php', data: {method: 'is_sorting'}})); }
        catch (e) { return 'ERR ' + e.status; } }""")
    s = str(r).replace(" ", "")
    if '"is_sorting":false' in s:
        return False
    if '"is_sorting":true' in s:
        return True
    return None          # 分からない


def jconfirm_yes(page, want, timeout=8000):
    """jquery-confirm の窓に want の文字があれば「はい」。違う窓なら押さずに止める"""
    t0 = time.time()
    while time.time() - t0 < timeout / 1000:
        txt = page.evaluate("() => { const j = document.querySelector('.jconfirm'); return j ? j.innerText.replace(/\\s+/g, ' ').slice(0, 200) : ''; }")
        if txt:
            if want not in txt:
                raise RuntimeError("思っていない確認の窓が出た: " + scrub(txt[:60]))
            page.click(".jconfirm button:has-text('はい')")
            page.wait_for_timeout(1500)
            return True
        page.wait_for_timeout(300)
    return False


def save_order(page, order):
    """人がパネルを動かして「並び順の保存」を押すのと同じ。読み直して案と同じかを返す"""
    page.evaluate("""(ids) => { const ul = document.querySelector('#girlList');
        const lis = {}; ul.querySelectorAll('li.girlBox').forEach(li => { lis[(li.querySelector('[name^=girlid]') || {}).value] = li; });
        ids.forEach(id => { if (lis[id]) ul.appendChild(lis[id]); });
        updateSequenceChanged(1); }""", order)
    page.wait_for_timeout(800)
    page.click("#seqChangeBtn")
    if not jconfirm_yes(page, "並び順を保存"):
        raise RuntimeError("「並び順を保存します」の窓が出ない")
    page.wait_for_timeout(4000)
    _, girls2 = read_sort(page)
    return [g["id"] for g in girls2] == order


def send_sites(page, n_girls):
    """「サイトへ並び替えを更新」→ 女の子は全員 →「次　へ」→ 全サイトにチェック →「更新スタート」→（はい）。返す: 送り先の数"""
    page.click("#update_site")
    page.wait_for_timeout(2500)
    page.wait_for_selector("#gListChecker", state="visible", timeout=20000)
    checked = page.evaluate("""() => { let n = 0;
        document.querySelectorAll('#colorbox input[name^=girlid]').forEach(c => { if (!c.checked) c.click(); if (c.checked) n++; });
        return n; }""")
    if checked < n_girls:
        raise RuntimeError(f"送る女の子が全員選べない（{checked}/{n_girls}人）")
    page.click("#gListChecker")
    page.wait_for_timeout(1500)
    n_sites = page.evaluate("""() => { let n = 0;
        document.querySelectorAll('#colorbox input[name="siteid[]"]').forEach(c => { if (!c.checked) c.click(); if (c.checked) n++; });
        return n; }""")
    if not n_sites:
        raise RuntimeError("送り先のサイトが1つも選べない")
    page.click("#sortExecuteBtn")
    if not jconfirm_yes(page, "並び替えを行います"):
        raise RuntimeError("「女の子の並び替えを行います」の窓が出ない")
    return n_sites


RESULT_ROW = re.compile(r"^(\d{1,2})時(\d{1,2})分\s*並び替え\s*(.+)$")


def read_results(page, sent_at):
    """更新結果の画面のいちばん新しい回（「17時10分 並び替え ◯◯」）の、成功・失敗の数をサイトごとに数える（名前は読まない）。
    その回が送った時刻の前後3分に入っていなければ、今回の分ではないので数えない"""
    page.goto(f"{BASE}/girl/updateResult.php?mode=sort", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('table tr')).map(tr => ({
        t: tr.innerText.replace(/\\s+/g, ' ').trim(), href: (tr.querySelector('a') || {}).href || ''})).filter(x => x.href)""")
    parsed = [(RESULT_ROW.match(r["t"]), r["href"]) for r in rows]
    parsed = [(m, h) for m, h in parsed if m]
    if not parsed:
        return {"_": "結果の行が無い"}
    h0, m0 = int(parsed[0][0].group(1)), int(parsed[0][0].group(2))
    sent_min = sent_at.hour * 60 + sent_at.minute
    if min(abs(h0 * 60 + m0 - sent_min), 1440 - abs(h0 * 60 + m0 - sent_min)) > 3:
        return {"_": f"いちばん新しい結果が {h0}:{m0:02d}（送ったのは {sent_at.strftime('%H:%M')}）"}
    out = {}
    for m, href in parsed[:10]:
        if (int(m.group(1)), int(m.group(2))) != (h0, m0):
            break
        site = m.group(3).strip()[:30]
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        out[site] = page.evaluate("""() => { const n = {}; for (const td of document.querySelectorAll('td')) { const t = td.innerText.trim();
            if (['成功', '失敗', '対象外', 'エラー'].includes(t)) n[t] = (n[t] || 0) + 1; } return n; }""")
    return out


# ---------- 本体 ----------
def inputs_trouble(boxes, box_err, absent_err, girls, week, now):
    """材料が欠けていて、送ると並びが行ったり来たりしそうな時の理由（無ければ空）。案は作るが送らない"""
    bm = biz_minutes(now)
    if box_err and bm >= BOX_FROM:
        return "ヘブンの即ヒメが読めない"
    if absent_err:
        return "CTIの当欠が読めない"
    today = biz_date(now).isoformat()
    working = sum(1 for g in girls if (week.get(g["id"], {}).get(today) or [""])[0] == "2")
    if bm >= 10 * 60 + 30 and working and not boxes:
        return f"今日出勤の子が{working}人いるのに、即ヒメの箱が0"
    if boxes and not match_boxes(girls, boxes):
        return "即ヒメの箱の名前が新着んの名前と1つも合わない"
    return ""


def run_shop(shopdir, now, mode, st):
    from playwright.sync_api import sync_playwright
    shop_name, shop_no = SHOPS[shopdir]
    rec = st.setdefault("shops", {}).setdefault(shopdir, {})
    rec.update(at=now.isoformat(timespec="seconds"), error="", skipped="")
    if mode == "send" and not gap_ok(now, rec.get("sent_at")):
        print(f"  {shopdir}: 前に送ってから10分たっていないので、今回は見ない")
        rec["skipped"] = "前に送ってから10分以内"
        return 0
    boxes, box_err = [], ""
    if biz_minutes(now) >= BOX_FROM:
        try:
            boxes = read_boxes(shopdir)
        except Exception as e:
            box_err = type(e).__name__
            print(f"  {shopdir}: ヘブンの即ヒメが読めない {box_err}")
    absent, absent_err = [], ""
    try:
        absent, src = read_absent(shopdir, now)
    except Exception as e:
        absent_err, src = type(e).__name__, f"CTIが読めない {type(e).__name__}"
    print(f"  {shopdir}: 即ヒメの箱{len(boxes)}（待機中{sum(1 for b in boxes if b['status'] == 'waiting')}・次回{sum(1 for b in boxes if b['status'] == 'next')}・受付終了{sum(1 for b in boxes if b['status'] == 'closed')}）・当欠{len(absent)}人（{src}）")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1400, "height": 1000}).new_page()
            page.on("dialog", lambda d: d.dismiss())
            si9_login(page, shop_name)
            today = biz_date(now).isoformat()
            week, dates, total = read_week(page, today)
            shop_id, girls = read_sort(page)
            if str(shop_id) != shop_no:
                raise RuntimeError(f"新着んの店番号が違う（{shop_id}）")
            if not girls:
                raise RuntimeError("並び替えの画面に女の子がいない")
            if not total or len(week) < min(total, len(girls)):
                raise RuntimeError(f"スケジュールが全部読めていない（{len(week)}/{total}人）")
            if today not in dates:
                raise RuntimeError(f"スケジュールに営業日（{today}）の列が無い（{dates[:2]}…）")
            absent_ids = match_absent(girls, absent)
            order, counts = decide(girls, week, dates, boxes, absent_ids, now)
            cur = [g["id"] for g in girls]
            if sorted(order) != sorted(cur):
                raise RuntimeError("案の人数が今の並びと合わない")
            n_moved, first = moved(cur, order)
            mb = len(match_boxes(girls, boxes))
            rec.update(girls=len(girls), schedule=len(week), boxes=len(boxes), matched_boxes=mb,
                       absent=len(absent), absent_matched=len(absent_ids), counts=counts, moved=n_moved, first_diff=first)
            print(f"  {shopdir}: 女の子{len(girls)}人・スケジュール{len(week)}人・箱と合った{mb}/{len(boxes)}・当欠と合った{len(absent_ids)}/{len(absent)} → {counts}")
            if order == cur:
                print(f"  {shopdir}: 今の並びのままでよい（送らない）")
                return 0
            print(f"  {shopdir}: 動く子{n_moved}人（最初に違うのは{first}位）")
            trouble = inputs_trouble(boxes, box_err, absent_err, girls, week, now)
            if trouble:
                print(f"  {shopdir}: {trouble}ので、今回は送らない（並びが行ったり来たりしないように）")
                rec["skipped"] = trouble
                return 0
            if mode != "send":
                print(f"  {shopdir}: 案を作るだけ（SI9_SORT_MODE が send でない）")
                rec["skipped"] = "案を作るだけ"
                return 0
            busy = is_sorting(page)
            if busy is not False:
                print(f"  {shopdir}: 新着んが送っている途中か分からない（{busy}）ので、今回は送らない")
                rec["skipped"] = "新着んが送っている途中"
                return 0
            if not save_order(page, order):
                raise RuntimeError("保存した並びが案と違う（送らない）")
            sent_at = datetime.now(JST)
            n_sites = send_sites(page, len(girls))
            rec.update(sent_at=sent_at.isoformat(timespec="seconds"), sent_moved=n_moved, sites=n_sites, result={}, done=False)
            print(f"  {shopdir}: 並び順を保存して、{n_sites}サイトへ送った")
            t0 = time.time()
            while time.time() - t0 < SEND_WAIT_SEC:
                time.sleep(10)
                if is_sorting(page) is False:
                    rec["done"] = True
                    break
            if rec["done"]:
                res = read_results(page, sent_at)
                rec["result"] = res
                bad = [k for k, v in res.items() if isinstance(v, dict) and (v.get("失敗") or v.get("エラー"))]
                print(f"  {shopdir}: 送り終わった。結果 {scrub(res)}" + (f" ⚠️失敗あり {bad}" if bad else ""))
            else:
                print(f"  {shopdir}: {SEND_WAIT_SEC}秒では終わらなかった（新着んの中で続いている。次の回は終わるまで送らない）")
            return 0
        finally:
            b.close()


def main():
    now = datetime.now(JST)
    shops = [s.strip() for s in os.environ.get("SI9_SORT_SHOPS", "").split(",") if s.strip() in SHOPS]
    mode = os.environ.get("SI9_SORT_MODE", "").strip()
    if "--dry" in sys.argv:
        mode = "dry"
    if not shops:
        print("新着んの並び替え: 店が決まっていない（SI9_SORT_SHOPS が空）ので何もしない")
        return 0
    if not in_window(now):
        print(f"新着んの並び替え: 時間外（{now.strftime('%H:%M')}。動くのは 8:00〜翌2:00）")
        return 0
    st = load_status()
    rc = 0
    print(f"新着んの並び替え（{'送る' if mode == 'send' else '案を作るだけ'}）")
    for shopdir in shops:
        try:
            rc |= run_shop(shopdir, now, mode, st)
        except Exception as e:
            msg = scrub(f"{type(e).__name__}: {str(e)[:160]}")
            print(f"  {shopdir}: 止めた {msg}")
            st.setdefault("shops", {}).setdefault(shopdir, {})["error"] = msg
            rc = 1
    st["at"] = now.isoformat(timespec="seconds")
    st["mode"] = mode
    save_status(st)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
