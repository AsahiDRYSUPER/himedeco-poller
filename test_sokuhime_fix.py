"""即ヒメ直しの決め方と、画面の読み方が壊れていないか（本物には触らない）。"""
import datetime

import cti_schedule as C
import sokuhime_fix as S

BOX = """<form name="sokuhimeForm" action="C9StandbyGirlList.php?shopdir=cg_kirakira" method="post">
<input type="hidden" name="_token__" value="tok"><input type="hidden" name="c_member_id" value=""><input type="hidden" name="girls_name" value="">
<input type="hidden" name="checkFlg" value=""><input type="hidden" name="update" value=""><input type="hidden" name="servingEndTime" value="">
<input type="hidden" id="servingEndHourHtmlList" value='<option value="00">00</option><option value="23">23</option><option value="25">25</option>'></form>
<table class="sokuhimegirlbox2"><tr><td><table><tr><td style="border: none; width:105px"> ＊子【新】…</td></tr>
<tr><td style="border: none;" colspan="2"> 12:00～ 3:00</td></tr></table></td></tr>
<tr><td><img id="111" alt="接客中" name="" class="servingEndTime" src="img/managersimple/sekkyaku_off.gif">
<img id="galAttribute2_0" alt="待機中" name="111" class="waitingUpdate" src="img/managersimple/taiki_on.gif"></td></tr></table>
<table class="sokuhimegirlbox2"><tr><td><table><tr><td style="border: none; width:105px"> 花子</td></tr>
<tr><td style="border: none;" colspan="2"> 18:00～ 2:00</td></tr></table></td></tr>
<tr><td><img id="222" alt="接客中" name="15:30" class="servingEndTime" src="img/managersimple/sekkyaku_on.gif">
<img id="galAttribute2_1" alt="待機中" name="222" class="waitingUpdate" src="img/managersimple/taiki_off.gif"></td></tr></table>"""

boxes, hidden, hours = S.parse_standby(BOX)
assert [b["id"] for b in boxes] == ["111", "222"], boxes
assert boxes[0]["shift"] == "12:00-3:00" and boxes[0]["waiting"] and not boxes[0]["serving"]
assert boxes[1]["serving"] and boxes[1]["end"] == "15:30"
assert hidden["_token__"] == "tok" and "servingEndTime" in hidden
assert hours == ["00", "23", "25"]
assert S.time_for_form(25 * 60 + 30, hours) == "25:30"
assert S.time_for_form(25 * 60 + 30, ["00", "23"]) == "01:30"
assert S.time_for_form(15 * 60 + 5, []) == "15:05"

# CTIの読み方
assert C.minutes("13:25") == 805 and C.minutes("翌0:30") == 1470 and C.minutes("2:30") == 1590
assert C.span("13:25-15:55") == (805, 955) and C.span("翌0:30-翌2:30") == (1470, 1590) and C.span("10:00-翌3:00Up") == (600, 1620)
assert C.badge_shop(["事務所", "出勤", "KG"]) == "cg_kirakira" and C.badge_shop(["街角", "PO"]) == "potya_reen"
assert C.badge_shop(["街角", "出勤"]) is None          # 「街角」は待機場所。店の印ではない

# 決め方（now は 0:00 からの分）
p = {"name": "＊子", "work": (12 * 60, 27 * 60), "bookings": [
    {"s": 13 * 60, "e": 14 * 60, "flags": ["終了"]}, {"s": 15 * 60, "e": 16 * 60 + 30, "flags": ["入室"]}, {"s": 18 * 60, "e": 19 * 60, "flags": ["仮予約"]}]}
w = boxes[0]
assert S.decide(w, p, 11 * 60, 60) == (12 * 60, "出勤前")
assert S.decide(w, p, 13 * 60 + 30, 60) is None                       # 終了した箱は無視、次は90分後
assert S.decide(w, p, 14 * 60 + 10, 60) == (16 * 60 + 30, "50分後に開始")
assert S.decide(w, p, 15 * 60 + 10, 60) == (16 * 60 + 30, "接客中（入室）")
assert S.decide(w, p, 16 * 60 + 40, 60) is None                       # 次は80分後 → 待機中のまま
assert S.decide(w, p, 17 * 60 + 20, 60) == (19 * 60, "40分後に開始")   # 仮予約も数える
assert S.decide(w, p, 17 * 60 + 20, 30) is None                       # とろ〜りは30分
assert S.decide(boxes[1], p, 15 * 60 + 10, 60) is None                # 接客中の子には触らない
assert S.decide(w, None, 15 * 60, 60) is None

# 名前の突き合わせ
m = S.match(boxes, [p, {"name": "花子", "work": None, "bookings": []}])
assert m["111"] is p and m["222"]["name"] == "花子"
assert S.now_minutes(datetime.datetime(2026, 10, 10, 1, 30)) == 25 * 60 + 30
print("即ヒメ直し: 読み方・決め方 OK")
