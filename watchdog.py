"""クラウド自動化の見張り（読むだけ）。止まっていたらスマホ（ntfy）へ知らせる。

なぜ: 2026-10-20、非公開リポジトリ cast-mypage の実行時間の枠（月の上限）を使い切り、裏姫デコの更新・送迎ボード・
      CTIの読み取りが朝から止まっていたのに、女の子に言われるまで誰も気づかなかった。アプリ側の毎朝の確認は、
      PCを閉じていると動かない。だから見張りは、無料で止まらないこの公開リポジトリに置く。
見るもの（名前を含まない公開のファイルだけ）:
  ・https://cast-mypage.pages.dev/updated.json … 裏姫デコ更新の最終時刻（1日2回。15時間以上空いたら異常）
  ・data ブランチの si9_status.json … 新着んの並び替えの最終時刻（営業時間は10分おき。90分以上空いたら異常）
知らせ方: 異常の間は JST 9・13・17・21時の回だけ送る（毎時は送らない）。手で動かした時はいつでも送る。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

JST = timezone(timedelta(hours=9))
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "AsahiDRYSUPER/himedeco-poller")
ALERT_HOURS = (9, 13, 17, 21)


def post_ntfy(title, body, tags="rotating_light", click="https://github.com/AsahiDRYSUPER/cast-mypage/actions"):
    if not NTFY_TOPIC:
        print("通知先が未設定（NTFY_TOPIC）。送らない:", title)
        return
    h = {"Title": title.encode("utf-8"), "Tags": tags.encode("utf-8"), "Click": click.encode("utf-8"), "Priority": b"high"}
    try:
        requests.post(f"{NTFY_HOST}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=h, timeout=20).raise_for_status()
        print("通知を送った:", title)
    except Exception as e:
        print("通知を送れなかった:", type(e).__name__)


def age_hours(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def check_mypage():
    r = requests.get("https://cast-mypage.pages.dev/updated.json", params={"t": int(datetime.now().timestamp())}, timeout=30)
    r.raise_for_status()
    d = r.json()
    age = age_hours(d.get("updated_at"))
    at = datetime.fromisoformat(d["updated_at"].replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M") if age is not None else "?"
    print(f"裏姫デコ更新: 最終 {at} JST（{age:.1f}時間前） 失敗 {d.get('failed')}/{d.get('total')}")
    probs = []
    if age is None or age > 15:
        probs.append(f"裏姫デコの更新が止まっている（最終 {at}）。GitHub Actions の実行枠（Billing）か、失敗ログを確認")
    elif (d.get("failed") or 0) >= 20:
        probs.append(f"裏姫デコの更新で失敗が多い（{d.get('failed')}/{d.get('total')}人）。ヘブンがつながりにくい可能性")
    return probs


def check_si9():
    now = datetime.now(JST)
    h = now.hour
    if not (h >= 9 or h < 2):          # 営業時間（8:00〜翌2:00）の外は見ない。8時台は動き始めなので見ない
        return []
    r = requests.get(f"https://raw.githubusercontent.com/{REPO}/data/si9_status.json", params={"t": int(now.timestamp())}, timeout=30)
    r.raise_for_status()
    shops = (r.json() or {}).get("shops") or {}
    ats = [age_hours(v.get("at")) for v in shops.values() if isinstance(v, dict) and v.get("at")]
    ats = [a for a in ats if a is not None]
    newest = min(ats) if ats else None
    print(f"新着んの並び替え: 店{len(shops)}・最新の読み取り {newest:.2f}時間前" if newest is not None else "新着ん: 時刻が読めない")
    if newest is None or newest > 1.5:
        return ["新着んの並び替えが止まっている（90分以上更新なし）。himedeco-poller の board-collect を確認"]
    return []


def main():
    force = "--test" in sys.argv
    probs = []
    for fn in (check_mypage, check_si9):
        try:
            probs += fn()
        except Exception as e:
            probs.append(f"{fn.__name__} を確かめられなかった: {type(e).__name__}")
    now = datetime.now(JST)
    if not probs:
        print("異常なし")
        if force:
            post_ntfy("見張り: 異常なし（手動の確認）", f"{now:%m/%d %H:%M} 裏姫デコ更新・新着んとも動いている", tags="white_check_mark")
        return 0
    print("異常:", *probs, sep="\n  ")
    if force or now.hour in ALERT_HOURS:
        post_ntfy("自動化が止まっている", f"{now:%m/%d %H:%M}\n" + "\n".join("・" + p for p in probs))
    else:
        print(f"知らせるのは {ALERT_HOURS} 時の回だけ（今は {now.hour} 時）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
