"""collect_shops.py を5分おきに繰り返し、そのたびに公開データ(dataブランチ)へ反映する。
GitHubの定期実行は数十分遅れることがあるため、1回の実行の中で何度も回して、5分ごとの更新を保つ。"""
import json
import os
import re
import subprocess
import sys
import time

LOOP_MIN = int(os.environ.get("LOOP_MINUTES", "0") or 0)
INTERVAL = 300

# 夜遊びヘブンの「来た時だけ」（2026-10-23 一希さん「夜遊びは来た時だけ」）
# 夜遊びの管理画面の通知メールを ntfy の受け口（メールで投稿できる宛先 ntfy-<topic>@ntfy.sh）へ送ってもらい、
# 5分の待ち時間のあいだ、その受け口を聞いている。メールが届いた瞬間に yoasobi_watch.py を回す。
# 受け口の名前は YOASOBI_MAIL_TOPIC、無ければ「いつもの通知先 + -mail」。名前はログに出さない。
MAIL_TOPIC = (os.environ.get("YOASOBI_MAIL_TOPIC") or "").strip() or (
    (os.environ.get("NTFY_TOPIC") or "").strip() + "-mail" if (os.environ.get("NTFY_TOPIC") or "").strip() else "")
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
# 念のための見回りの間隔（何回に1回か）。既定は今まで通り 2回に1回＝10分ごと。受け口にメールが届くのを確かめたら、
# リポジトリの変数 YOASOBI_SWEEP_CYCLES=12（約1時間ごと）にする
SWEEP = int(os.environ.get("YOASOBI_SWEEP_CYCLES", "") or 2)
_mail_since = int(time.time())      # 最初は今の時刻、以後はメッセージID
_mail_seen = set()


def wait_mail(seconds):
    """受け口を seconds 秒まで聞く。メールが来たら True。来なければ時間切れで False（その間は眠るのと同じ）。"""
    global _mail_since
    seconds = int(seconds)
    if not MAIL_TOPIC or seconds <= 3:
        time.sleep(max(0, seconds))
        return False
    import requests
    t0 = time.time()
    try:
        # since は「最後に受け取ったメッセージのID」（時刻だと同じメッセージが何度も返る。2026-10-23 1時間空回りした）
        r = requests.get(f"{NTFY_HOST}/{MAIL_TOPIC}/json", params={"since": _mail_since}, stream=True, timeout=(15, seconds))
        for line in r.iter_lines():
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("event") == "message" and d.get("id"):
                if d["id"] in _mail_seen:          # since=ID は同じメッセージも返すことがあるので、見たIDは飛ばす
                    continue
                _mail_seen.add(d["id"])
                _mail_since = d["id"]
                r.close()
                # 通知メールの本文に shop?id=<店のcommuId> が入っている → どの店か分かる（分からなければ全部の店）
                m = re.search(r"[?&]id=(\d{4,})", (d.get("message") or "") + " " + (d.get("title") or ""))
                return {"shop_id": m.group(1) if m else ""}
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
        # 待ち時間いっぱいで切れたのは正常（流しながら読む時は ConnectionError の形で来る）
        if "timed out" not in str(e).lower() and time.time() - t0 < seconds - 5:
            print("夜遊びの受け口の待ち受けで失敗:", type(e).__name__)
            time.sleep(min(60, max(1, seconds - (time.time() - t0))))
    except Exception as e:
        print("夜遊びの受け口の待ち受けで失敗:", type(e).__name__)
        time.sleep(min(60, seconds))
    return False


def _shopdir_of(shop_id):
    """店のcommuId → shopdir。見張りの店の中に無ければ空（＝全部の店を見る）"""
    try:
        from heaven_http import COMMU_IDS
        for k, v in COMMU_IDS.items():
            if str(v) == str(shop_id):
                return k
    except Exception:
        pass
    return ""


def yoasobi_watch(reason, shop_id=""):
    try:
        env = dict(os.environ)
        sd = _shopdir_of(shop_id) if shop_id else ""
        if sd:
            env["YOASOBI_SHOPS"] = sd         # メールで分かった店だけを見に行く（速く返すため）
        print("夜遊びの見張り:", reason, f"（店 {sd}）" if sd else "")
        subprocess.run([sys.executable, "yoasobi_watch.py"], timeout=600, env=env)
    except Exception as _e:
        print("夜遊びの見張りで失敗:", type(_e).__name__)


def publish():
    r = subprocess.run(["bash", "publish_data.sh"])
    print("公開:", "OK" if r.returncode == 0 else f"失敗({r.returncode})")


end = time.time() + LOOP_MIN * 60
_cycle = 0
while True:
    t0 = time.time()
    _cycle += 1
    # ヘブンが重い日に返事待ちで固まらないよう、ここにも時間の上限（2026-10-22 18:37 の回が2時間以上止まり、見張り全体が止まった）
    try:
        r = subprocess.run([sys.executable, "collect_shops.py"], timeout=900)
    except subprocess.TimeoutExpired:
        print("ボードの集計が15分で終わらず打ち切り")
        r = subprocess.CompletedProcess(args=[], returncode=1)
    # 姫デコチャットの見張り(新しい連絡があればスマホへ通知)。ここで失敗しても、ボードのデータ更新は止めない
    try:
        subprocess.run([sys.executable, "watch_chat.py"], timeout=900)   # 出勤を上げる時はブラウザ操作で数分かかる
        # 夜遊びヘブンの多言語チャットの念のための見回り（受け口がある時は約1時間ごと、無い時は10分ごと。2026-10-23）
        if os.environ.get("YOASOBI_IN_LOOP", "on").lower() not in ("off", "0", "false") and (_cycle % SWEEP == 0):
            yoasobi_watch("見回り")
    except Exception as e:
        print("姫デコチャットの見張りに失敗:", type(e).__name__)
    # 即ヒメの「待機中なのに本当は接客中」を直す（毎回＝約5分おき。一希さん 10/10。失敗してもボードの更新は止めない）
    if os.environ.get("CTI_PASSWORD"):
        try:
            subprocess.run([sys.executable, "sokuhime_fix.py"], timeout=600)
        except Exception as e:
            print("即ヒメ直しに失敗:", type(e).__name__)
    if r.returncode == 0:
        publish()
    # 新着んの並び替え（8:00〜翌2:00。並びが変わった時だけ、10分以上あけて7サイトへ送る。一希さん 10/18）。失敗してもボードの更新は止めない。
    # 切り替えは si9_sort.py が main の si9_mode.json を毎回読む。保存・送信をした回は印（out/si9_changed）を残すので、その記録をすぐ公開し直す
    if os.environ.get("SINCHAKUN_ID") and time.time() + 480 < end + INTERVAL:
        try:
            subprocess.run([sys.executable, "si9_sort.py"], timeout=480)
        except Exception as e:
            print("新着んの並び替えに失敗:", type(e).__name__)
        if os.path.exists("out/si9_changed"):
            os.remove("out/si9_changed")
            publish()
    if time.time() + INTERVAL > end:
        break
    # 次の回までの待ち時間は、夜遊びの受け口を聞きながら待つ（メールが来たらすぐ見に行き、残りをまた待つ）
    while True:
        remain = INTERVAL - (time.time() - t0)
        if remain <= 5:
            break
        got = wait_mail(remain)
        if got and os.environ.get("YOASOBI_IN_LOOP", "on").lower() not in ("off", "0", "false"):
            yoasobi_watch("メールの合図", (got or {}).get("shop_id", "") if isinstance(got, dict) else "")
