"""夜遊びヘブン（YOASOBI HEAVEN）の公開ページから、載っている女の子の一覧と出勤を読み、AIに渡す短い文にする（2026-11-11）。

- 読むのはログイン不要の公開ページだけ（女の子の一覧・出勤表）。サイトの確認画面が出た時（中身が空）は読めなかったとして扱う。
- 公開リポジトリなので、ここには店の決まりを書かない。読んだ中身はログに出さない（数だけ）。
- 読めなかった時は空を返す（AIは「一覧が無い」として受付に回す）。
"""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

JST = timezone(timedelta(hours=9))
BASE = "https://yoasobi-heaven.com/en/okayama/YA3301/{d}/"
DAYS_AHEAD = 2                      # 今日＋何日先の出勤まで読むか（1店で一覧1＋出勤3＝4ページ。読みすぎるとサイトの確認画面が出る）
_CARD = re.compile(r'girlid-(\d+)/">\s*<p class="name year">\s*([^<(]+?)\s*\((\d+)Age\)\s*</p>\s*</a>\s*<p class="size">\s*([^<]*?)\s*</p>')
_CACHE = {}


def _get(url):
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    if r.status_code != 200 or len(r.text) < 2000:      # 確認画面（202・空）は「読めなかった」。出勤ゼロと取り違えない
        raise RuntimeError(f"status {r.status_code}")
    return r.text


def parse_list(t):
    """女の子の一覧のページ → {girlid: {name, age, size, price}}"""
    out = {}
    for m in _CARD.finditer(t):
        gid, name, age, size = m.groups()
        pr = re.search(r'girl-price">\s*([\d,]+)', t[m.end(): m.end() + 1500])
        out.setdefault(gid, {"name": name.strip(), "age": age, "size": re.sub(r"\s+", " ", size).strip(),
                             "price": pr.group(1) if pr else ""})
    return out


def parse_attend(t):
    """出勤表のページ → [(girlid, "14:30-23:00")]（並びはページのまま）"""
    out, seen = [], set()
    for chunk in t.split('<li class="box sp_mode btn_girls">')[1:]:
        g = re.search(r"girlid-(\d+)/", chunk)
        if not g or g.group(1) in seen:
            continue
        seen.add(g.group(1))
        tm = re.search(r"(\d{1,2}:\d{2})(?:&nbsp;|\s)*-(?:&nbsp;|\s)*(\d{1,2}:\d{2})", chunk)
        out.append((g.group(1), f"{tm.group(1)}-{tm.group(2)}" if tm else "時間未定"))
    return out


def girl_url(d, gid):
    return BASE.format(d=d) + f"girlid-{gid}/"


def attend_url(d):
    return BASE.format(d=d) + "attend/"


def map_url(name):
    q = name if "岡山" in name else f"{name} 岡山"
    return "https://www.google.com/maps/search/?api=1&query=" + quote(q, safe="")


def roster_text(d, now=None):
    """店（夜遊びのディレクトリ名）の一覧と出勤を、AIに渡す文にする。読めなければ空。1回の実行で1店1回だけ読む。"""
    if not d:
        return ""
    if d in _CACHE:
        return _CACHE[d]
    now = now or datetime.now(JST)
    text = ""
    try:
        girls = parse_list(_get(BASE.format(d=d) + "girls-list/"))
        if girls:
            lines = ["（YOASOBI HEAVEN に載っている女の子。案内してよいのはこの子たちだけ。料金は60分の最低料金＝コース料金＋その子の指名料。［女の子:番号］はその子のページ）"]
            for g, v in girls.items():
                lines.append(f"- {v['name']}（{v['age']}歳・{v['size']}・60分{v['price']}円〜）［女の子:{g}］")
            # 出勤。日付なしのページ＝サイトの「今日」。その日付は、ページにある先の日のリンクの一番早い日の前日で決める
            # （深夜はサイトの今日と暦の今日がずれることがあるため）
            lines.append("（出勤表。終わりの時刻が始まりより早い時は、翌日の朝まで）")
            try:
                t0 = _get(attend_url(d))
            except Exception:
                t0 = None
            later = sorted(set(re.findall(r"attend/date-(\d{8})/", t0 or "")))
            site_today = (datetime.strptime(later[0], "%Y%m%d").date() - timedelta(days=1)) if later else now.date()
            for i in range(DAYS_AHEAD + 1):
                day = site_today + timedelta(days=i)
                try:
                    t = t0 if i == 0 else _get(attend_url(d) + f"date-{day.strftime('%Y%m%d')}/")
                    rows = parse_attend(t) if t is not None else None
                except Exception:
                    rows = None
                head = f"{day.month}/{day.day}（{'月火水木金土日'[day.weekday()]}{'・今日' if i == 0 else '・明日' if i == 1 else ''}）の出勤"
                if rows is None:
                    lines.append(f"{head}：読めなかった")
                elif not rows:
                    lines.append(f"{head}：まだ出ていない")
                else:
                    lines.append(f"{head}：" + "、".join(f"{girls.get(g, {}).get('name', '番号' + g)} {tm}" for g, tm in rows))
            text = "\n".join(lines)
    except Exception:
        text = ""
    _CACHE[d] = text
    return text


def open_slots(work, busy, now_min, lead, need=60, step=10):
    """出勤 work=(始め, 終わり)・予約 busy=[(始め, 終わり)]（いずれも、その日の0時からの分）から、
    今から入れる時間を [(始め, 終わり)] で返す。計算できない時は None。
    lead＝女の子が着くまでにみる分（出勤の始め・今・前の予約の終わりから）。次の予約の前は lead の3分の1をあける。
    始めは step 分単位に切り上げる。need 分に満たない隙間は返さない。"""
    if not work or lead is None or now_min is None:
        return None
    tail = lead // 3
    def up(m):
        return -(-m // step) * step
    out = []
    cur = up(max(work[0], now_min) + lead)
    for a, b in sorted((int(x), int(y)) for x, y in (busy or [])):
        if b + lead <= cur:
            continue
        if (a - tail) - cur >= need:
            out.append((cur, a - tail))
        cur = max(cur, up(b + lead))
    if work[1] - cur >= need:
        out.append((cur, work[1]))
    return out


def course_cap(mins):
    """隙間の長さ（分）に入るいちばん長いコース"""
    return "120分も入る" if mins >= 120 else "90分まで" if mins >= 90 else "60分まで"


LINK = re.compile(r"[\[［](予約リンク|出勤表|女の子[:：]\s*(\d+)|地図[:：]\s*([^\]］]+))[\]］]")   # 全角のかっこを写しても置き換える


def fill_links(reply, d, reserve):
    """AIの文の置き換え記号をURLにする。[予約リンク] [出勤表] [女の子:番号] [地図:ホテル名]。分からない記号は消す"""
    src = reply or ""

    def url_of(m):
        whole, gid, place = m.group(1), m.group(2), m.group(3)
        if whole == "予約リンク":
            return reserve
        if whole == "出勤表":
            return attend_url(d) if d else ""
        if gid:
            return girl_url(d, gid) if d else ""
        if place:
            return map_url(place.strip())
        return ""

    def rep(m):
        u = url_of(m)
        # URLのすぐ後ろに文字が続くと、リンクがその文字まで伸びる（2026-11-12）→ 間をあける
        if u and m.end() < len(src) and not src[m.end()].isspace():
            u += " "
        return u
    out = LINK.sub(rep, src)
    out = re.sub(r"[\[［](予約リンク|出勤表|女の子|地図)[^\]］]*[\]］]", "", out)
    return re.sub(r"[ \t]+\n", "\n", out).strip()


def drop_reserve(reply):
    """ネット予約の無い店の文から、[予約リンク] を含む文をまるごと外す（2026-11-12 開いても予約できない店がある）"""
    parts = re.split(r"(?<=[。！？!?\n])", reply or "")
    keep = [p for p in parts if not re.search(r"[\[［]予約リンク[\]］]", p)]
    return re.sub(r"\n{3,}", "\n\n", "".join(keep)).strip()
