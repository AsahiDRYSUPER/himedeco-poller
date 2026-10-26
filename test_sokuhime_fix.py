"""即ヒメ直しの決め方と、画面の読み方が壊れていないか（本物には触らない）。
決まりの分数・動く時間・理由の言葉は試し用の作りもの（本物は鍵つき sealed/ops_sokuhime.enc。鍵が無くても試験は同じに動く）。名前・場所も作りもの。"""
import datetime

import cti_schedule as C
import sokuhime_fix as S

# 試し用の決まり（作りもの）：ふつうの店は 50分、短い店（shop_short）は 25分
LEAD, LEAD_SHORT = 50, 25
S.LEAD_MIN, S.LEAD_DEFAULT = {"shop_short": LEAD_SHORT}, LEAD
S.LAST_TAKE, S.LAST_TAKE_DEFAULT = {"shop_short": LEAD_SHORT}, LEAD
S.OPEN_MIN, S.CLOSE_MIN = 8 * 60, 25 * 60          # 動く時間（作りもの）
S.REASONS = {}                                     # 理由の言葉なし＝記号のまま
assert S.ops_ready()

BOX = """<form name="sokuhimeForm" action="C9StandbyGirlList.php?shopdir=shop_test" method="post">
<input type="hidden" name="_token__" value="tok"><input type="hidden" name="c_member_id" value=""><input type="hidden" name="girls_name" value="">
<input type="hidden" name="checkFlg" value=""><input type="hidden" name="update" value=""><input type="hidden" name="servingEndTime" value="">
<input type="hidden" id="servingEndHourHtmlList" value='<option value="00">00</option><option value="23">23</option><option value="25">25</option>'></form>
<table class="sokuhimegirlbox2"><tr><td><table><tr><td style="border: none; width:105px"> ＊子【新】…</td></tr>
<tr><td style="border: none;" colspan="2"> 12:00～ 3:00</td></tr></table></td></tr>
<tr><td><img id="111" alt="接客中" name="" class="servingEndTime" src="img/managersimple/sekkyaku_off.gif">
<img id="galAttribute2_0" alt="待機中" name="111" class="waitingUpdate" src="img/managersimple/taiki_on.gif"></td></tr></table>
<table class="sokuhimegirlbox3"><tr><td><table><tr><td style="border: none; width:105px"> 花子</td></tr>
<tr><td style="border: none;" colspan="2"> 18:00～ 2:00</td></tr></table></td></tr>
<tr><td><img id="222" alt="接客中" name="15:30" class="servingEndTimeUpdate" src="img/managersimple/sekkyaku_on.gif">
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
assert C.badge_shop(["待機A", "出勤", "KG"]) == C.SHOP_BADGE["KG"] and C.badge_shop(["待機B", "PO"]) == C.SHOP_BADGE["PO"]
assert C.badge_shop(["待機B", "出勤"]) is None          # 待機場所の印は店の印ではない

# 決め方（now は 0:00 からの分）
p = {"name": "＊子", "work": (12 * 60, 27 * 60), "bookings": [
    {"s": 13 * 60, "e": 14 * 60, "flags": ["終了"]}, {"s": 15 * 60, "e": 16 * 60 + 30, "flags": ["入室"]}, {"s": 18 * 60, "e": 19 * 60, "flags": ["仮予約"]}]}
w = boxes[0]
assert S.decide(w, p, 11 * 60, LEAD) == (12 * 60, [("pre", {})])
assert S.decide(w, p, 13 * 60 + 30, LEAD) is None                       # 終了した箱は無視、次は90分後
assert S.decide(w, p, 14 * 60 + 10, LEAD) == (16 * 60 + 30, [("soon", {"min": 50})])
assert S.decide(w, p, 15 * 60 + 10, LEAD) == (16 * 60 + 30, [("serving_in", {})])
assert S.decide(w, p, 16 * 60 + 40, LEAD) is None                       # 次は80分後 → 待機中のまま
assert S.decide(w, p, 17 * 60 + 20, LEAD) == (19 * 60, [("soon", {"min": 40})])   # 仮予約も数える
assert S.decide(w, p, 17 * 60 + 20, LEAD_SHORT) is None                 # 短い店は25分
assert S.decide(boxes[1], p, 15 * 60 + 10, LEAD) is None                # 接客中の子には触らない
assert S.decide(w, None, 15 * 60, LEAD) is None

# 入室なのに時間付けが無い
q = {"name": "x", "work": (600, 1500), "bookings": [{"s": 795, "e": 975, "flags": ["入室"], "rid": "r1"}]}
assert S.timed_fix(q, 800)["rid"] == "r1"
assert S.timed_fix({"name": "x", "bookings": [{"s": 795, "e": 975, "flags": ["入室", "時間付け"], "rid": "r1"}]}, 800) is None
assert S.timed_fix({"name": "x", "bookings": [{"s": 795, "e": 975, "flags": ["入室", "終了"], "rid": "r1"}]}, 800) is None
assert S.timed_fix(q, 980) is None and S.timed_fix(None, 800) is None

# 退勤の「決まりの分数」前を過ぎたら受付終了（ふつうの店 50分・短い店 25分。作りもの）
up = {"name": "x", "work": (12 * 60, 17 * 60), "up": True, "bookings": []}
bx = {"shift": "12:00-17:00"}
assert S.final_end(up, bx, 16 * 60 + 15, "shop_long") == (17 * 60, [("close", {"end": "17:00", "min": 50})])
assert S.final_end(up, bx, 16 * 60 + 10, "shop_long")[0] == 17 * 60                          # ちょうど50分も入らない
assert S.final_end(up, bx, 16 * 60 + 5, "shop_long") == (16 * 60 + 5, [])                    # 55分残る → そのまま
an = {"name": "a", "work": (10 * 60, 24 * 60), "bookings": [
    {"s": 17 * 60 + 33, "e": 18 * 60 + 33, "flags": ["入室", "時間付け"], "rid": "1"},
    {"s": 19 * 60, "e": 20 * 60 + 20, "flags": [], "rid": "2"},
    {"s": 21 * 60, "e": 23 * 60 + 15, "flags": ["予約"], "rid": "3"}]}
assert S.final_end(an, {"shift": "10:00-0:00"}, 18 * 60 + 33, "shop_long")[0] == 24 * 60      # 23:15から退勤まで45分→完売
assert S.final_end(up, bx, 16 * 60 + 9, "shop_short") == (16 * 60 + 9, [])                   # 短い店は25分前まで取れる
assert S.final_end(up, bx, 16 * 60 + 40, "shop_short")[0] == 17 * 60
assert S.final_end(up, {"shift": "12:00-17:30"}, 16 * 60 + 9, "shop_long") == (16 * 60 + 9, [])   # ヘブンの退勤が17:30なら81分残る
assert S.final_end(up, {"shift": ""}, 16 * 60 + 15, "shop_long")[0] == 17 * 60               # ヘブンが読めなければCTIの出勤の終わり
assert S.shift_end_min({"shift": "18:00-2:00"}) == 26 * 60
assert S.final_end(None, {"shift": ""}, 100, "x") == (100, [])

# 予約が無く、退勤まで決まりの分数を切った待機中の子 → 受付終了
w2 = dict(w, shift="12:00-17:00")
none_left = {"name": "y", "work": (12 * 60, 17 * 60), "bookings": [{"s": 13 * 60, "e": 14 * 60, "flags": ["終了"], "rid": "a"}]}
assert S.decide(w2, none_left, 16 * 60 + 10, LEAD) == (17 * 60, [("idle_close", {"end": "17:00", "min": 50})])
assert S.decide(w2, none_left, 15 * 60 + 50, LEAD) is None                         # まだ70分ある
assert S.decide(w2, none_left, 16 * 60 + 10, LEAD, last=LEAD_SHORT) is None        # 短い店は25分前まで取れる
assert S.decide(w2, none_left, 16 * 60 + 40, LEAD, last=LEAD_SHORT)[0] == 17 * 60
assert S.decide(w2, none_left, 17 * 60 + 5, LEAD) is None                          # 退勤を過ぎている（ヘブンが自分で受付終了にする）
later = {"name": "y", "work": (12 * 60, 17 * 60), "bookings": [{"s": 16 * 60 + 30, "e": 17 * 60, "flags": [], "rid": "a"}]}
assert S.decide(w2, later, 15 * 60 + 10, LEAD) is None                             # 80分後の予約：まだ何もしない

# 複数店の子：CTIの表記名「◯◯【業界未経験】,△△RSP」はどちらの名前でも合う
assert S.aliases("試験花子【業界未経験】,試験RSP") == ["試験花子", "試験RSP"]
multi = {"name": "試験花子【業界未経験】,試験RSP", "work": None, "bookings": []}
bxs = [{"id": "9", "name": "試験花子【業界未経験】", "serving": False, "waiting": True, "end": "", "shift": ""},
       {"id": "8", "name": "試験RSP", "serving": False, "waiting": True, "end": "", "shift": ""}]
mm = S.match(bxs, [multi])
assert mm["9"] is multi and mm["8"] is multi

assert S.norm('試ヶ験甲【業界未経験】') == S.norm('試ケ験甲') and S.norm('試ヵ験') == S.norm('試カ験')
assert S.norm('試験猫子【優…') == '試験猫子' and S.norm('試験猫子【優等生】,試験猫RSP'.split(',')[0]) == '試験猫子'

# 先まで予約が詰まっている子：次までの空きが決まりの分数未満なら、その予約の終了までつなげる。退勤まで詰まっていれば受付終了
full = {"name": "a", "work": (10 * 60, 24 * 60), "bookings": [
    {"s": 17 * 60 + 13, "e": 18 * 60 + 33, "flags": ["入室"], "rid": "1"},
    {"s": 18 * 60 + 50, "e": 20 * 60, "flags": [], "rid": "2"},
    {"s": 20 * 60 + 30, "e": 22 * 60, "flags": ["予約"], "rid": "3"},
    {"s": 22 * 60 + 40, "e": 24 * 60, "flags": [], "rid": "4"}]}
assert S.chain_end(full, 18 * 60 + 33, "shop_long") == (24 * 60, 3)
fe, why = S.final_end(full, {"shift": "10:00-0:00"}, 18 * 60 + 33, "shop_long")
assert fe == 24 * 60 and [c for c, _ in why] == ["chain", "close"], (fe, why)
gap = {"name": "a", "work": (10 * 60, 24 * 60), "bookings": [
    {"s": 17 * 60 + 13, "e": 18 * 60 + 33, "flags": ["入室"], "rid": "1"},
    {"s": 20 * 60, "e": 21 * 60, "flags": [], "rid": "2"}]}
assert S.chain_end(gap, 18 * 60 + 33, "shop_long") == (18 * 60 + 33, 0)        # 87分空く → 18:33で待機中に戻る
assert S.final_end(gap, {"shift": "10:00-0:00"}, 18 * 60 + 33, "shop_long") == (18 * 60 + 33, [])
half = {"name": "a", "work": (10 * 60, 24 * 60), "bookings": [
    {"s": 17 * 60 + 13, "e": 18 * 60 + 33, "flags": ["入室"], "rid": "1"},
    {"s": 19 * 60, "e": 20 * 60, "flags": [], "rid": "2"}]}
assert S.final_end(half, {"shift": "10:00-0:00"}, 18 * 60 + 33, "shop_long")[0] == 20 * 60   # 27分しか空かない → 20:00まで

# 名前の突き合わせ
m = S.match(boxes, [p, {"name": "花子", "work": None, "bookings": []}])
assert m["111"] is p and m["222"]["name"] == "花子"
assert S.now_minutes(datetime.datetime(2026, 10, 10, 1, 30)) == 25 * 60 + 30

# 理由の文：言葉（作りもの）があれば差し込む、無ければ記号のまま
assert S.why_text([("soon", {"min": 40})]) == "soon(min=40)"
S.REASONS = {"soon": "試験{min}", "close": "→試験の終わり{end}"}
assert S.why_text([("soon", {"min": 40}), ("close", {"end": "17:00", "min": 50})]) == "試験40→試験の終わり17:00"
assert S.why_text([("pre", {})]) == "pre"
S.REASONS = {"soon": "壊れた{nai}"}
assert S.why_text([("soon", {"min": 1})]) == "soon(min=1)"          # 差し込めない言葉は記号に戻す
S.REASONS = {}

# 動く時間（作りもの）が開けない時も直さない合図
S.OPEN_MIN, S.CLOSE_MIN = None, None
assert not S.ops_ready()
S.OPEN_MIN, S.CLOSE_MIN = 8 * 60, 25 * 60

# 決まりが開けない時（数がそろわない）は直さない合図になる。決め方も「何もしない」に倒れる
S.LEAD_DEFAULT, S.LAST_TAKE_DEFAULT, S.LAST_TAKE = None, None, {}
assert not S.ops_ready()
assert S.decide(w2, none_left, 16 * 60 + 40, LEAD) is None
assert S.chain_end(full, 18 * 60 + 33, "shop_long") == (18 * 60 + 33, 0)
assert S.final_end(up, bx, 16 * 60 + 15, "shop_long") == (16 * 60 + 15, [])

# 時間帯の決まりの読み方（作りもの）
import ops_hours as H
conf = {"fix": ["08:00", "25:00"], "chain": ["08:00", "26:00"], "last_weekday": 26, "bad": ["x", "1"]}
assert H.span("fix", conf) == (480, 1500) and H.span("bad", conf) is None and H.span("none", conf) is None
assert H.value("last_weekday", conf) == 26 and H.value("fix", conf) is None
assert H.within("fix", datetime.datetime(2026, 10, 10, 0, 30), conf) is True       # 翌0:30 は 24:30
assert H.within("fix", datetime.datetime(2026, 10, 10, 1, 0), conf) is False
assert H.within("fix", datetime.datetime(2026, 10, 10, 7, 59), conf) is False
assert H.within("chain", datetime.datetime(2026, 10, 10, 1, 59), conf) is True
assert H.within("none", datetime.datetime(2026, 10, 10, 12, 0), conf) is None

# 即ヒメ直しの間隔：決まり（作りもの）を渡した時と、開けない時（ふだんの間隔・忙しい時間帯なし）
import sokuhime_loop as L
conf = {"busy": [["12:00", "13:00"], ["22:00", "25:00"]], "busy_every_min": 2, "every_min": 4}
assert L.pace(datetime.datetime(2026, 10, 10, 12, 30), conf) == (2, True)
assert L.pace(datetime.datetime(2026, 10, 10, 0, 30), conf) == (2, True)        # 翌0時台は 24時台として比べる
assert L.pace(datetime.datetime(2026, 10, 10, 14, 0), conf) == (4, False)
assert L.pace(datetime.datetime(2026, 10, 10, 12, 30), {}) == (L.DEFAULT["every_min"], False)
print("即ヒメ直し: 読み方・決め方 OK")
