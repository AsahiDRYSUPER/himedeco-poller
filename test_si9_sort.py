"""新着んの並び替えの決め方と、即ヒメの画面の読み方が壊れていないか（本物には触らない。名前は作りもの）。"""
from datetime import datetime

import si9_sort as S

JST = S.JST


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=JST)


# ---------- 時刻 ----------
assert S.bd(10, 0) == 600 and S.bd(0, 40) == 1480 and S.bd(5, 59) == 1799 and S.bd(6, 0) == 360
assert S.span("12:00", "2:00") == (720, 1560) and S.span("10:00", "19:00") == (600, 1140) and S.span("20:00", "27:00") == (1200, 1620)
assert S.span("20:00") == (1200, S.END_MIN) and S.span("20:00", "") == (1200, S.END_MIN)
assert S.biz_date(at(2026, 10, 19, 1, 30)).isoformat() == "2026-10-18"      # 夜中は前の日の続き
assert S.biz_date(at(2026, 10, 19, 8, 0)).isoformat() == "2026-10-19"
assert S.in_window(at(2026, 10, 18, 8, 0)) and S.in_window(at(2026, 10, 19, 1, 59))
assert not S.in_window(at(2026, 10, 18, 7, 59)) and not S.in_window(at(2026, 10, 19, 2, 0)) and not S.in_window(at(2026, 10, 19, 5, 0))
assert S.gap_ok(at(2026, 10, 18, 17, 10), "2026-10-18T17:00:00+09:00") and not S.gap_ok(at(2026, 10, 18, 17, 9), "2026-10-18T17:00:00+09:00")
assert S.gap_ok(at(2026, 10, 18, 17, 9), "") and S.gap_ok(at(2026, 10, 18, 17, 9), None)
assert not S.gap_ok(at(2026, 10, 18, 17, 9), "2026-10-18T16:00:00+09:00", "2026-10-18T17:05:00+09:00")   # どれか1つでも10分以内なら待つ

# ---------- 名前 ----------
assert S.norm("試験甲...") == "試験甲" and S.norm("試験乙【新入") == "試験乙" and S.norm("試験丙【業界未経験】") == "試験丙"
assert S.norm("試ヶ験 丁") == "試ケ験丁" and S.norm("試験戊…") == "試験戊" and S.norm("Ｍｉｋｕ") == "Miku"

# ---------- 即ヒメの画面（10/17 キラ学の作りと同じ形。名前は作りもの） ----------
def box(cls, name, shift, red, comment="", serving=False):
    return (f'<table class="{cls}"><tr><td rowspan="2" style="border: none; padding: 5px;"><img class="sokuhimeimg" src="x.jpg"></td>'
            f'<td style="border: none; width:105px">{name}</td><td style="border: none; vertical-align:top;">新</td></tr>'
            f'<tr><td style="border: none;" colspan="2">{shift}</td><td style="border: none; color: #F00;">{red}</td>'
            f'<td style="border: none;" class="marquee">{comment}</td>'
            f'<td><img class="{"servingEndTimeUpdate" if serving else "servingEndTime"}" src="{"sekkyaku_on" if serving else "sekkyaku_off"}.gif"></td></tr></table>')


HTML = "".join([
    box("sokuhimegirlbox2", "試験一号【…", "12:00～ 2:00", "16:26～待機中", "148cm 18:00～19:00 にご案内"),
    box("sokuhimegirlbox", "試験三号…", "16:00～ 22:00", "次回17:58～", "2025年 デビュー 10:00～", serving=True),
    box("sokuhimegirlbox3", "試験桜【…", "10:00～ 18:00", "受付終了", "受付終了まであと少し"),
    box("sokuhimegirlbox3", "試験十三…", "20:30～ 3:00", "次回00:40～", "", serving=True),
    box("sokuhimegirlbox3", "試験十四", "17:00～ 0:00", "", ""),
    box("sokuhimegirlbox3", "試験十五", "20:00～ LAST", "20:00～待機中", ""),
])
bx = S.parse_standby(HTML)
assert [b["short"] for b in bx] == ["試験一号", "試験三号", "試験桜", "試験十三", "試験十四", "試験十五"], bx
assert [b["cut"] for b in bx] == [False, True, False, True, False, False]
assert bx[5]["status"] == "waiting" and bx[5]["shift"] == (1200, S.END_MIN)
assert bx[0]["status"] == "waiting" and bx[0]["shift"] == (720, 1560) and bx[0]["since"] == 986
assert bx[1]["status"] == "next" and bx[1]["next"] == 17 * 60 + 58 and bx[1]["shift"] == (960, 1320)   # コメントの数字は見ない
assert bx[2]["status"] == "closed" and bx[2]["shift"] == (600, 1080)
assert bx[3]["status"] == "next" and bx[3]["next"] == 1480 and bx[3]["shift"] == (1230, 1620)
assert bx[4]["status"] == "none" and bx[4]["shift"] == (1020, 1440)

# ---------- 突き合わせ ----------
G = [{"id": "1", "name": "試験一号【PREMIUM】"}, {"id": "2", "name": "試験三号"}, {"id": "3", "name": "試験桜一"}, {"id": "4", "name": "試験桜二"}]
m = S.match_boxes(G, [{"short": "試験一号"}, {"short": "試験三号"}, {"short": "試験桜", "cut": True}])
assert set(m) == {"1", "2"}                                         # 「試験桜」は2人に合うので使わない
assert S.match_boxes(G, [{"short": "試験三号"}, {"short": "試験三", "cut": True}]) == {}  # 2つの箱が同じ子に合ったら使わない
assert S.match_boxes(G, [{"short": "試験三", "cut": False}]) == {}       # 切れていない名前は完全一致だけ
# 名前が切れた箱で候補が2人：今日出勤の子に絞る
G2 = [{"id": "a", "name": "試験まな"}, {"id": "b", "name": "試験まなみ"}]
assert S.match_boxes(G2, [{"short": "試験ま", "cut": True}], working={"b"}) == {"b": {"short": "試験ま", "cut": True}}
assert S.match_boxes(G2, [{"short": "試験ま", "cut": True}], working=set()) == {}
assert set(S.match_boxes(G2, [{"short": "試験まな", "cut": False}])) == {"a"}   # 「試験まな」は「試験まなみ」に取られない
assert S.match_boxes(G2, [{"short": "試験ゆい", "cut": False}]) == {}            # いない子の箱は誰にも付かない
assert S.match_absent(G, [("試験三号,試験三号RSP", "cg_kirakira")], "cg_kirakira") == {"2"}
assert S.match_absent(G, [("RSP,試験三号", None)], "cg_kirakira") == {"2"}       # 別名のどれでも（完全一致）
assert S.match_absent(G, [("試験一", "cg_kirakira")], "cg_kirakira") == set()     # 名前の途中までの前方一致は使わない
assert S.match_absent(G, [("試験桜", "cg_kirakira")], "cg_kirakira") == set()     # 2人に合うので使わない
G3 = [{"id": "k", "name": "試験ゆうか♡"}, {"id": "l", "name": "試験ゆうかりん"}]
assert S.match_absent(G3, [("試験ゆうか", "cg_kirakira")], "cg_kirakira") == {"k"}   # 残りが記号だけなら合う
assert S.match_absent(G3, [("試験ゆうか", None)], "cg_kirakira") == set()           # 店の印が無い子は完全一致だけ
assert S.is_cut("あいう…") and S.is_cut("あいう...") and not S.is_cut("あいう【…") and not S.is_cut("あいう") and not S.is_cut("あいう【新人】")

# ---------- 決め方（2026-10-18 17:00） ----------
D = ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"]


def wk(today=None, **future):
    w = {}
    if today:
        w["2026-10-18"] = ["2", today[0], today[1]]
    for d, (s, e) in future.items():
        w["2026-10-" + d[1:]] = ["2", s, e]
    return w


girls = [{"id": f"g{i}", "name": n, "fixed": False} for i, n in enumerate(
    ["試験一号", "試験二号", "試験三号", "試験四号", "試験桜一", "試験六号", "試験七号", "試験八号", "試験九号", "試験十号", "試験十一", "試験十二"], 1)]
week = {
    "g1": wk(("12:00", "02:00")), "g2": wk(("10:00", "18:00")), "g3": wk(("16:00", "22:00")), "g4": wk(("19:00", "01:00")),
    "g5": wk(("10:00", "18:00")), "g6": wk(("10:00", "15:00")), "g7": wk(("12:00", "20:00")),
    "g8": wk(None, d19=("12:00", "20:00")), "g9": wk(None, d21=("10:00", "15:00")), "g10": {},
    "g11": wk(("15:00", "23:00")), "g12": wk(("14:00", "20:00")),
}
boxes = [
    {"short": "試験一号", "status": "waiting", "shift": (720, 1560), "since": 986, "next": None},
    {"short": "試験二号", "status": "waiting", "shift": (600, 1080), "since": 1000, "next": None},
    {"short": "試験三号", "status": "next", "shift": (960, 1320), "since": None, "next": 1078},
    {"short": "試験桜一", "status": "closed", "shift": (600, 1080), "since": None, "next": None},
    {"short": "試験十一", "status": "next", "shift": (900, 1380), "since": None, "next": 1050},
    {"short": "試験七号", "status": "waiting", "shift": (720, 1200), "since": 900, "next": None},     # 当欠の子に箱が残っていても当欠
]
order, counts = S.decide(girls, week, D, boxes, {"g7"}, at(2026, 10, 18, 17, 0))
assert order == ["g2", "g1", "g12", "g11", "g3", "g4", "g5", "g6", "g7", "g8", "g9", "g10"], order
assert counts["①待機中"] == 2 and counts["②接客中・出勤前"] == 4 and counts["③終わった"] == 2 and counts["④当欠"] == 1
assert counts["⑤先の出勤あり"] == 2 and counts["⑤先の出勤なし"] == 1

# 並べた後にもう一度決めても同じ（毎回揺れない）
again = [next(g for g in girls if g["id"] == i) for i in order]
assert S.decide(again, week, D, boxes, {"g7"}, at(2026, 10, 18, 17, 0))[0] == order
# 同じ時刻の中は、今の並びで上の子が先
rev = list(reversed(girls))
o2 = S.decide(rev, week, D, boxes, {"g7"}, at(2026, 10, 18, 17, 0))[0]
assert o2.index("g6") < o2.index("g5")          # ③ の2人はどちらも10:00出勤 → 今の並びが上の g6 が先
assert o2[-1] == "g10"

# ピン留めの子は今の位置のまま
pinned = [dict(g) for g in girls]
pinned[3]["fixed"] = True                         # g4 を4番目にピン留め
o3 = S.decide(pinned, week, D, boxes, {"g7"}, at(2026, 10, 18, 17, 0))[0]
assert o3[3] == "g4" and sorted(o3) == sorted(g["id"] for g in girls)
assert [x for x in o3 if x != "g4"] == [x for x in order if x != "g4"]

# 朝 8:30（即ヒメはまだ見ない＝箱なし）：今日の出勤の早い順。終わった子はまだいない
o4, c4 = S.decide(girls, week, D, [], set(), at(2026, 10, 18, 8, 30))
assert o4[:9] == ["g2", "g5", "g6", "g1", "g7", "g12", "g11", "g3", "g4"], o4
assert c4["①待機中"] == 0 and c4["③終わった"] == 0

# 夜中 1:00（営業日は 10/18）。画面の列は前の週から足した 10/18 と、10/19 から先
D2 = ["2026-10-18"] + D[1:]
o5 = S.decide(girls, week, D2, [{"short": "試験一号", "status": "next", "shift": (720, 1560), "since": None, "next": 1530}],
              set(), at(2026, 10, 19, 1, 0))[0]
# 出勤中は 1(次回1:30)・4(19:00〜1:00 → 1:00ちょうどで終わり)。g1 が一番上、終わった子は出勤の早い順
assert o5[0] == "g1" and o5.index("g4") > o5.index("g1")
assert o5.index("g8") > o5.index("g4")           # 明日出勤の子は、今日終わった子より下

# 箱に状態（待機中）があるのに、出勤がどこにも無い子（取り込み前に足された出勤）→ 今日出勤している子として ① に入る
w9 = dict(week); w9["g9"] = wk(None, d21=("10:00", "15:00"))
o9 = S.decide(girls, w9, D, [{"short": "試験九号", "cut": False, "status": "waiting", "shift": None, "since": 1000, "next": None}], set(), at(2026, 10, 18, 17, 0))[0]
assert o9[0] == "g9"

# ---------- 送らない時の見分け ----------
T = at(2026, 10, 18, 17, 0)
assert S.inputs_trouble([], "ConnectionError", "", girls, week, T, 0) == "ヘブンの即ヒメが読めない"
assert S.inputs_trouble([], "ConnectionError", "", girls, week, at(2026, 10, 18, 8, 30), 0) == ""      # 9時前は即ヒメを見ない
assert S.inputs_trouble(boxes, "", "TimeoutError", girls, week, T, 5) == "CTIの当欠が読めない"
assert "即ヒメの箱が0" in S.inputs_trouble([], "", "", girls, week, T, 0)
assert "即ヒメの箱が0" in S.inputs_trouble([], "", "", girls, week, at(2026, 10, 18, 10, 5), 0)      # 開店後は箱が無いとおかしい
assert S.inputs_trouble([], "", "", girls, week, at(2026, 10, 18, 9, 30), 0) == ""                     # 開店前は箱が無くてもよい
assert "1つも合わない" in S.inputs_trouble([{"short": "該当なし", "status": "waiting", "shift": None}], "", "", girls, week, T, 0)
assert "合った子が少ない" in S.inputs_trouble(boxes, "", "", girls, week, T, 2)                        # 箱6つのうち2つしか合わない
assert S.inputs_trouble(boxes, "", "", girls, week, T, 3) == ""                                        # 半分なら送る
assert S.inputs_trouble(boxes, "", "", girls, week, T, 5) == ""
assert S.inputs_trouble(boxes[:3], "", "", girls, week, T, 1) == ""                                    # 箱が3つ以下なら数で止めない

# ---------- 送るかどうか ----------
cur_ids = [g["id"] for g in girls]
f_cur, f_new = S.fp(cur_ids), S.fp(order)
assert S.need_send(order, cur_ids, {}) == (True, True, "並びが変わった")
assert S.need_send(cur_ids, cur_ids, {})[1] is True                                    # 一度も送れていない並びは送る
assert S.need_send(cur_ids, cur_ids, {"ok_fp": f_cur})[1] is False                     # 7サイトに届いた並びは送らない
assert S.need_send(order, cur_ids, {"ok_fp": f_cur}) == (True, True, "並びが変わった")
assert S.need_send(cur_ids, cur_ids, {"tries": {"fp": f_cur, "n": 2}})[1] is False     # 同じ並びは2回まで
assert S.need_send(cur_ids, cur_ids, {"tries": {"fp": f_cur, "n": 1}})[1] is True
assert S.need_send(order, cur_ids, {"tries": {"fp": f_cur, "n": 2}})[1] is True        # 違う並びなら回数は関係ない
assert S.result_ok({"シティヘブンネット（A）": {"成功": 10, "対象外": 3}, "B": {"成功": 5}}, 2) == (True, "")
assert S.result_ok({"シティヘブンネット（A）": {"成功": 10}, "B": {"成功": 5, "失敗": 1}}, 2) == (True, "他サイトで失敗 B")   # ヘブンが成功なら送れた扱い
assert S.result_ok({"シティヘブンネット（A）": {"失敗": 10}, "B": {"成功": 5}}, 2)[0] is False
assert S.result_ok({"A": {"成功": 10}}, 2)[0] is False                                  # ヘブンの行が無い
assert S.result_ok({"_": "今回の分の結果の行が無い"}, 7)[0] is False
assert S.result_ok({}, 7)[0] is False
assert S.minutes_since(at(2026, 10, 18, 17, 14), 17, 10) == 4 and S.minutes_since(at(2026, 10, 19, 0, 5), 23, 58) == 7
rows = [(17, 10, "A", "u"), (16, 55, "B", "u")]
assert not S.someone_sent_after(at(2026, 10, 18, 17, 12), "2026-10-18T17:10:00+09:00", rows)   # 最新＝私たちの分
assert S.someone_sent_after(at(2026, 10, 18, 17, 40), "2026-10-18T17:10:00+09:00", [(17, 30, "A", "u")] + rows)  # 人が17:30に送った
assert not S.someone_sent_after(at(2026, 10, 18, 17, 40), "", rows) and not S.someone_sent_after(at(2026, 10, 18, 17, 40), "2026-10-18T17:10:00+09:00", [])
assert S.safe_err(RuntimeError("だめ\nCall log:\n  - 試験三号 の行")) == "RuntimeError: だめ"

# ---------- 新着んの「送っている途中か」の返事 ----------
assert S.parse_sorting('{"is_sorting":false}') is False and S.parse_sorting('{"is_sorting": true}') is True
assert S.parse_sorting('"{\\"is_sorting\\":false}"') is False          # 文字列の中にJSON（JSON.stringify した時）
assert S.parse_sorting({"is_sorting": False}) is False and S.parse_sorting({"is_sorting": "1"}) is True
assert S.parse_sorting("ERR 500") is None and S.parse_sorting("<html>") is None and S.parse_sorting('{"other": 1}') is None and S.parse_sorting(None) is None

# ---------- 動く子の数 ----------
assert S.moved(["a", "b", "c"], ["a", "c", "b"]) == (2, 2) and S.moved(["a"], ["a"]) == (0, None)

# ---------- ログの名前を伏せる ----------
S._NAMES.update({"試験三号", "試験一号【PREMIUM】"})
assert S.scrub("試験三号さんと 試験一号【PREMIUM】") == "＊＊さんと ＊＊"

print("新着んの並び替えのテスト: OK")
