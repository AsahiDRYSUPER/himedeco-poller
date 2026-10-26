"""クラウド自動化の見張り（読むだけ）。止まっていたらスマホ（ntfy）へ知らせる。

なぜ: 非公開リポジトリの実行枠が尽きて自動化が止まっても気づけなかったことがあるので、見張りは無料で止まらないこの公開リポジトリに置く。
見るもの（名前を含まないファイルだけ）:
  ・裏姫デコ更新の生きている印（場所は鍵つき heaven_ids の "mypage_status_url"。無ければこの見張りは飛ばす）
  ・data ブランチの si9_status・sokuhime_status … 新着んの並び替え・即ヒメ直しの最終時刻と失敗
    （2026-10-25〜 鍵つき X.enc.json。鍵は金庫の TODO_KEY。移行のため enc が無ければ平文 X.json を読む）
    見る時間帯は鍵つきの時間帯の決まり（ops_sokuhime の "hours" の "watch"）。開けない時は見ずに「見られない」と知らせる
知らせ方: 異常の間は決まった時刻の回だけ送る（毎時は送らない）。手で動かした時はいつでも送る。
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import data_box
import ops_hours
import secret_box

JST = timezone(timedelta(hours=9))
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "AsahiDRYSUPER/himedeco-poller")
DATA_RAW = f"https://raw.githubusercontent.com/{REPO}/data"
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
    url = ((secret_box.data("heaven_ids") or {}).get("mypage_status_url") or "").strip()
    if not url:
        print("裏姫デコ更新: 見る場所（鍵つき）が無いので見ない")
        return []
    r = requests.get(url, params={"t": int(datetime.now().timestamp())}, timeout=30)
    r.raise_for_status()
    d = r.json()
    age = age_hours(d.get("updated_at"))
    at = datetime.fromisoformat(d["updated_at"].replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M") if age is not None else "?"
    print(f"裏姫デコ更新: 最終 {at} JST" + (f"（{age:.1f}時間前）" if age is not None else ""))
    probs = []
    if age is None or age > 15:
        probs.append(f"裏姫デコの更新が止まっている（最終 {at}）。GitHub Actions の実行枠（Billing）か、失敗ログを確認")
    elif d.get("ok") is False or (d.get("failed") or 0) >= 20:
        # 失敗の数は出さない（公開の場所に数を書かない）。中身は非公開側の作業記録で見る
        probs.append("裏姫デコの更新で失敗が多い。ヘブンがつながりにくい可能性")
    return probs


def watch_window():
    """新着ん・即ヒメ直しを見る時間か。決まり（鍵つき）が開けなければ例外（見られないと知らせる）"""
    w = ops_hours.within("watch")
    if w is None:
        raise RuntimeError("見る時間帯の決まり（鍵つき）が開けない")
    return w


def check_si9():
    if not watch_window():             # 見る時間帯の外は見ない（時間帯は鍵つき）
        return []
    state, d = data_box.fetch("si9_status", base=DATA_RAW, timeout=30)
    if state != "ok":
        raise RuntimeError(f"si9_status が読めない（{state}）")
    shops = (d or {}).get("shops") or {}
    recs = [v for v in shops.values() if isinstance(v, dict)]
    ats = [age_hours(v.get("at")) for v in recs if v.get("at")]
    ats = [a for a in ats if a is not None]
    newest = min(ats) if ats else None
    # 「読んだ時刻」だけでは、毎回失敗していても動いて見える（2026-10-23 新着んが丸1日止まっていたのに鳴らなかった）。失敗と、最後に送れた時刻も見る
    errs = [v for v in recs if v.get("error")]
    sent = [age_hours(v.get("sent_at")) for v in recs if v.get("sent_at")]
    sent = [a for a in sent if a is not None]
    last_sent = min(sent) if sent else None
    print(f"新着んの並び替え: 店{len(shops)}・最新の読み取り {newest:.2f}時間前・失敗している店 {len(errs)}・最後に送れたのは {last_sent:.1f}時間前" if newest is not None else "新着ん: 時刻が読めない")
    if newest is None or newest > 1.5:
        return ["新着んの並び替えが止まっている（90分以上更新なし）。himedeco-poller の board-collect を確認"]
    probs = []
    if recs and len(errs) == len(recs):
        kinds = sorted({str(v.get("error"))[:40] for v in errs})
        probs.append(f"新着んの並び替えが全店で失敗している（{'／'.join(kinds)[:120]}）。クラウドからのログインが拒否されていないか確認")
    if last_sent is None or last_sent > 4:
        probs.append(f"新着んへ4時間以上送れていない（最後 {last_sent:.1f}時間前）" if last_sent is not None else "新着んへ送れた記録が無い")
    return probs


def check_sokuhime():
    """即ヒメ直し（ヘブンの箱の時間付け）。生きている印 sokuhime_status の error と時刻を見る（名前なし）"""
    if not watch_window():
        return []
    state, d = data_box.fetch("sokuhime_status", base=DATA_RAW, timeout=30)
    if state != "ok":
        raise RuntimeError(f"sokuhime_status が読めない（{state}）")
    d = d or {}
    age = age_hours(d.get("at"))
    print(f"即ヒメ直し: 最終 {age:.2f}時間前・error={d.get('error') or '-'}" if age is not None else "即ヒメ直し: 時刻が読めない")
    if age is None or age > 1.5:
        return ["即ヒメ直しが止まっている（90分以上更新なし）。himedeco-poller の board-collect を確認"]
    if d.get("error"):
        return [f"即ヒメ直しが失敗している（{str(d.get('error'))[:60]}）。jp-sokuhime の作業記録を確認"]
    return []


def main():
    force = "--test" in sys.argv
    probs = []
    for fn in (check_mypage, check_si9, check_sokuhime):
        try:
            probs += fn()
        except Exception as e:
            # 自分で投げた理由（RuntimeError）だけは中身も出す（鍵や名前は入らない）
            probs.append(f"{fn.__name__} を確かめられなかった: {type(e).__name__}" + (f"（{str(e)[:60]}）" if type(e) is RuntimeError else ""))
    now = datetime.now(JST)
    if not probs:
        print("異常なし")
        if force:
            post_ntfy("見張り: 異常なし（手動の確認）", f"{now:%m/%d %H:%M} 裏姫デコ更新・新着ん・即ヒメ直しとも動いている", tags="white_check_mark")
        return 0
    print("異常:", *probs, sep="\n  ")
    if force or now.hour in ALERT_HOURS:
        post_ntfy("自動化が止まっている", f"{now:%m/%d %H:%M}\n" + "\n".join("・" + p for p in probs))
    else:
        print(f"知らせるのは {ALERT_HOURS} 時の回だけ（今は {now.hour} 時）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
