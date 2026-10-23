"""新着ん（si9.jp）の女の子の並び替えを、お店の決まりで自動に保つ（一希さん 2026-10-18「要は脱属人化」）。

いつ：朝 8:00〜翌 2:00、約5分ごと（collect_loop.py の即ヒメ直しのあと）。
      スタッフが 8:30〜9:00 に来た時にはもう並んでいるように、朝は 8 時の回から並べる（一希さん 10/18）。
何を：
  1. ヘブン管理画面の「即ヒメ登録」を読む（店舗IDでHTTP。読むだけ。9:00 より前は読まない）
  2. CTIの本日スケジュールの「当欠」（即ヒメ直しが読んだものを使う。無ければ自分で読む。読むだけ）
  3. 新着んにログインして、スケジュール（今日から7日。全員分）と今の並びを読む
  4. 決まりで並べた案を、まだ7サイトへ送れていない時だけ「並び順の保存」→ 読み直して確かめる →「サイトへ並び替えを更新」→ 7サイト
     ・前に送ってから10分以上あいている時だけ（新着んの更新結果の画面のいちばん新しい「並び替え」の時刻で確かめる。人が送った分も数える）
     ・新着んが送っている途中なら何もしない／材料（即ヒメ・CTI・スケジュール）が欠けた回は送らない（並びが行ったり来たりしないように）
     ・保存したのに送れなかった・サイトで失敗があった時は、同じ並びをもう1回だけ送り直す
決まり（Addness 98564629-4105-41ba-931a-56f3455199f5）：
  ① 待機中（即ヒメ「◯◯〜待機中」）：出勤の早い順
  ② 接客中と出勤前を混ぜて、空く時刻（即ヒメ「次回◯◯〜」。出勤前で表示が無ければ出勤時刻）の早い順
  ③ 受付終了と、その日の仕事が終わった子：出勤の早い順
  ④ 今日休みになった子（CTIの「当欠」）
  ⑤ 今日出勤しない子：先の出勤が上がっている子（近い順）→ 上がっていない子（今の並びのまま）
  同じ段・同じ時刻の中は、今の並びで上にいる方を先にする（並びが毎回揺れない）。ピン留めの子は今の位置のまま触らない。
公開リポジトリなので、ログにも記録にも女の子の名前は出さない（人数と段ごとの数だけ）。
切り替え（毎回読む。走っている途中でも次の回から効く）：main の si9_mode.json
  {"shops": ["cg_kirakira"], "mode": "send"}   mode: "send"＝保存して送る／"dry"＝案を作って数を出すだけ／"off"＝何もしない
  読めない時は何もしない。急いで止める時は mode を "off" にする（実行を取り消すだけだと、すぐ次の実行が始まる）
"""
import hashlib
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
CHANGED_FILE = OUT_DIR / "si9_changed"        # 保存・送信をした回の印（collect_loop がすぐ公開し直す）
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
# 即ヒメ直しが読んだCTIの当欠（名前を含むので、公開の out/ には置かない。この実行の中だけの置き場）
CTI_CACHE = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "cti_people.json"
CTI_CACHE_MAX_SEC = 15 * 60

# ヘブンの店 → (新着んの「店を選ぶ」画面のリンクの文字（そのまま）, 新着んの店番号 #shop_id)。
# 動かす店は si9_mode.json の shops で選ぶ（10/18 はキラ学だけ。1日見てから広げる）
SHOPS = {
    "cg_kirakira": ("素人専門キラキラ学園", "896"),
    "undercover": ("ハイブリッド痴女性感UNDER COVER", "895"),
    "potya_reen": ("ぽちゃりーん", "897"),
    "s_matikado": ("素人専門 街角レディ", "2525"),
    "venus_okayama": ("ハイブリットエステVENUS", "3045"),
    "torori_angel": ("とろ～りAngel", "4326"),
    "mrs_orange": ("素人ミセス・オレンジな気持ち", "4556"),
}
DAY_CUT_H = 6                       # 営業日の区切り：0:00〜5:59 は前の日の続き（24:00〜29:59）
START_MIN, END_MIN = 8 * 60, 26 * 60  # 動く時間：8:00〜翌2:00
BOX_FROM = 9 * 60                   # 即ヒメの箱を見るのは 9:00 から（即ヒメ直しと同じ。それより前は誰も待機していない）
OPEN_MIN = 10 * 60                  # 開店。これより後に即ヒメの箱が0なのはおかしい
MIN_GAP_SEC = 10 * 60               # 送るのは、前に送ってから10分以上あけて
MAX_TRIES = 2                       # 同じ並びを送る回数の上限（1回目＋送り直し1回）
SEND_WAIT_SEC = 45                  # 送った後、その場で終わるのを待つ長さ（終わらなければ次の回で結果を確かめる）
_NAMES = set()     # 読んだ女の子の名前（ログに出す前に伏せる用。外には出さない）


def scrub(text):
    """ログに出す文から、読んだ女の子の名前を伏せる（公開リポジトリのログなので）。切る前に伏せること"""
    t = str(text)
    for n in sorted(_NAMES, key=len, reverse=True):
        if len(n) >= 2:
            t = t.replace(n, "＊＊")
    return t


def safe_err(e, n=160):
    """例外を、ログと公開の記録に出せる形に。Playwright の例外は2行目以降（Call log）に画面のHTMLが入るので、1行目だけ"""
    first = str(e).split("\n")[0].split("Call log")[0]
    return scrub(f"{type(e).__name__}: {first}")[:n]


def fp(ids):
    """並びの指紋（女の子の番号だけから作る。名前は入らない）"""
    return hashlib.sha256(",".join(ids).encode()).hexdigest()[:16]


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


def span(s, e=None):
    """出勤の始まり・終わり（営業日の分）。終わりが無ければ閉店（翌2:00）。終わりが始まり以下なら次の日"""
    s = bd(*s.split(":"))
    e = bd(*e.split(":")) if e else END_MIN
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


def is_cut(raw):
    """ヘブンの箱の名前が途中で切れているか。「◯◯【…」は飾りの所で切れただけで名前は全部ある→切れていない"""
    t = unicodedata.normalize("NFKC", raw or "").strip()
    if re.search(r"[\[【(（]", t):
        return False
    return bool(re.search(r"(…|\.{2,}|．{2,})$", t))


LETTER = re.compile(r"[0-9A-Za-zぁ-んァ-ヶー一-龠々]")


# ---------- ヘブンの即ヒメ登録の画面 ----------
TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[～〜~]\s*(\d{1,2}):(\d{2})")
TIME_FROM = re.compile(r"(\d{1,2}):(\d{2})\s*[～〜~]")


def parse_standby(html):
    """1人分の箱 → {short: 突き合わせ用の名前, cut: 名前が途中で切れているか, status: waiting/next/closed/none, shift: (始, 終), next: 空く時刻}。
    箱の作り（10/17 キラ学で確認）：名前＝幅105pxの欄（長い名前は「…」で切れる）・出勤＝colspan の欄「12:00～ 2:00」・
    状態＝赤い字の欄「16:26～待機中」「次回17:58～」「受付終了」・コメント＝marquee の欄（ここの数字は見ない）"""
    from bs4 import BeautifulSoup
    out = []
    for t in BeautifulSoup(html, "html.parser").find_all("table", class_=re.compile(r"^sokuhimegirlbox")):
        name_td = t.find("td", style=re.compile(r"width:\s*105px"))
        name = name_td.get_text(" ", strip=True) if name_td is not None else ""
        shift = None
        for td in t.find_all("td"):
            if not td.get("colspan") or "marquee" in (td.get("class") or []):
                continue
            tx = td.get_text(" ", strip=True)
            m = TIME_RANGE.search(tx)
            if m:
                shift = span(f"{m.group(1)}:{m.group(2)}", f"{m.group(3)}:{m.group(4)}")
                break
            m = TIME_FROM.search(tx)          # 「12:00～ LAST」のように終わりが時刻でない時は、終わり＝閉店
            if m:
                shift = span(f"{m.group(1)}:{m.group(2)}")
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
        out.append({"short": norm(name), "cut": is_cut(name), "status": st, "shift": shift,
                    "since": bd(w.group(1), w.group(2)) if w else None,
                    "next": bd(nx.group(1), nx.group(2)) if nx else None})
    return out


# ---------- 決める（ここは画面に触らない。テストで確かめる） ----------
def working_today(girls, week, today):
    """新着んのスケジュールで今日出勤（'2'）の子の番号"""
    return {g["id"] for g in girls if (week.get(g["id"], {}).get(today) or [""])[0] == "2"}


def match_boxes(girls, boxes, working=frozenset()):
    """新着んの女の子 → ヘブンの箱。まず名前の完全一致。箱の名前が途中で切れている時だけ前方一致にし、
    候補が何人もいれば今日出勤の子に絞る。1人に決まる時だけ使う。2つの箱が同じ子に合った時は、どちらも使わない"""
    gn = [(g["id"], norm(g["name"])) for g in girls]
    out, used = {}, {}
    for bx in boxes:
        s = bx.get("short")
        if not s:
            continue
        exact = [gid for gid, n in gn if n == s]
        pick = None
        if len(exact) == 1:
            pick = exact[0]
        elif not exact and bx.get("cut"):
            cands = [gid for gid, n in gn if n.startswith(s)]
            w = [c for c in cands if c in working]
            pool = w or cands
            if len(pool) == 1:
                pick = pool[0]
        if pick is not None:
            used[pick] = used.get(pick, 0) + 1
            out[pick] = bx
    return {gid: bx for gid, bx in out.items() if used[gid] == 1}


def match_absent(girls, absent, shopdir):
    """CTIの当欠 [(名前, 店)] → 新着んの女の子の番号。CTIの名前は「◯◯,△△RSP」のように別名がカンマでつながることがある。
    名前の完全一致だけ。その店の印の子に限り、残りが記号だけの前方一致（「◯◯♡」）も許す。1人にだけ合う時だけ使う。
    店の印が無い子（複数の店に出ている子）は完全一致だけ（よその店の子を取り違えないように）"""
    ids = set()
    gn = [(g["id"], norm(g["name"])) for g in girls]
    for raw, shop in absent:
        for part in re.split(r"[,、，]", raw or ""):
            a = norm(part)
            if not a:
                continue
            cands = [gid for gid, n in gn if n == a]
            if not cands and shop == shopdir:
                cands = [gid for gid, n in gn if n.startswith(a) and not LETTER.search(n[len(a):])]
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
    box_of = match_boxes(girls, boxes, working_today(girls, week, today.isoformat())) if boxes else {}
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
        if shift is None and tcol and tcol[0] == "2" and tcol[1]:
            shift = span(tcol[1], tcol[2] or None)
        if shift is None and bx and bx["status"] in ("waiting", "next", "closed"):
            # 箱に状態があるのに出勤の時刻が読めない（取り込み前に足された出勤など）：今日出勤している子として扱う
            shift = (bx.get("since") or now_bd, END_MIN)
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


def _ts(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except Exception:
        return None


def gap_ok(now, *isos):
    """前に送ってから（送り始めてから）10分以上たったか。記録が無ければ True（本当の確かめは新着んの更新結果の時刻でする）"""
    ts = [t for t in (_ts(i) for i in isos) if t is not None]
    return not ts or (now - max(ts)).total_seconds() >= MIN_GAP_SEC


def minutes_since(now, h, m):
    """「◯時◯分」から今まで何分たったか（日付が無いので、0〜1439 分のどれか）"""
    return (now.hour * 60 + now.minute - (h * 60 + m)) % 1440


def someone_sent_after(now, sent_iso, rows):
    """新着んの更新結果のいちばん新しい並び替えが、私たちが送った時より後（3分より後）か＝人が送ったか"""
    ours = _ts(sent_iso)
    if not rows or ours is None:
        return False
    age_ours = (now - ours).total_seconds() / 60
    if age_ours > 1200:                  # 20時間より前の記録とは時刻だけでは比べられない
        return False
    return minutes_since(now, rows[0][0], rows[0][1]) + 3 < age_ours


def need_send(order, cur, rec):
    """送る必要があるか。返す: (保存も要るか, 送る必要があるか, 理由)。
    新着んに保存した並び＝7サイトにある並び、とは限らない（保存の後に送れずに止まった回がありうる）ので、
    最後に7サイトへ送れたと確かめた並びの指紋（ok_fp）とくらべる。同じ並びは MAX_TRIES 回まで"""
    f = fp(order)
    save = order != cur
    if rec.get("ok_fp") == f:
        return save, False, "7サイトはもうこの並び"
    tries = rec.get("tries") or {}
    if tries.get("fp") == f and int(tries.get("n") or 0) >= MAX_TRIES:
        return save, False, f"同じ並びを{MAX_TRIES}回送ったので、並びが変わるまで待つ"
    return save, True, "並びが変わった" if save else "前の送信が確かめられていない"


def inputs_trouble(boxes, box_err, absent_err, girls, week, now, matched):
    """材料が欠けていて、送ると並びが行ったり来たりしそうな時の理由（無ければ空）。案は作るが送らない"""
    bm = biz_minutes(now)
    if box_err and bm >= BOX_FROM:
        return "ヘブンの即ヒメが読めない"
    if absent_err:
        return "CTIの当欠が読めない"
    working = len(working_today(girls, week, biz_date(now).isoformat()))
    if bm >= OPEN_MIN and working and not boxes:
        return f"今日出勤の子が{working}人いるのに、即ヒメの箱が0"
    if boxes and not matched:
        return "即ヒメの箱の名前が新着んの名前と1つも合わない"
    # 即ヒメに登録できる人数には上限があるので、出勤の全員が箱にいるとは限らない。箱のうち合った子が半分未満の時だけ止める
    if len(boxes) >= 4 and matched * 2 < len(boxes):
        return f"即ヒメの箱と合った子が少ない（{matched}/{len(boxes)}箱）"
    return ""


def result_ok(res, n_sites):
    """更新結果が「シティヘブンに届いた」か。返す: (よいか, だめな理由か注意)。
    目的は表ページ（シティヘブン）の並びなので、ヘブンの行が成功なら「送れた」。他のサイトの失敗は注意として残す
    （10/18 オレンジ：風俗じゃぱん・アンダーナビは新着ん側の設定で全員「失敗」。毎時の更新も前から失敗していて、こちらの送り方の問題ではない）"""
    if not isinstance(res, dict) or not res or "_" in res:
        return False, str((res or {}).get("_", "結果が無い"))[:60]
    heaven = [k for k in res if "ヘブン" in k]
    if not heaven:
        if len(res) < n_sites:
            return False, f"結果のサイトが{len(res)}/{n_sites}"
        return False, "ヘブンの行が無い"
    hv = res[heaven[0]]
    if hv.get("失敗") or hv.get("エラー"):
        return False, f"ヘブンで失敗 {hv.get('失敗') or hv.get('エラー')}人"
    if not hv.get("成功"):
        return False, "成功が1つも数えられない"
    bad = [k for k, v in res.items() if k not in heaven and (v.get("失敗") or v.get("エラー"))]
    return True, ("他サイトで失敗 " + "・".join(bad)[:60]) if bad else ""


# ---------- 記録（名前なし） ----------
def load_status():
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        import requests
        r = requests.get(f"{PUBLIC_RAW}/si9_status.json?t={int(time.time())}", timeout=15)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def save_status(st):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def mark_changed():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CHANGED_FILE.write_text("1", encoding="utf-8")


def live_config():
    """どの店を・どう動かすか。手で動かす時（si9-check）は環境変数、いつもの回は main の si9_mode.json を毎回読む"""
    env_shops = os.environ.get("SI9_SORT_SHOPS", "").strip()
    if env_shops:
        return [s.strip() for s in env_shops.split(",") if s.strip() in SHOPS], os.environ.get("SI9_SORT_MODE", "dry").strip()
    repo, tok = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    if not repo or not tok:
        return [], "off"
    import requests
    r = requests.get(f"https://api.github.com/repos/{repo}/contents/si9_mode.json?ref=main",
                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"si9_mode.json が読めない（{r.status_code}）")
    c = json.loads(r.text)
    return [s for s in c.get("shops", []) if s in SHOPS], str(c.get("mode", "off")).strip()


# ---------- 材料を読む ----------
def read_boxes(shopdir):
    """即ヒメ登録の画面を読む。ログイン画面に戻された・画面が違う時は「読めない」にする（0人と取り違えない）"""
    from heaven_http import HeavenClient, _is_login_page, load_credentials
    a, p, d = load_credentials(shopdir)
    cli = HeavenClient(a, p, direct=d)
    cli.login_and_select(shopdir)
    r = cli._get(f"/C9StandbyGirlList.php?shopdir={shopdir}")
    if r.status_code != 200 or _is_login_page(r) or "sokuhimeForm" not in r.text:
        raise RuntimeError(f"即ヒメの画面でない（{r.status_code}）")
    return parse_standby(r.text)


def read_absent(shopdir, now):
    """CTIの当欠 [(名前, 店)]（その店の子と、店の印が無い子）。即ヒメ直しが残したものが新しければそれを、無ければCTIを読む"""
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
    return [(p["name"], p.get("shop")) for p in people if p.get("absent") and p.get("shop") in (shopdir, None)], src


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
        # リンクの文字は「素人専門 街角レディ」のように空白の形（半角・全角・改行）が揺れるので、空白を全部外して比べる（10/18 街角で合わなかった）
        ok = page.evaluate("""(t) => { const z = s => (s || '').replace(/[\\s\\u3000]+/g, '');
            const a = Array.from(document.querySelectorAll('a')).find(a => z(a.innerText) === z(t)); if (!a) return false; a.click(); return true; }""", shop_name)
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
MAXROW_JS = "() => { const s = document.querySelector('select[name=max_row]'); return s ? s.value : ''; }"
NEXT_JS = "() => { const n = Array.from(document.querySelectorAll('.cdp_i.pager')).find(e => (e.innerText || '').trim() === '次へ'); if (!n) return false; n.click(); return true; }"


def _read_all_pages(page):
    """今出ている週を、ページを送りながら全員分読む。返す: (日付の並び, {番号: [7日分]}, 全体の人数)。
    読み切れなかった・途中で週がずれた時は止める（30人だけ読んで「全員」と取り違えないように）"""
    if page.evaluate(MAXROW_JS) != "200":
        page.select_option("select[name=max_row]", "200")
    r = None
    for _ in range(40):
        page.wait_for_timeout(500)
        r = page.evaluate(WEEK_JS)
        if r["total"] and r["to"] >= min(r["total"], 200) and len(r["rows"]) >= min(r["total"], 200):
            break
    dates, rows = r["dates"], dict(r["rows"])
    if not r["total"] or not dates:
        raise RuntimeError("スケジュールの人数か日付が読めない")
    for _ in range(10):
        if r["to"] >= r["total"]:
            break
        cur_to = r["to"]
        if not page.evaluate(NEXT_JS):
            break
        for _ in range(40):
            page.wait_for_timeout(500)
            r = page.evaluate(WEEK_JS)
            if r["to"] > cur_to:
                break
        if r["dates"] != dates:
            raise RuntimeError("スケジュールのページを送ったら週がずれた")
        rows.update(r["rows"])
    if len(rows) < r["total"] or r["to"] < r["total"]:
        raise RuntimeError(f"スケジュールが全部読めていない（{len(rows)}/{r['total']}人）")
    return dates, rows, r["total"]


def read_week(page, need_date):
    """スケジュールの画面：{女の子の番号: {日付: [状態, 始, 終]}}・日付の並び・全体の人数。
    画面は30人ずつのページ分け（10/18 試しでは30人しか読めていなかった）→ 200件表示にして、残りはページを送る。
    夜中（0:00〜5:59）に画面の「本日」がもう次の日になっていたら、「前の週へ」で営業日の列も読む"""
    page.goto(f"{BASE}/schedule/list.php", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    dates, rows, total = _read_all_pages(page)
    week = {gid: {dates[i]: c for i, c in enumerate(cols) if i < len(dates)} for gid, cols in rows.items()}
    if need_date not in dates and need_date < dates[0]:
        first = dates[0]
        page.click("#prev_week")
        for _ in range(40):
            page.wait_for_timeout(500)
            if page.evaluate(WEEK_JS)["dates"][:1] != [first]:
                break
        d2, rows2, _ = _read_all_pages(page)
        if need_date not in d2:
            raise RuntimeError(f"前の週にも営業日（{need_date}）の列が無い")
        if set(rows) - set(rows2):
            raise RuntimeError(f"前の週で読めない子がいる（{len(set(rows) - set(rows2))}人）")
        i = d2.index(need_date)
        for gid, cols in rows2.items():
            if i < len(cols):
                week.setdefault(gid, {})[need_date] = cols[i]
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


def parse_sorting(raw):
    """新着んの is_sorting の返事（JSONの文字列。文字列の中にJSONが入っていることもある）→ True/False。読めなければ None"""
    v = raw
    for _ in range(3):
        if isinstance(v, dict):
            if "is_sorting" in v:
                x = v["is_sorting"]
                return x if isinstance(x, bool) else (str(x).lower() in ("true", "1"))
            return None
        if isinstance(v, str):
            t = v.strip()
            if not t.startswith(("{", "[", '"')):
                return None
            try:
                v = json.loads(t)
            except Exception:
                return None
        else:
            return None
    return None


def is_sorting(page):
    # is_sorting は並び替えの画面からでないと 403 で弾かれる（10/23 確認：更新結果の画面からは ERR 403、並び替えの画面からは false）。
    # 403 だと前の送信が確かめられず、同じ並びを2回送っていた → 別の画面にいたら並び替えの画面へ移ってから聞く
    if "/girl/sort.php" not in (page.url or ""):
        page.goto(f"{BASE}/girl/sort.php", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
    r = page.evaluate("""async () => { try { const r = await $.ajax({type: 'POST', url: '/ajax/girl/data.php', data: {method: 'is_sorting'}});
        return typeof r === 'string' ? r : JSON.stringify(r); } catch (e) { return 'ERR ' + e.status; } }""")
    v = parse_sorting(r)
    if v is None:
        print("  （is_sorting の返事が読めない:", re.sub(r"[^\w{}\[\]:,\"' ]", "", str(r))[:80], "）")
    return v


def jconfirm_yes(page, want, timeout=8000):
    """jquery-confirm の窓に want の文字があれば「はい」。違う窓なら押さずに止める（窓の文はログに出さない）"""
    t0 = time.time()
    while time.time() - t0 < timeout / 1000:
        txt = page.evaluate("() => { const j = document.querySelector('.jconfirm'); return j ? j.innerText.replace(/\\s+/g, ' ').slice(0, 200) : ''; }")
        if txt:
            if want not in txt:
                raise RuntimeError(f"思っていない確認の窓が出た（{len(txt)}字・エラーの文字{'あり' if 'エラー' in txt else 'なし'}）")
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


def read_result_rows(page):
    """更新結果（並び替え）の画面の行 [(時, 分, サイト, 詳しい結果のURL)]。上ほど新しい"""
    page.goto(f"{BASE}/girl/updateResult.php?mode=sort", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('table tr')).map(tr => ({
        t: tr.innerText.replace(/\\s+/g, ' ').trim(), href: (tr.querySelector('a') || {}).href || ''})).filter(x => x.href)""")
    out = []
    for r in rows:
        m = RESULT_ROW.match(r["t"])
        if m:
            out.append((int(m.group(1)), int(m.group(2)), m.group(3).strip()[:30], r["href"]))
    return out


def read_results(page, sent_at, rows=None):
    """sent_at に送った回（前後3分）の、成功・失敗の数をサイトごとに数える（名前は読まない）。無ければ {"_": 理由}"""
    rows = rows if rows is not None else read_result_rows(page)
    mine = [r for r in rows if min(minutes_since(sent_at, r[0], r[1]), 1440 - minutes_since(sent_at, r[0], r[1])) <= 3]
    if not mine:
        return {"_": "今回の分の結果の行が無い"}
    out = {}
    for h, m, site, href in mine[:10]:
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        # 詳しい結果の行は <td class="girl_result_label"><a><span class="btn">成功</span>名前</a></td> の形（10/18 VENUSで確認）。
        # innerText では「成功名前」とくっつくので、先頭の言葉で見る
        out[site] = page.evaluate("""() => { const n = {}; const words = ['成功', '失敗', '対象外', 'エラー'];
            const cells = document.querySelectorAll('td.girl_result_label');
            for (const td of (cells.length ? cells : document.querySelectorAll('table tr'))) {
              const t = (td.innerText || '').replace(/\\s+/g, '');
              const w = words.find(x => t.startsWith(x)); if (w) n[w] = (n[w] || 0) + 1; } return n; }""")
    return out


# ---------- 本体 ----------
def confirm_last_send(page, rec, rows, now):
    """前の回で送ったきり結果を確かめていない時、その結果を読んで決める（全サイトへ届いたなら ok_fp を付ける）。
    新着んがまだ送っている途中・詳しい結果にまだ数字が無い時は決めない（送ってから30分までは次の回に回す）"""
    if not rec.get("sent_at") or rec.get("done"):
        return
    sent_at = datetime.fromisoformat(rec["sent_at"])
    if is_sorting(page) is not False:
        print("  前の送信: 新着んがまだ送っている途中")
        return
    res = read_results(page, sent_at, rows)
    ok, why = result_ok(res, int(rec.get("sites") or 0))
    if not ok and (why.startswith("成功が1つも") or "_" in res) and (now - sent_at).total_seconds() < 1800:
        print(f"  前の送信: まだ結果が出ていない（{why}。次の回で確かめる）")
        return
    rec.update(done=True, result=res, result_error=why)
    if ok:
        rec["ok_fp"] = rec.get("sent_fp")
    print(f"  前の送信の結果: {'全サイト失敗なし' if ok else '確かめられない・失敗あり（' + why + '）'}")


def box_counts(girls, boxes, week, today):
    """箱と合った数と、合わなかった箱の状態ごとの数（名前は出さない）"""
    got = match_boxes(girls, boxes, working_today(girls, week, today))
    ids = {id(x) for x in got.values()}
    unmatched = {}
    for x in boxes:
        if id(x) not in ids:
            unmatched[x["status"]] = unmatched.get(x["status"], 0) + 1
    return len(got), unmatched


def run_shop(shopdir, now, mode, rec, persist):
    """1店ぶん。rec＝この店の記録（読み書き）。persist()＝途中で打ち切られても残したい時に呼ぶ（記録を書く）"""
    from playwright.sync_api import sync_playwright
    shop_name, shop_no = SHOPS[shopdir]
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
            box_err = safe_err(e, 80)
            print(f"  {shopdir}: ヘブンの即ヒメが読めない {box_err}")
    absent, absent_err = [], ""
    try:
        absent, src = read_absent(shopdir, now)
    except Exception as e:
        absent_err = safe_err(e, 80)
        src = "CTIが読めない " + absent_err
    rec["absent_src"] = src
    print(f"  {shopdir}: 即ヒメの箱{len(boxes)}（待機中{sum(1 for b in boxes if b['status'] == 'waiting')}・次回{sum(1 for b in boxes if b['status'] == 'next')}・受付終了{sum(1 for b in boxes if b['status'] == 'closed')}）・当欠{len(absent)}人（{src}）")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 1400, "height": 1000}).new_page()
            page.on("dialog", lambda d: d.dismiss())
            si9_login(page, shop_name)
            # 送る回の頭で、新着んの更新結果（人が送った分も入る）を読む。前の送信の結果もここで確かめる
            rows, rows_err = None, ""
            if mode == "send":
                try:
                    rows = read_result_rows(page)
                    confirm_last_send(page, rec, rows, now)
                    if someone_sent_after(now, rec.get("sent_at"), rows):
                        rec["ok_fp"] = None          # 私たちの後に人が送った → 7サイトの並びはもう私たちの並びとは限らない
                except Exception as e:
                    rows, rows_err = None, safe_err(e, 80)
            today = biz_date(now).isoformat()
            week, dates, total = read_week(page, today)
            shop_id, girls = read_sort(page)      # 保存は並び替えの画面の上でするので、これを最後に開く
            if str(shop_id) != shop_no:
                raise RuntimeError(f"新着んの店番号が違う（{shop_id}）")
            if not girls:
                raise RuntimeError("並び替えの画面に女の子がいない")
            missing = [g["id"] for g in girls if today not in week.get(g["id"], {})]
            if missing:
                raise RuntimeError(f"スケジュールで今日の出勤が読めない子がいる（{len(missing)}/{len(girls)}人）")
            absent_ids = match_absent(girls, absent, shopdir)
            order, counts = decide(girls, week, dates, boxes, absent_ids, now)
            cur = [g["id"] for g in girls]
            if sorted(order) != sorted(cur):
                raise RuntimeError("案の人数が今の並びと合わない")
            n_moved, first = moved(cur, order)
            matched, unmatched = box_counts(girls, boxes, week, today)
            rec.update(girls=len(girls), schedule=len(week), boxes=len(boxes), matched_boxes=matched, unmatched_boxes=unmatched,
                       absent=len(absent), absent_matched=len(absent_ids), counts=counts, moved=n_moved, first_diff=first, order_fp=fp(order))
            print(f"  {shopdir}: 女の子{len(girls)}人・スケジュール{len(week)}人・箱と合った{matched}/{len(boxes)}"
                  + (f"（合わない箱 {unmatched}）" if unmatched else "") + f"・当欠と合った{len(absent_ids)}/{len(absent)} → {counts}")
            do_save, do_send, why = need_send(order, cur, rec)
            print(f"  {shopdir}: 今の並びから動く子{n_moved}人" + (f"（最初に違うのは{first}位）" if first else "") + f"／{why}")
            if not do_send:
                return 0
            trouble = inputs_trouble(boxes, box_err, absent_err, girls, week, now, matched)
            if trouble:
                print(f"  {shopdir}: {trouble}ので、今回は送らない（並びが行ったり来たりしないように）")
                rec["skipped"] = trouble
                return 0
            if mode != "send":
                print(f"  {shopdir}: 案を作るだけ（mode が send でない）")
                rec["skipped"] = "案を作るだけ"
                return 0
            if rows is None:
                print(f"  {shopdir}: 新着んの更新結果が読めない（{rows_err}）ので、今回は送らない")
                rec["skipped"] = "更新結果が読めない"
                return 0
            if rows and minutes_since(now, rows[0][0], rows[0][1]) < MIN_GAP_SEC // 60:
                print(f"  {shopdir}: 新着んで{minutes_since(now, rows[0][0], rows[0][1])}分前に並び替えを送っている（人が送った分も含む）ので、今回は送らない")
                rec["skipped"] = "新着んで10分以内に送っている"
                return 0
            busy = is_sorting(page)
            if busy is not False:
                print(f"  {shopdir}: 新着んが送っている途中か分からない（{busy}）ので、今回は送らない")
                rec["skipped"] = "新着んが送っている途中"
                return 0
            f = fp(order)
            if do_save:
                rec.update(saved_at=datetime.now(JST).isoformat(timespec="seconds"), saved_fp=f)
                persist()                        # 途中で打ち切られても、保存したことは残す
                mark_changed()
                if not save_order(page, order):
                    raise RuntimeError("保存した並びが案と違う（送らない）")
            tries = rec.get("tries") or {}
            n_try = (int(tries.get("n") or 0) if tries.get("fp") == f else 0) + 1
            sent_at = datetime.now(JST)
            rec.update(sent_at=sent_at.isoformat(timespec="seconds"), sent_fp=f, sent_moved=n_moved, tries={"fp": f, "n": n_try},
                       done=False, result={}, result_error="", sites=0)
            persist()                            # 送り始めた時刻を先に残す（打ち切られても10分あける）
            mark_changed()
            n_sites = send_sites(page, len(girls))
            rec["sites"] = n_sites
            persist()
            print(f"  {shopdir}: " + ("並び順を保存して、" if do_save else "保存済みの並びを、") + f"{n_sites}サイトへ送った（この並びの{n_try}回目）")
            seen_busy = False
            t0 = time.time()
            while time.time() - t0 < SEND_WAIT_SEC:
                time.sleep(5)
                s_ = is_sorting(page)
                if s_ is True:
                    seen_busy = True
                elif s_ is False and (seen_busy or time.time() - t0 >= 20):
                    break
            if is_sorting(page) is False:
                res = read_results(page, sent_at)
                ok, why = result_ok(res, n_sites)
                if ("_" in res and not seen_busy) or why.startswith("成功が1つも"):
                    # 結果の行がまだ無い／詳しい結果にまだ数字が入っていない → 次の回で読み直す（送り直しはしない）
                    print(f"  {shopdir}: まだ結果が出ていない（{why}。次の回で確かめる）")
                else:
                    rec.update(done=True, result=res, result_error=why)
                    if ok:
                        rec["ok_fp"] = f
                    print(f"  {shopdir}: 送り終わった。結果 {scrub(res)}" + ("" if ok else f" ⚠️{why}"))
            else:
                print(f"  {shopdir}: まだ送っている途中（次の回で結果を確かめる）")
            return 0
        finally:
            b.close()


REC_DIR = Path(os.environ.get("RUNNER_TEMP") or "/tmp")
CHILD_TIMEOUT = 300          # 1店の上限（秒）
PARALLEL = 4                 # 同時に開く店の数
STAGGER_SEC = 3              # 店ごとの開始をずらす（ヘブン・新着んに一度に押しかけない）


def rec_path(shopdir):
    return REC_DIR / f"si9_rec_{shopdir}.json"


def prefetch_cti(now):
    """親が1回だけCTIを読んで控えを残す（子が店ごとにCTIへログインしないように）。即ヒメ直しの控えが新しければ何もしない"""
    try:
        c = json.loads(CTI_CACHE.read_text(encoding="utf-8"))
        if (now - datetime.fromisoformat(c["at"])).total_seconds() <= CTI_CACHE_MAX_SEC:
            return "即ヒメ直しの読んだもの"
    except Exception:
        pass
    if not os.environ.get("CTI_PASSWORD"):
        return "CTIの鍵なし"
    import cti_schedule
    rows, _ = cti_schedule.read_today(now)
    CTI_CACHE.write_text(json.dumps({"at": now.isoformat(), "people": [{"shop": p["shop"], "name": p["name"], "absent": bool(p.get("absent"))} for p in rows]},
                                    ensure_ascii=False), encoding="utf-8")
    return "CTIを読んだ"


def child_main(shopdir, mode, now):
    """1店だけ動かす（親から呼ばれる）。記録は自分の店のファイルに書く"""
    st = load_status()
    rec = dict(st.get("shops", {}).get(shopdir, {}))
    def persist():
        rec_path(shopdir).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    try:
        rc = run_shop(shopdir, now, mode, rec, persist)
    except Exception as e:
        rec["error"] = safe_err(e)
        print(f"  {shopdir}: 止めた {rec['error']}")
        rc = 1
    persist()
    return rc


def merge_recs(st, shops):
    changed = False
    for sd in shops:
        try:
            rec = json.loads(rec_path(sd).read_text(encoding="utf-8"))
        except Exception:
            continue
        if st.setdefault("shops", {}).get(sd) != rec:
            st["shops"][sd] = rec
            changed = True
    return changed


def run_parallel(shops, mode, now):
    """店ごとに別の実行を同時に（最大 PARALLEL）。子の出力は終わった順にまとめて出す。記録は出てきた順にまとめて書く"""
    import subprocess
    for sd in shops:
        try:
            rec_path(sd).unlink()
        except Exception:
            pass
    st = load_status()
    pending = list(shops)
    running = {}       # shopdir → (Popen, 開始時刻)
    rc = 0
    last_start = 0.0
    t_begin = time.time()
    while pending or running:
        while pending and len(running) < PARALLEL and time.time() - last_start >= (STAGGER_SEC if running else 0):
            sd = pending.pop(0)
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--shop", sd, "--mode", mode, "--now", now.isoformat()],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            running[sd] = (p, time.time())
            last_start = time.time()
        time.sleep(1.0)
        for sd, (p, t0) in list(running.items()):
            if p.poll() is None and time.time() - t0 > CHILD_TIMEOUT:
                p.kill()
                print(f"  {sd}: {CHILD_TIMEOUT}秒で打ち切った")
            if p.poll() is not None:
                out = p.stdout.read() if p.stdout else ""
                print(out.rstrip() or f"  {sd}: （出力なし）")
                rc |= (p.returncode or 0) != 0
                del running[sd]
        if merge_recs(st, shops):
            st["at"] = now.isoformat(timespec="seconds")
            st["mode"] = mode
            save_status(st)
    merge_recs(st, shops)
    st["at"] = now.isoformat(timespec="seconds")
    st["mode"] = mode
    save_status(st)
    print(f"新着んの並び替え: {len(shops)}店 {int(time.time() - t_begin)}秒")
    return 1 if rc else 0


def main():
    args = sys.argv[1:]
    if "--shop" in args:
        shopdir = args[args.index("--shop") + 1]
        mode = args[args.index("--mode") + 1] if "--mode" in args else "dry"
        now = datetime.fromisoformat(args[args.index("--now") + 1]) if "--now" in args else datetime.now(JST)
        return child_main(shopdir, mode, now)
    now = datetime.now(JST)
    try:
        shops, mode = live_config()
    except Exception as e:
        print(f"新着んの並び替え: 切り替えの設定が読めないので何もしない（{safe_err(e, 80)}）")
        return 0
    if "--dry" in args:
        mode = "dry"
    if not shops or mode not in ("send", "dry"):
        print(f"新着んの並び替え: 止めてある（店 {shops}・mode {mode or 'なし'}）")
        return 0
    if not in_window(now):
        print(f"新着んの並び替え: 時間外（{now.strftime('%H:%M')}。動くのは 8:00〜翌2:00）")
        return 0
    print(f"新着んの並び替え（{'送る' if mode == 'send' else '案を作るだけ'}・{len(shops)}店を同時に）")
    try:
        print(f"  CTIの当欠: {prefetch_cti(now)}")
    except Exception as e:
        print(f"  CTIの当欠が読めない（各店は送らない）: {safe_err(e, 80)}")
    return run_parallel(shops, mode, now)


if __name__ == "__main__":
    raise SystemExit(main())
