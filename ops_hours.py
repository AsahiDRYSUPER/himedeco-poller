"""時間帯の決まり（動く時間・「ラスト」の時刻など）を読む。中身は鍵つき（sealed/ops_sokuhime.enc の "hours"）。2026-10-26
開けない時・書いていない時は None を返す（呼ぶ側は「その仕事をしない・人へ回す」に倒す）。

時刻は "HH:MM"。翌日の深夜は 24 を足して書く（"25:30"＝翌1:30）。今の時刻は 0〜(CUT_H-1)時を前の日の続き（24時〜）として比べる。
使い方（ワークフローから）：python3 ops_hours.py within <名前>  → その時間帯なら 0、外なら 1、決まりが開けなければ 2
"""
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
CUT_H = 5            # 0:00〜4:59 は前の日の続き


def hours():
    try:
        import secret_box
        return dict((secret_box.data("ops_sokuhime") or {}).get("hours") or {})
    except Exception:
        return {}


def to_min(t):
    """"07:30" → 450、"25:30" → 1530。数ならそのまま。読めなければ None"""
    if isinstance(t, bool):
        return None
    if isinstance(t, int):
        return t
    try:
        h, m = str(t).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def span(name, conf=None):
    """名前の時間帯 → (始まりの分, 終わりの分)。読めなければ None"""
    v = (hours() if conf is None else conf).get(name)
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    a, b = to_min(v[0]), to_min(v[1])
    return (a, b) if a is not None and b is not None and a < b else None


def value(name, conf=None):
    """数の決まり（「ラスト」の時など）。int でなければ None"""
    v = (hours() if conf is None else conf).get(name)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def biz_min(now):
    m = now.hour * 60 + now.minute
    return m + 24 * 60 if now.hour < CUT_H else m


def within(name, now=None, conf=None):
    """今がその時間帯か。決まりが開けなければ None"""
    sp = span(name, conf)
    if sp is None:
        return None
    m = biz_min(now or datetime.now(JST))
    return sp[0] <= m < sp[1]


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "within":
        w = within(a[1])
        if w is None:
            print("時間帯の決まり（鍵つき）が開けない")
            raise SystemExit(2)
        raise SystemExit(0 if w else 1)
    print("使い方: python3 ops_hours.py within <名前>")
    raise SystemExit(64)
