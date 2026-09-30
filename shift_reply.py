"""出勤の返事を読む（判定だけ。ネットには出ない）。

返事を「日にちのまとまり」に分けて、まとまりごとに判断する（2026-09-27 大塚めぐみさんの件で作り直し）:
    clear      … 日にち＋始まり〜終わりが揃っていて、迷いの言葉が無い日 → そのまま上げてよい
    tentative  … 「未定」「かも」「残業かも」など迷いの言葉が付いた日 → 上げずに、備考に「オキニトークでご確認ください。」
    declined   … 「休み」「無理」などが付いた日 → 何もしない
    足りない日  … 日にちだけで時間が無い（迷いの言葉も無い）→ 人に見せる
                 ただし「3,4日出勤予定でお願いします」のように出勤の言葉が付いていれば「出勤予定」＝ tentative
                 （2026-09-28 街角 めとさんの件。一希さん「出勤予定のシフトでアップして、ちゃんと返して」）
返す status:
    clear     … 全部の日が clear
    partial   … clear の日があり、ほかに tentative / declined の日もある（clear の日だけ上げる）
    tentative … 時間の付いた日が無く、出勤予定（日にちだけ・未定）の日だけ → 備考「オキニトークでご確認ください。」を入れて返す
    unclear   … 上げてよい日が無い、質問・「変更」がある、読めない所がある → 人に見せる
    none      … 出勤の話に見えない

決まりの元: メイン作業場/projects/ヘブン運用・自動化/出勤返事の読み方.md（一希さんと決めたパターン集。直されたらここと test_*.py も直す）
"""
import re
from datetime import date, datetime, timedelta

MAX_AHEAD_DAYS = 40      # これより先の日は、見間違いの可能性が高いので人に見せる
MAX_LEN = 300            # 長すぎる文は事情が混ざっているので人に見せる
MIN_HOURS, MAX_HOURS = 1, 17
NOTE_TENTATIVE = "オキニトークでご確認ください。"   # 未定の日の備考（一希さん 2026-09-27）
# 「ラスト」は全店共通：月〜土は27時（翌3時）、日曜だけ25時（翌1時）（一希さん 2026-09-27）
LAST_WEEKDAY, LAST_SUNDAY = 27, 25
_LAST_MARK = 99          # 読み分けの途中で「ラスト」を表す印（日にちが決まってから曜日で時間に直す）

# その日を「休み・出ない」にする言葉
NEG = ("出れない", "出られない", "出勤できない", "出勤できません", "行けない", "いけない", "無理", "むずかしい", "難しい",
       "できない", "出来ない", "休ませ", "お休み", "休み", "休む", "欠勤", "キャンセル", "取り消", "消して", "削除",
       "なしで", "無しで", "出れません", "出られません", "でれません", "でられません", "でれない", "でられない",
       "行けません", "いけません", "厳しい", "きびしい", "できなく", "出来なく", "行けなく", "いけなく", "出れなく",
       "出られなく", "でれなく", "でられなく")
# その日だけ「未定」にする言葉（ほかの日は上げる）
HEDGE = ("かも", "たぶん", "多分", "できれば", "出来れば", "もしかし", "未定", "わからない", "分からない", "分かりません",
         "わかりません", "迷って", "悩んで", "考え", "検討", "くらい", "ぐらい", "頃", "ごろ", "以降", "前後",
         "ラスト", "らすと", "最後まで", "閉店", "午前", "午後", "あれば", "なければ", "もし", "場合", "相談",
         "どちら", "どっち", "いずれ", "または", "もしくは", "体調", "様子", "調整", "決まったら",
         "後で", "あとで", "また連絡", "残業", "かな", "わかんな", "分かんな", "そうだったら", "そうなら",
         "出れたら", "出られたら", "行けたら")
# 日にちだけ（時間なし）でも、この言葉があれば「出勤予定」として扱う（2026-09-28）
ATTEND = ("出勤", "出れ", "出られ", "入れ", "入り", "行け", "行き", "予定", "お願い")
PAST = ("ました", "でした", "昨日", "先日", "この前", "前回")
CHOICE = ("どちら", "どっち", "いずれ", "または", "もしくは")
CHOICE_RE = (re.compile(r"日か(?![らもな])"), re.compile(r"時か(?![らもな])"))   # 「26日か27日」（「18時かも」「かな」は選ばせていない）   # 「3日の出勤ありがとうございました」のような過ぎた日の話は拾わない
_ANY_TIME = re.compile(r"\d{1,2}\s*時|\d{1,2}:\d{2}")


def _has_time_left(txt):
    """日にちの書き方を取り除いたあとに数字が残っていれば、時間らしきものが書いてある（「26日 12〜ラスト」）。"""
    for rx in (MD, DAYSPAN, DAYS, WEEKDAY):
        txt = rx.sub(" ", txt)
    return bool(re.search(r"\d", txt))
HEDGE_RE = (re.compile(r"日か(?!ら)"), re.compile(r"時か(?!ら)"))   # 「26日か27日」「15時か18時」
# 文全体を人に回す言葉（質問・前の返事の変更）
GLOBAL = ("？", "?", "ですか", "ますか", "でしょうか", "変更", "変えて", "ずらし", "代わり", "かわり", "やっぱり")

_Z2H = str.maketrans("０１２３４５６７８９：／（）", "0123456789:/()")
# 「12-20時」「12ー20」のような区切りは、数字にはさまれた時だけ「〜」にそろえる
_DASH = re.compile(r"(?<=[\d時分半])\s*[ー−－—–\-~～]\s*(?=\d)")
_TIME = r"(?<!\d)(\d{1,2})(?:\s*時(?:\s*(\d{1,2})\s*分?|(半))?|:(\d{2}))?(?!\d)"
RANGE = re.compile(_TIME + r"\s*(?:〜|から|より)\s*" + _TIME + r"\s*(?:まで|迄)?(?!\s*日)")
LAST_WORDS = re.compile(r"(\d{1,2}\s*時(?:\s*\d{1,2}\s*分|半)?|\d{1,2}:\d{2})\s*(?:〜|から|より)\s*(?:ラスト|らすと|最後まで|閉店まで)(?:まで)?")
MD = re.compile(r"(?<!\d)(\d{1,2})\s*(?:月|/)\s*(\d{1,2})\s*日?(?!\d)")
DAYSPAN = re.compile(r"(?<!\d)(\d{1,2})\s*日?\s*〜\s*(\d{1,2})\s*日")                      # 「9〜13日」「26日〜28日」
DAYS = re.compile(r"(?<!\d)((?:\d{1,2}\s*[、,.・と]\s*)*\d{1,2})\s*日(?![間目前後])")
WEEKDAY = re.compile(r"(?<!\d)(\d{1,2})\s*\(\s*[月火水木金土日祝]\s*\)")                  # 「28(月)」
BARE_LIST = re.compile(r"(?<![\d:])(\d{1,2}(?:\s*[、,・]\s*\d{1,2})+)(?=\s+\d{1,2}\s*(?:時|:))")   # 「29、30、2 10時から15時」
REL = re.compile(r"今日|本日|明日|あした|明後日|あさって")
_REL_N = {"今日": 0, "本日": 0, "明日": 1, "あした": 1, "明後日": 2, "あさって": 2}
_CLAUSE = re.compile(r"\s+/\s+|\n+|[。！!]+")          # 「 / 」は見張りが連絡をつなぐ印（9/26 の / は切らない）


def _norm(t, shop=None):
    t = (t or "").translate(_Z2H).replace("　", " ")
    t = _DASH.sub("〜", t)
    # 「19時30分~lastまで」「18時〜LAST」も「ラスト」として読む（2026-09-29 街角 りくさんの件）
    t = re.sub(r"last", "ラスト", t, flags=re.IGNORECASE)
    t = re.sub(r"[~～]\s*(?=ラスト|らすと|最後|閉店)", "〜", t)
    # 「16時〜0時 10月2日」の「0時 10」を「0時10分」と読まないよう、時のあとに空白＋数字（分が付かない）が来たら区切る
    t = re.sub(r"時\s+(?=\d{1,2}(?!\d)(?!\s*分))", "時、", t)
    t = LAST_WORDS.sub(lambda mm: f"{mm.group(1)}〜{_LAST_MARK}時", t)
    return t


def _tokens(t):
    """節の中から、日にち・時間の範囲を、出てくる順に拾う。重なりは先に出た方を取る。"""
    found = []
    for m in MD.finditer(t):
        found.append((m.start(), m.end(), 0, "md", (int(m.group(1)), int(m.group(2)))))
    for m in DAYSPAN.finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a < b <= 31 and b - a <= 14:
            found.append((m.start(), m.end(), 0, "days", list(range(a, b + 1))))
    for m in DAYS.finditer(t):
        found.append((m.start(), m.end(), 1, "days", [int(x) for x in re.findall(r"\d{1,2}", m.group(1))]))
    for m in WEEKDAY.finditer(t):
        found.append((m.start(), m.end(), 1, "days", [int(m.group(1))]))
    for m in BARE_LIST.finditer(t):
        found.append((m.start(), m.end(), 1, "days", [int(x) for x in re.findall(r"\d{1,2}", m.group(1))]))
    for m in REL.finditer(t):
        found.append((m.start(), m.end(), 2, "rel", _REL_N[m.group(0)]))
    for m in RANGE.finditer(t):
        found.append((m.start(), m.end(), 3, "range", m.groups()))
    found.sort(key=lambda x: (x[0], x[2]))
    out, last_end = [], -1
    for s, e, _, kind, data in found:
        if s < last_end:
            continue
        out.append((s, kind, data))
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


def _has(words, text):
    return any(w in text for w in words)


def read_shift_reply(body, now=None, shop=None):
    """返す: {"status", "shifts": [(date, "HH:MM", "HH:MM")], "tentative": [date], "declined": [date],
             "display": ["28日 19:00〜23:00", …], "reason", "hint"}
    shifts の時間は本人の書いたまま（25:30 など）。画面に入れる形（0130）は hhmm で。"""
    now = now or datetime.now()
    today = now.date()
    raw = body or ""
    t = _norm(raw, shop)

    def result(status, reason="", shifts=(), tentative=(), declined=(), display=(), hint=""):
        return {"status": status, "reason": reason, "shifts": list(shifts), "tentative": sorted(set(tentative)),
                "declined": sorted(set(declined)), "display": list(display), "hint": hint}

    clauses = [c for c in _CLAUSE.split(t) if c and c.strip()]
    groups, last_open = [], None      # group: {"days": [...], "range": None, "text": ""}
    orphan_neg = False
    for c in clauses:
        toks = _tokens(c)
        starts = []
        cg = []
        for s, kind, data in toks:
            if kind in ("md", "days", "rel"):
                if not cg or cg[-1]["range"] is not None:
                    g = {"days": [], "range": None, "start": s}
                    cg.append(g)
                g = cg[-1]
                if kind == "md":
                    g["days"].append(_resolve_md(data[0], data[1], today) or f"{data[0]}/{data[1]}")
                elif kind == "days":
                    g["days"].extend(_resolve_day(x, today) or x for x in data)
                else:
                    g["days"].append(today + timedelta(days=data))
            else:   # range
                if cg and cg[-1]["range"] is None:
                    cg[-1]["range"] = data
                elif not cg and last_open is not None and last_open["range"] is None:
                    last_open["range"] = data          # 前の節の日にちに続く時間（「28日」改行「19〜23時」）
                else:
                    return result("unclear", "時間はあるが日にちが無い",
                                  hint=f"{_fmt(*_hm(*data[:4]))}〜{_fmt(*_hm(*data[4:]))}")
        # 節の中の文を、まとまりごとに割り振る（最初のまとまりの前の文は、最初のまとまりに付ける）
        for i, g in enumerate(cg):
            s0 = 0 if i == 0 else g["start"]
            s1 = cg[i + 1]["start"] if i + 1 < len(cg) else len(c)
            g["text"] = c[s0:s1]
        if cg:
            groups.extend(cg)
            last_open = cg[-1]
        else:
            # 日にちの無い節：迷いの言葉は直前のまとまりに付ける。休みの言葉は判断できないので人に回す
            if _has(NEG, c):
                orphan_neg = True
            elif groups and (_has(HEDGE, c) or any(rx.search(c) for rx in HEDGE_RE)):
                groups[-1]["text"] += " " + c

    if not groups:
        # 時間だけ・何も無し
        if any(k == "range" for c in clauses for _, k, _ in _tokens(c)):
            return result("unclear", "時間はあるが日にちが無い")
        return result("none")
    if len(t) > MAX_LEN:
        return result("unclear", "文が長い")
    for w in GLOBAL:
        if w in t:
            return result("unclear", f"「{w}」がある")
    if orphan_neg:
        return result("unclear", "日にちの無い所に「休み」などがある")

    clear, tentative, declined, display, incomplete = [], [], [], [], []
    timed_tentative = False   # 未定の日に読めない時間が書いてある（「4日 最後まで 18時から」）→ 自動の「出勤予定」にはしない
    choice = False            # 「26日か27日」のように選ばせている → 人に見せる
    tdisplay = []             # 未定の日の、読めた時間（返事に書く）
    for g in groups:
        for d in g["days"]:
            if not isinstance(d, date):
                return result("unclear", f"日にち「{d}」が読めない")
            if (d - today).days > MAX_AHEAD_DAYS:
                return result("unclear", "先すぎる日にち")
        txt = g["text"]
        if _has(NEG, txt):
            declined += g["days"]
            continue
        if _has(HEDGE, txt) or any(rx.search(txt) for rx in HEDGE_RE):
            tentative += g["days"]
            if _has(CHOICE, txt) or any(rx.search(txt) for rx in CHOICE_RE):
                choice = True                       # 「26日か27日」→ どちらかを選ぶのは人
            elif g["range"] is not None:
                h1, m1 = _hm(*g["range"][:4])
                h2, m2 = _hm(*g["range"][4:])
                for d in g["days"]:
                    end = "ラスト" if h2 == _LAST_MARK else _fmt(h2 + 24 if (h2 <= h1 and h2 <= 9) else h2, m2)
                    tdisplay.append((d, f"{d.day}日（{_fmt(h1, m1)}〜{end}）"))
            elif _has_time_left(txt):
                timed_tentative = True
            continue
        if g["range"] is None:
            if _has(ATTEND, txt) and not _has(PAST, txt) and not _has_time_left(txt):
                tentative += g["days"]          # 日にちだけの「出勤予定」
            else:
                incomplete += g["days"]
            continue
        h1, m1 = _hm(*g["range"][:4])
        h2, m2 = _hm(*g["range"][4:])
        is_last = (h2 == _LAST_MARK)
        for d in g["days"]:
            if is_last:
                end, em = (LAST_SUNDAY if d.weekday() == 6 else LAST_WEEKDAY), 0
            else:
                if m1 not in (0, 30) or m2 not in (0, 30):
                    return result("unclear", "分が00か30でない")
                if h1 > 23 or h2 > 29:
                    return result("unclear", "時間が読めない")
                end, em = (h2 + 24 if (h2 <= h1 and h2 <= 9) else h2), m2
            if m1 not in (0, 30) or h1 > 23:
                return result("unclear", "時間が読めない")
            if not MIN_HOURS <= (end - h1) + (em - m1) / 60 <= MAX_HOURS:
                return result("unclear", "時間の長さが変")
            if d == today and h1 <= now.hour:
                return result("unclear", "今日の、もう始まっている時間")
            clear.append((d, _fmt(h1, m1), _fmt(end, em)))
            display.append((d, f"{d.day}日 {_fmt(h1, m1)}〜{'ラスト' if is_last else _fmt(end, em)}"))

    days_clear = [d for d, _, _ in clear]
    if len(days_clear) != len(set(days_clear)):
        return result("unclear", "同じ日に時間が2つある")
    tentative = [d for d in tentative if d not in days_clear]
    if incomplete:
        return result("unclear", "日にちはあるが時間が無い", hint="日にちはあるが時間が読めない")
    clear.sort()
    display = [s for _, s in sorted(display)]
    hint = " / ".join(display) + (" / " + "・".join(f"{d.day}日" for d in sorted(set(tentative))) + "は未定" if tentative else "")
    if not clear:
        if tentative and not timed_tentative and not choice:
            shown = dict(tdisplay)
            disp = [shown.get(d, f"{d.day}日") for d in sorted(set(tentative))]
            return result("tentative", "時間の決まった日が無い（出勤予定の日だけ）", tentative=tentative, declined=declined,
                          display=disp, hint="・".join(disp) + "は出勤予定")
        return result("unclear", "上げてよい日が無い（未定・休みだけ）", tentative=tentative, declined=declined, hint=hint)
    status = "partial" if (tentative or declined) else "clear"
    return result(status, shifts=clear, tentative=tentative, declined=declined, display=display, hint=hint)


def hhmm(t):
    """「24:30」→「0030」。ヘブンの選択肢は 0時をまたぐと 0000〜0559 に戻る。"""
    h, m = str(t).split(":")
    h = int(h)
    if h >= 24:
        h -= 24
    return f"{h:02d}{m}"
