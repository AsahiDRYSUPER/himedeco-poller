"""shift_apply（出勤申請の読み取り）が、画面の形どおりに読めるかを確かめる（ネット不要・名前は架空）。"""
from datetime import date, datetime

import shift_apply as sa

GID = "11111111"


def cell(day, start="", end="", msg="", absent="", head=""):
    h = f'<span class="expected-head">{head}</span>' if head else ""
    fixed = " 確定済" if head == "" and start else ""
    return (f'<div class="calendar-item" data-sel="sel_{GID}_{day}"><div class="item-body">{h}'
            f'<p class="date-text">{start}~{end}{fixed}</p>'
            f'<input type="hidden" name="expected_start_time" value="{start}">'
            f'<input type="hidden" name="expected_end_time" value="{end}">'
            f'<input type="hidden" name="expected_display_message" value="{msg}">'
            f'<input type="hidden" name="expected_absent_flg" value="{absent}"></div></div>')


HTML = "<html><body>" + "".join([
    cell("20261005"),                                                   # 申請なし
    cell("20261006", "1500", "0300"),                                   # 確定済 15:00〜27:00
    cell("20261008", "1930", "2300", absent="0", head="承認待ち"),
    cell("20261009", "2000", "0000", absent="0", head="承認待ち"),       # 〜24:00
    cell("20261010", "", "", msg="出勤予定", absent="0", head="承認待ち"),  # 文字だけ
    cell("20261011", "", "", absent="1", head="承認待ち"),               # 休み
    cell("20261012", "0030", "0500", absent="0", head="承認待ち"),       # 夜中 24:30〜29:00
]) + "</body></html>"

ok = True


def check(got, want, label):
    global ok
    if got != want:
        ok = False
        print(f"✗ {label}: {got!r}（期待 {want!r}）")


apps = sa.parse(HTML, GID)
check([a["day"].day for a in apps], [6, 8, 9, 10, 11, 12], "申請のある日")
check([(a["start"], a["end"], a["state"]) for a in apps][:3],
      [("15:00", "27:00", "確定済"), ("19:30", "23:00", "承認待ち"), ("20:00", "24:00", "承認待ち")], "時刻と状態")
check(apps[3]["text"], "出勤予定", "文字だけの申請")
check(apps[4]["absent"], True, "休みの申請")
check((apps[5]["start"], apps[5]["end"]), ("24:30", "29:00"), "夜中の申請")
check(sa.times("1930", "1900"), None, "長すぎる時間は読まない")

NOW = datetime(2026, 10, 5, 19, 0)
check(sa.pick(apps, NOW), [(date(2026, 10, 8), "19:30", "23:00"), (date(2026, 10, 9), "20:00", "24:00"),
                           (date(2026, 10, 12), "24:30", "29:00")], "指定なし→承認待ちだけ")
check(sa.pick(apps, NOW, [date(2026, 10, 6), date(2026, 10, 10)]), [(date(2026, 10, 6), "15:00", "27:00")],
      "名指しの日だけ（文字だけの日は取らない）")
only_fixed = [a for a in apps if a["state"] == "確定済"]
check(sa.pick(only_fixed, NOW), [(date(2026, 10, 6), "15:00", "27:00")], "承認待ちが無ければ1週間の確定済")
check(sa.pick(only_fixed, datetime(2026, 10, 7, 4, 0)), [], "終わった日は取らない")
check(sa.pick(apps, datetime(2026, 10, 6, 23, 0)), [(date(2026, 10, 8), "19:30", "23:00"), (date(2026, 10, 9), "20:00", "24:00"),
                                                    (date(2026, 10, 12), "24:30", "29:00")], "指定なしは承認待ち")

check(sa.named_days("8日と9日、申請した時間で個室お願いします", NOW, "mrs_orange"), [date(2026, 10, 8), date(2026, 10, 9)], "名指しの日")
check(sa.named_days("出勤申請しました！個室お願いします", NOW, "mrs_orange"), [], "名指しなし")
check(sa.named_days("明日の分の申請しました、個室お願いします", NOW, "mrs_orange"), [date(2026, 10, 6)], "明日")

for t in ("出勤申請を出したので、その時間の個室をお願いします", "申請出しました🙏 個室も取ってもらえますか？",
          "8日と9日、申請した時間で個室お願いします", "申請通りで個室お願いします"):
    check(bool(sa.APPLY_RE.search(t)), True, f"申請の話: {t}")
for t in ("個室の申請お願いします", "10日 14時〜20時 個室お願いします"):
    check(bool(sa.APPLY_RE.search(t)), False, f"申請の話ではない: {t}")
for t, want in (("出勤申請したので個室お願いします", True), ("申請しました。個室とってもらえますか？", True),
                ("申請しました。個室はいらないです", False), ("出勤申請を取り消したいです。個室もキャンセルで", False),
                ("出勤申請しました", False)):
    check(sa.wants_booth(t), want, f"個室を頼んでいるか: {t}")
check(sa.display(apps[1:3]), "8日 19:30〜23:00（承認待ち）・9日 20:00〜24:00（承認待ち）", "通知の書き方")

print("すべて通りました" if ok else "直すところがあります")
raise SystemExit(0 if ok else 1)
