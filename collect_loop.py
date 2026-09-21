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
        subprocess.run([sys.executable, "watch_chat.py"], timeout=240)
    except Exception as e:
        print("姫デコチャットの見張りに失敗:", type(e).__name__)
    if r.returncode == 0:
        publish()
    if time.time() + INTERVAL > end:
        break
    time.sleep(max(5, INTERVAL - (time.time() - t0)))
