"""collect_shops.py を5分おきに繰り返し、そのたびに公開データ(dataブランチ)へ反映する。
GitHubの定期実行は数十分遅れることがあるため、1回の実行の中で何度も回して、5分ごとの更新を保つ。"""
import os
import subprocess
import sys
import time

LOOP_MIN = int(os.environ.get("LOOP_MINUTES", "0") or 0)
INTERVAL = 300


def publish():
    r = subprocess.run(["bash", "publish_data.sh"])
    print("公開:", "OK" if r.returncode == 0 else f"失敗({r.returncode})")


end = time.time() + LOOP_MIN * 60
while True:
    t0 = time.time()
    r = subprocess.run([sys.executable, "collect_shops.py"])
    # 姫デコチャットの見張り(新しい連絡があればスマホへ通知)。ここで失敗しても、ボードのデータ更新は止めない
    try:
        subprocess.run([sys.executable, "watch_chat.py"], timeout=900)   # 出勤を上げる時はブラウザ操作で数分かかる
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
            subprocess.run([sys.executable, "si9_sort.py"], timeout=420)
        except Exception as e:
            print("新着んの並び替えに失敗:", type(e).__name__)
        if os.path.exists("out/si9_changed"):
            os.remove("out/si9_changed")
            publish()
    if time.time() + INTERVAL > end:
        break
    time.sleep(max(5, INTERVAL - (time.time() - t0)))
