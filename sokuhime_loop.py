"""即ヒメ直しを、1回の実行（日本の実行機）の中でくり返す。

- 間隔は鍵つきの決まり（sealed/ops_sokuhime.enc の "pace"）：busy の時間帯は busy_every_min 分ごと、それ以外は every_min 分ごと。
  開けない時はふだんの間隔（DEFAULT）で回す（直すかどうかは sokuhime_fix.py が決まりを見て決める。開けなければ直さない）
- 新着んの並び替え（si9_sort.py）は、1回目の即ヒメ直しのあとに横で1回だけ回す（即ヒメ直しが残す CTI の控えを使う）
- LOOP_SEC（既定 540秒）を過ぎる前に終わる。次の実行は workflow の最後で自分で起動する（Mac の起動係は止まった時の予備）
- ログインの数は1回ごとに今までの即ヒメ直しと同じ。間隔を2分より短くはしない
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
LOOP_SEC = int(os.environ.get("LOOP_SEC", "540") or 540)
DRY = os.environ.get("SOKUHIME_DRY", "") == "1"
SKIP_SI9 = os.environ.get("SKIP_SI9", "") == "1"
FIX_TIMEOUT = 300
MIN_EVERY = 2
DEFAULT = {"busy": [], "busy_every_min": 5, "every_min": 5}   # 決まりが開けない時（忙しい時間帯なし）
UNIT = int(os.environ.get("PACE_UNIT_SEC", "60") or 60)   # 1分の長さ（試しの時だけ短くする）
SI9_LOG = os.path.join(os.environ.get("RUNNER_TEMP") or "/tmp", "si9_sort_out.txt")
_warned = []


def _min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def pace(now, conf=None):
    """今の間隔（分）と、忙しい時間帯か。翌0〜5時は 24〜29時として比べる"""
    if conf is None:
        import secret_box
        conf = (secret_box.data("ops_sokuhime") or {}).get("pace") or {}
        if not conf and not _warned:
            _warned.append(1)
            print("即ヒメ直しの間隔の決まり（鍵つき）が開けないので、ふだんの間隔で回す", flush=True)
    c = {**DEFAULT, **{k: v for k, v in conf.items() if not k.startswith("_")}}
    m = now.hour * 60 + now.minute
    if m < 6 * 60:
        m += 24 * 60
    busy = any(_min(a) <= m < _min(b) for a, b in c["busy"])
    every = int(c["busy_every_min"] if busy else c["every_min"])
    return max(MIN_EVERY, every), busy


def main():
    t_begin = time.time()
    t_end = t_begin + LOOP_SEC
    si9 = None
    n = 0
    while True:
        t0 = time.time()
        n += 1
        now = datetime.now(JST)
        every, busy = pace(now)
        print(f"=== 即ヒメ直し {n}回目 {now.strftime('%H:%M:%S')}（{'忙しい時間帯' if busy else 'ふだん'}・{every}分ごと）", flush=True)
        try:
            subprocess.run([sys.executable, "sokuhime_fix.py"] + (["--dry"] if DRY else []), timeout=FIX_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"即ヒメ直しが{FIX_TIMEOUT // 60}分で終わらず打ち切り", flush=True)
        except Exception as e:
            print("即ヒメ直しに失敗:", type(e).__name__, flush=True)
        if si9 is None and not SKIP_SI9:
            # 新着んは横で1回だけ。出力はあとでまとめて出す（即ヒメの出力と混ざらないように）
            si9 = subprocess.Popen([sys.executable, "si9_sort.py"], stdout=open(SI9_LOG, "w"), stderr=subprocess.STDOUT)
        nxt = t0 + every * UNIT
        # 次の回の即ヒメ直し（ふつう1〜2分）が終わりまでに収まらなければ、ここで終える（次の実行がすぐ続きをする）
        if nxt + 2 * UNIT > t_end:
            break
        while time.time() < nxt:
            time.sleep(2)
    if si9 is not None:
        try:
            si9.wait(timeout=max(30, t_end + 4 * UNIT - time.time()))
        except subprocess.TimeoutExpired:
            si9.kill()
            print("新着んの並び替えが長すぎるので打ち切った", flush=True)
        try:
            with open(SI9_LOG, encoding="utf-8", errors="replace") as f:
                print(f.read().rstrip(), flush=True)
        except Exception:
            pass
    print(f"即ヒメ直し {n}回・{int(time.time() - t_begin)}秒", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
