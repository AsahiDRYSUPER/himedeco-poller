"""女の子の「出勤申請」（姫デコの「出勤申請」から出した出勤の希望）を読む（読むだけ。承認はしない）。

なぜ：「出勤申請を出したので、その時間の個室をお願いします」のように、時間を書かずに個室を頼まれた時に、
申請の日と時刻で個室を取るため（2026-10-05 一希さん「申請した時間イコール出勤申請。出勤申請で時間の依頼が
来ていると思うので、そこを確認すれば個室は取れる」）。

どこに：管理画面「出勤情報」＞「出勤申請」の、その子の1か月分（C9ShiftManagementMonthly.php?girlsid=）。
日ごとの枠（data-sel="sel_<女の子の番号>_<yyyymmdd>"）に、見出し（承認待ち／確定済）と申請の中身の隠し項目が入っている：
    expected_start_time=1930 / expected_end_time=2300（0000 は24時、0300 は翌3時）
    expected_display_message（「出勤予定」など文字だけの申請）/ expected_absent_flg（1＝休み）
次の月は、画面の「次の月」と同じく form に idou=next を付けて送る（見るだけの切り替え）。
公開リポジトリなので、名前や時刻はログに出さない。
"""
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

import shift_reply

BASE = "https://newmanager.cityheaven.net"
URL_SHOP = {"venus_okayama": "cg_venus_okayama"}     # 出勤の画面は VENUS だけ cg_ が付く
# 「出勤申請した」「申請出しました」「申請した時間で」など。「個室の申請お願いします」（個室を頼むだけ）は入れない
APPLY_RE = re.compile(r"出勤申請|申請(?:し|出|済|中|通り|どおり|の時間|時間|あげ|上げ)")
# 個室を「お願いする」言葉（取り消し・いらない、の話では取らない）
WANT = ("お願い", "おねがい", "取って", "とって", "取れ", "とれ", "押さえ", "おさえ", "抑え", "確保", "希望",
        "使いたい", "使わせ", "使えます", "使えますか", "ほしい", "欲しい", "予約", "したい", "いいですか", "良いですか")
NOT_WANT = ("いらな", "要らな", "不要", "なしで", "無しで", "キャンセル", "取り消", "取消", "消して", "削除", "無効",
            "やめ", "止め", "休み", "休む", "行けな", "いけな", "出れな", "出られな", "無理", "難しい", "むずかしい")
AHEAD_DAYS = 31            # 日にちの指定が無い時に見る範囲（今日から）


def wants_booth(text):
    """個室を頼んでいる文か（「個室」があって、お願いの言葉があり、取り消し・いらないの言葉が無い）。"""
    t = text or ""
    return "個室" in t and any(w in t for w in WANT) and not any(w in t for w in NOT_WANT)


def _min(v):
    v = (v or "").strip()
    if not re.fullmatch(r"\d{4}", v):
        return None
    return int(v[:2]) * 60 + int(v[2:])


def times(start, end):
    """("1930", "2300") → ("19:30", "23:00")。日をまたぐ終わりは 24 以上（"0300" → "27:00"）。読めなければ None。"""
    s, e = _min(start), _min(end)
    if s is None or e is None:
        return None
    if s < 6 * 60:                    # 0時〜6時の始まりは、その営業日の夜中（24:30 など）
        s += 24 * 60
    if e <= s:
        e += 24 * 60
    if not 60 <= e - s <= 17 * 60:
        return None
    return f"{s // 60:02d}:{s % 60:02d}", f"{e // 60:02d}:{e % 60:02d}"


def parse(html, gid):
    """その子の月表示から、申請のある日を返す。[{"day", "start", "end", "state", "text", "absent"}]（start/end は無いこともある）"""
    s = BeautifulSoup(html, "html.parser")
    out = []
    for c in s.select(f'[data-sel^="sel_{gid}_"]'):
        m = re.fullmatch(rf"sel_{gid}_(\d{{8}})", c.get("data-sel") or "")
        if not m:
            continue
        ex = {}
        for i in c.find_all("input"):
            n = i.get("name") or ""
            if n.startswith("expected_"):
                ex[n] = (i.get("value") or "").strip()
        if not any(ex.get(k) for k in ("expected_start_time", "expected_end_time", "expected_display_message")) \
                and ex.get("expected_absent_flg") != "1":
            continue
        head = c.select_one(".expected-head")
        txt = re.sub(r"\s+", " ", c.get_text(" ", strip=True))
        state = "承認待ち" if (head and "承認待ち" in head.get_text()) else "確定済" if "確定済" in txt else ""
        d = datetime.strptime(m.group(1), "%Y%m%d").date()
        t = times(ex.get("expected_start_time"), ex.get("expected_end_time"))
        out.append({"day": d, "start": t[0] if t else None, "end": t[1] if t else None, "state": state,
                    "text": ex.get("expected_display_message", ""), "absent": ex.get("expected_absent_flg") == "1"})
    return out


def read(cli, shopdir, gid, today, until=None):
    """今月（と、until が来月にかかる時は来月）の申請を読む。"""
    us = URL_SHOP.get(shopdir, shopdir)
    url = f"/C9ShiftManagementMonthly.php?shopdir={us}&girlsid={gid}"
    r = cli._get(url)
    apps = parse(r.text, gid)
    if until and (until.year, until.month) != (today.year, today.month):
        s = BeautifulSoup(r.text, "html.parser")
        bd = s.find("input", attrs={"name": "basedate"})
        data = {"idou": "next", "allDisp": "", "fromDisp": "", "girlsid": str(gid),
                "basedate": (bd.get("value") if bd else today.strftime("%Y%m%d")), "page": "", "dummy": ""}
        r2 = cli.s.post(BASE + url, data=data, timeout=30)
        r2.encoding = "utf-8"
        apps += parse(r2.text, gid)
    seen, uniq = set(), []
    for a in sorted(apps, key=lambda a: a["day"]):
        if a["day"] not in seen:
            seen.add(a["day"])
            uniq.append(a)
    return uniq


def named_days(text, now, shop=None):
    """文の中で名指しされた日（「8日」「10/9」「明日」など）。無ければ空。"""
    today = now.date() if isinstance(now, datetime) else now
    t = shift_reply._norm(text or "", shop)
    days = set()
    for _, kind, data in shift_reply._tokens(t):
        if kind == "md":
            d = shift_reply._resolve_md(data[0], data[1], today)
            if d:
                days.add(d)
        elif kind == "days":
            for n in data:
                d = shift_reply._resolve_day(n, today)
                if d:
                    days.add(d)
        elif kind == "rel":
            days.add(today + timedelta(days=data))
    return sorted(days)


def pick(apps, now, days=None):
    """個室を取る日を選ぶ。返す: [(date, "HH:MM", "HH:MM")]
    ・名指しの日があれば、その日の申請だけ（承認待ちでも確定済でも）
    ・無ければ、今日から先の「承認待ち」の申請。承認待ちが無ければ、今日から1週間の確定済の申請
    ・休み・時刻の無い申請・もう終わった時間は取らない"""
    today = now.date()
    now_h = now.hour + now.minute / 60

    def usable(a):
        if a["absent"] or not a["start"] or a["day"] < today:
            return False
        if a["day"] == today:
            h, m = a["end"].split(":")
            return int(h) + int(m) / 60 > now_h
        return True

    ok = [a for a in apps if usable(a)]
    if days:
        want = set(days)
        chosen = [a for a in ok if a["day"] in want]
    else:
        chosen = [a for a in ok if a["state"] == "承認待ち" and a["day"] <= today + timedelta(days=AHEAD_DAYS)]
        if not chosen:
            chosen = [a for a in ok if a["day"] <= today + timedelta(days=7)]
    return [(a["day"], a["start"], a["end"]) for a in chosen]


def display(apps):
    """通知用「8日 19:30〜23:00（承認待ち）」の並び。"""
    out = []
    for a in apps:
        when = f'{a["start"]}〜{a["end"]}' if a["start"] else ("休み" if a["absent"] else (a["text"] or "時刻なし"))
        out.append(f'{a["day"].day}日 {when}' + (f'（{a["state"]}）' if a["state"] else ""))
    return "・".join(out)


def selftest(shopdirs):
    """本物の画面で読めるかを確かめる（読むだけ）。名前・番号・時刻は出さず、数だけ出す。"""
    from heaven_http import HeavenClient, load_credentials
    today = datetime.now().date()
    for shopdir in shopdirs:
        a, p, d = load_credentials(shopdir)
        cli = HeavenClient(a, p, direct=d)
        cli.login_and_select(shopdir)
        us = URL_SHOP.get(shopdir, shopdir)
        s = BeautifulSoup(cli._get(f"/C9ShiftManagement.php?shopdir={us}&list_cnt=ALL").text, "html.parser")
        gids = []
        for h in s.select(".expected-head"):
            c = h.find_parent(attrs={"data-sel": True})
            if c is not None and "承認待ち" in h.get_text() and c["data-sel"].split("_")[1] not in gids:
                gids.append(c["data-sel"].split("_")[1])
        if not gids:
            print(f"{shopdir}: 今週の承認待ちの申請なし（読み取りの確かめは次の店へ）")
            continue
        url = f"/C9ShiftManagementMonthly.php?shopdir={us}&girlsid={gids[0]}"
        r = cli._get(url)
        bd = BeautifulSoup(r.text, "html.parser").find("input", attrs={"name": "basedate"})
        r2 = cli.s.post(BASE + url, data={"idou": "next", "allDisp": "", "fromDisp": "", "girlsid": gids[0],
                                          "basedate": bd.get("value") if bd else "", "page": "", "dummy": ""}, timeout=30)
        r2.encoding = "utf-8"

        def months(html):
            ds = {x["data-sel"].rsplit("_", 1)[1][:6] for x in BeautifulSoup(html, "html.parser").select("[data-sel]")
                  if x.get("data-sel", "").count("_") >= 2}
            return sorted(ds)

        apps = read(cli, shopdir, gids[0], today, today + timedelta(days=AHEAD_DAYS))
        print(f"{shopdir}: 承認待ちのある子 {len(gids)}人。1人目の月表示 {months(r.text)} → 次の月 {months(r2.text)}。"
              f"申請 {len(apps)}日分（承認待ち {sum(1 for x in apps if x['state'] == '承認待ち')}・時刻あり {sum(1 for x in apps if x['start'])}）")
        return True
    return False


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        shops = [a for a in sys.argv[1:] if not a.startswith("--")] or ["mrs_orange", "cg_kirakira", "s_matikado"]
        raise SystemExit(0 if selftest(shops) else 1)
