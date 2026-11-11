"""yoasobi_roster の読み取りとリンクの置き換え、会話リストの「問い合わせしたい」の見分けを、作り物のページで確かめる（2026-11-11）。
本物の店の名前・女の子の名前は使わない。"""
import sys
from datetime import datetime, timedelta, timezone

import yoasobi_roster as R

ok = True

LIST = """
<li class="box sp_mode btn_girls"><a href="/en/okayama/YA3301/testshop/girlid-11111111/"><img></a>
<a href="/en/okayama/YA3301/testshop/girlid-11111111/">
    <p class="name year">SAMPLEA
        (20Age)
    </p>
</a>
<p class="size">
    T158 84(C)・56・83
</p>
<div class="min-price"><p class="min-price-text"><span class="girl-price">12,345</span></p></div></li>
<li class="box sp_mode btn_girls"><a href="/en/okayama/YA3301/testshop/girlid-22222222/">
    <p class="name year">SAMPLEB (22Age)</p>
</a>
<p class="size">T150 90(F)・58・88</p>
<div class="min-price"><span class="girl-price">23,456</span></div></li>
"""
g = R.parse_list(LIST)
ok &= list(g) == ["11111111", "22222222"]
ok &= g["11111111"] == {"name": "SAMPLEA", "age": "20", "size": "T158 84(C)・56・83", "price": "12,345"}
ok &= g["22222222"]["price"] == "23,456"

ATT = """<a href="attend/date-20990102/">x</a>
<li class="box sp_mode btn_girls"><a href="/en/okayama/YA3301/testshop/girlid-22222222/"><p class="shukkin_detail_time"><img>
    19:00&nbsp;-&nbsp;3:00</p><p class="name">SAMPLEB</p></a></li>
<li class="box sp_mode btn_girls"><a href="/en/okayama/YA3301/testshop/girlid-33333333/"><p class="name">SAMPLEC</p></a></li>
"""
a = R.parse_attend(ATT)
ok &= a == [("22222222", "19:00-3:00"), ("33333333", "時間未定")]

# 置き換え記号
r = R.fill_links("予約は[予約リンク] 地図[地図:サンプルホテル] 子[女の子:11111111] 表[出勤表] 消える[地図] [女の子:]", "testshop", "RSV")
ok &= "RSV" in r and "[" not in r
ok &= "https://www.google.com/maps/search/?api=1&query=" in r and "%E5%B2%A1%E5%B1%B1" in r      # 「岡山」を足して探す
ok &= "https://yoasobi-heaven.com/en/okayama/YA3301/testshop/girlid-11111111/" in r
ok &= "https://yoasobi-heaven.com/en/okayama/YA3301/testshop/attend/" in r
ok &= R.fill_links("そのまま", "testshop", "RSV") == "そのまま"
ok &= R.fill_links("全角［女の子:11111111］と［地図：サンプル］と［地図］", "testshop", "RSV") == "全角https://yoasobi-heaven.com/en/okayama/YA3301/testshop/girlid-11111111/と" + R.map_url("サンプル") + "と"

# 読めない時（確認画面・つながらない）は空。出勤ゼロと取り違えない
R._CACHE.clear()
def boom(url):
    raise RuntimeError("status 202")
R._get = boom
ok &= R.roster_text("testshop") == ""

# 1回の実行で1店1回だけ読む・サイトの「今日」は先の日のリンクの前日
calls = []
def fake(url):
    calls.append(url)
    if url.endswith("girls-list/"):
        return LIST
    if url.endswith("attend/"):
        return ATT
    return ATT.replace("SAMPLEB", "SAMPLEB")
R._CACHE.clear()
R._get = fake
now = datetime(2099, 1, 1, 21, 0, tzinfo=timezone(timedelta(hours=9)))
t1 = R.roster_text("testshop", now=now)
t2 = R.roster_text("testshop", now=now)
ok &= t1 == t2 and len(calls) == 1 + 1 + R.DAYS_AHEAD
ok &= "SAMPLEA（20歳・T158 84(C)・56・83・60分12,345円〜）［女の子:11111111］" in t1
ok &= "1/1（木・今日）の出勤：SAMPLEB 19:00-3:00、番号33333333 時間未定" in t1
ok &= "1/2（金・明日）の出勤" in t1

# 会話リストの1行：ボットの「問い合わせしたい」で止まっている会話は未返信に数える
import yoasobi_watch as W
nw = datetime(2099, 1, 5, 12, 0, tzinfo=W.JST)
r1 = W.parse_row("SampleGuest 自分: I WANT TO INQUIRE 01/05 11:50", nw)
r2 = W.parse_row("SampleGuest 自分: CAST LIST 01/05 11:50", nw)
r3 = W.parse_row("SampleGuest こんにちは 01/05 11:50", nw)
r4 = W.parse_row("SampleGuest 自分: お問い合わせありがとうございます 01/05 11:50", nw)
ok &= r1["mine"] and r1["inquiry"]
ok &= r2["mine"] and not r2["inquiry"]
ok &= (not r3["mine"]) and not r3["inquiry"]
ok &= r4["mine"] and not r4["inquiry"]

# 今から入れる時間（作り物の数字。移動の分は本物と違う値で試す）
OS = R.open_slots
ok &= OS((1200, 1500), [], 1260, 20) == [(1280, 1500)]
ok &= OS((1200, 1500), [(1290, 1350)], 1260, 20) == [(1370, 1500)]                 # 予約の直前の短い隙間は出さない
ok &= OS((1200, 1500), [(1350, 1410)], 1260, 20) == [(1280, 1344), (1430, 1500)]   # 次の予約の前は少しあける
ok &= OS((1200, 1500), [(1000, 1100)], 1260, 20) == [(1280, 1500)]                 # 終わった予約は関係ない
ok &= OS((1200, 1500), [], 1600, 20) == []                                         # 出勤が終わっている
ok &= OS((1200, 1500), [], 1260, None) is None and OS(None, [], 1260, 20) is None
ok &= OS((1380, 1500), [], 1260, 20) == [(1400, 1500)]                             # 出勤前の子は、出勤の始め＋移動から
ok &= R.course_cap(130) == "120分も入る" and R.course_cap(95) == "90分まで" and R.course_cap(64) == "60分まで"
W._AVAIL.update(loaded=True, data={"generated_at": "2099-01-01T21:00:00+09:00", "day": "2099年1月1日(木)", "girls": [
    {"shop": "testshop", "name": "テストA", "work": [1200, 1500], "busy": [[1290, 1350]], "absent": False},
    {"shop": "testshop", "name": "テストB", "work": [1200, 1380], "busy": [[1280, 1370]], "absent": False},
    {"shop": "other", "name": "テストC", "work": [1200, 1500], "busy": [], "absent": False}]})
W.ARRIVE_AFTER = 20
av = W.avail_text("testshop", now=datetime(2099, 1, 1, 21, 0, tzinfo=W.JST))
ok &= "今は 21:00" in av and "テストC" not in av
ok &= "テストA：出勤 20:00-翌1:00（一番早い到着は 20:20）／予約で埋まっている時間 21:30-22:30／今から入れる時間 22:50頃〜翌1:00（120分も入る）" in av
ok &= "テストB：" in av and "今から入れる時間 なし（60分が入る空きが無い）" in av
W.ARRIVE_AFTER = None
ok &= "今から入れる時間" not in W.avail_text("testshop", now=datetime(2099, 1, 1, 21, 0, tzinfo=W.JST))

print("yoasobi_roster:", "ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
