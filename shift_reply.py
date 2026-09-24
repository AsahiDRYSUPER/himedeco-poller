"""出勤の返事を読む（判定だけ。ネットには出ない）。

「26日 12-20時」のように、日にちと始まり〜終わりの時間が揃っていて、
条件や迷いの言葉が無いものだけを「そのまま上げてよい（clear）」と判定する。
それ以外は「人に見せる（unclear）」。出勤の話に見えないものは none。

決まりの元: メイン作業場/projects/ヘブン運用・自動化/出勤返事の読み方.md
（一希さんと決めたパターン集。直されたらここの言葉のリストや判定も直す）
"""
import re
from datetime import date, datetime, timedelta

MAX_AHEAD_DAYS = 40      # これより先の日は、見間違いの可能性が高いので人に見せる
MAX_LEN = 160            # 長い文は事情が混ざっているので人に見せる
MIN_HOURS, MAX_HOURS = 1, 17

# これらの言葉が入っていたら、上げずに人に見せる
NEG = ("出れない", "出られない", "出勤できない", "出勤できません", "行けない", "いけない", "無理", "むずかしい", "難しい",
       "できない", "出来ない", "休ませ", "お休み", "休み", "休む", "欠勤", "キャンセル", "取り消", "消して", "削除",
       "なしで", "無しで")
HEDGE = ("かも", "たぶん", "多分", "できれば", "出来れば", "もしかし", "未定", "わからない", "分からない", "分かりません",
         "わかりません", "迷って", "悩んで", "考え", "検討", "くらい", "ぐらい", "頃", "ごろ", "以降", "前後",
         "ラスト", "らすと", "最後まで", "閉店", "午前", "午後", "あれば", "なければ", "もし", "場合", "相談",
         "どちら", "どっち", "いずれ", "または", "もしくは", "体調", "様子", "調整", "決まったら",
         "後で", "あとで", "また連絡", "ですか", "ますか", "でしょうか", "かな", "？", "?")
HEDGE_RE = (re.compile(r"日か(?!ら)"), re.compile(r"時か(?!ら)"))   # 「26日か27日」「15時か18時」

_Z2H = str.maketrans("０１２３４５６７８９：／", "0123456789:/")
# 「12-20時」「12ー20」のような区切りは、数字にはさまれた時だけ「〜」にそろえる
_DASH = re.compile(r"(?<=[\d時分半])\s*[ー−－—–\-~～]\s*(?=\d)")

_TIME = r"(?<!\d)(\d{1,2})(?:\s*時(?:\s*(\d{1,2})\s*分?|(半))?|:(\d{2}))?(?!\d)"
RANGE = re.compile(_TIME + r"\s*(?:〜|から|より)\s*" + _TIME + r"\s*(?:まで|迄)?(?!\s*日)")
MD = re.compile(r"(?<!\d)(\d{1,2})\s*(?:月|/)\s*(\d{1,2})\s*日?(?!\d)")
DAYS = re.compile(r"(?<!\d)((?:\d{1,2}\s*[、,.・と]\s*)*\d{1,2})\s*日(?![間目前後])")
REL = re.compile(r"今日|本日|明日|あした|明後日|あさって")
_REL_N = {"今日": 0, "本日": 0, "明日": 1, "あした": 1, "明後日": 2, "あさって": 2}
DAY_RANGE = re.compile(r"\d\s*日?\s*[〜]\s*\d{1,2}\s*日")   # 「26〜28日」「26日〜28日」


def _norm(t):
    t = (t or "").translate(_Z2H).replace("　", " ")
    return _DASH.sub("〜", t)


def _tokens(t):
    """本文から、日にち・相対日・時間の範囲を、出てくる順に拾う。重なりは先に出た方を取る。"""
    found = []
    for m in MD.finditer(t):
        found.append((m.start(), m.end(), 0, "md", (int(m.group(1)), int(m.group(2)))))
    for m in DAYS.finditer(t):
        days = [int(x) for x in re.findall(r"\d{1,2}", m.group(1))]
        found.append((m.start(), m.end(), 1, "days", days))
    for m in REL.finditer(t):
        found.append((m.start(), m.end(), 2, "rel", _REL_N[m.group(0)]))
    for m in RANGE.finditer(t):
        found.append((m.start(), m.end(), 3, "range", m.groups()))
    found.sort(key=lambda x: (x[0], x[2]))
    out, last_end = [], -1
    for s, e, _, kind, data in found:
        if s < last_end:
            continue
        out.append((kind, data))
        last_end = e
    return out


def _hm(h, m, half, mm):
    h = int(h)
    if half:
        mi = 30
    elif m is not None:
        mi = int(m)
    elif mm is not None:
        mi = int(mm)
    else:
        mi = 0
    return h, mi


def _resolve_day(day, today):
    """「26日」→ 今日以降でいちばん近い26日。"""
    if not 1 <= day <= 31:
        return None
    for add_month in (0, 1, 2):
        y, mo = today.year, today.month + add_month
        if mo > 12:
            y, mo = y + 1, mo - 12
        try:
            d = date(y, mo, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def _resolve_md(month, day, today):
    for y in (today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def _fmt(h, mi):
    return f"{h:02d}:{mi:02d}"


def read_shift_reply(body, now=None):
    """返す: {"status": "clear"|"unclear"|"none", "shifts": [(date, "HH:MM", "HH:MM")], "reason": str, "hint": str}
    shifts の時間は本人の書いたまま（24:30 など）。画面に入れる形（0030）は shift_auto.hhmm で。"""
    now = now or datetime.now()
    today = now.date()
    t = _norm(body)
    toks = _tokens(t)
    if not toks:
        return {"status": "none", "shifts": [], "reason": "", "hint": ""}

    # まず、人に見せるべき合図
    hint_parts, pending, shifts = [], [], []
    for kind, data in toks:
        if kind == "range":
            h1, m1 = _hm(data[0], data[1], data[2], data[3])
            h2, m2 = _hm(data[4], data[5], data[6], data[7])
            label = f"{_fmt(h1, m1)}〜{_fmt(h2, m2)}"
            last = pending[-1] if pending else None
            head = f"{last.day}日 " if isinstance(last, date) else (f"{last} " if last else "")
            hint_parts.append(head + label)
            for d in pending:
                shifts.append((d, (h1, m1), (h2, m2)))
            if not pending:
                shifts.append((None, (h1, m1), (h2, m2)))
            pending = []
        elif kind == "md":
            pending.append(_resolve_md(data[0], data[1], today) or f"{data[0]}/{data[1]}")
        elif kind == "days":
            pending.extend(_resolve_day(x, today) or x for x in data)
        elif kind == "rel":
            pending.append(today + timedelta(days=data))
    hint = " / ".join(hint_parts) if hint_parts else ("日にちはあるが時間が読めない" if pending else "")

    def unclear(reason):
        return {"status": "unclear", "shifts": [], "reason": reason, "hint": hint}

    if len(t) > MAX_LEN:
        return unclear("文が長い")
    if DAY_RANGE.search(t):
        return unclear("日にちが範囲で書かれている")
    for w in NEG:
        if w in t:
            return unclear(f"「{w}」がある")
    for w in HEDGE:
        if w in t:
            return unclear(f"「{w}」がある")
    for rx in HEDGE_RE:
        if rx.search(t):
            return unclear("日にちか時間を選ばせている")
    if pending:
        return unclear("日にちはあるが時間が無い")
    if not shifts:
        return unclear("時間が読めない")

    out, seen_days = [], set()
    for d, (h1, m1), (h2, m2) in shifts:
        if d is None:
            return unclear("時間はあるが日にちが無い")
        if not isinstance(d, date):
            return unclear(f"日にち「{d}」が読めない")
        if d in seen_days:
            return unclear("同じ日に時間が2つある")
        seen_days.add(d)
        if (d - today).days > MAX_AHEAD_DAYS:
            return unclear("先すぎる日にち")
        if m1 not in (0, 30) or m2 not in (0, 30):
            return unclear("分が00か30でない")
        if h1 > 23 or h2 > 29:
            return unclear("時間が読めない")
        end = h2 + 24 if (h2 <= h1 and h2 <= 9) else h2
        if not MIN_HOURS <= (end - h1) + (m2 - m1) / 60 <= MAX_HOURS:
            return unclear("時間の長さが変")
        if d == today and h1 <= now.hour:
            return unclear("今日の、もう始まっている時間")
        out.append((d, _fmt(h1, m1), _fmt(end, m2) if end != h2 else _fmt(h2, m2)))
    out.sort()
    return {"status": "clear", "shifts": out, "reason": "",
            "hint": " / ".join(f"{d.day}日 {s}〜{e}" for d, s, e in out)}


def hhmm(t):
    """「24:30」→「0030」。ヘブンの選択肢は 0時をまたぐと 0000〜0559 に戻る。"""
    h, m = str(t).split(":")
    h = int(h)
    if h >= 24:
        h -= 24
    return f"{h:02d}{m}"
