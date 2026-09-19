"""姫デコチャットの新着を5分ごとに確認する。

一覧は「新しい連絡があったトークが上」に並ぶので、各店舗の上位だけを見る。
・口コミ返信へのフィードバックらしいメッセージ → お礼を自動返信
・それ以外の未読 → 自動返信せず、件数だけ数える(内容は管理画面で確認する)

このリポジトリは公開なので、実行ログにキャスト名・メッセージ本文は一切出さない(件数のみ)。
状態ファイルも持たない: 「相手のメッセージより後に店側の送信があるか」で対応済みかを判定する。
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from heaven_http import BASE, HeavenClient, load_credentials

SHOPS = ["cg_kirakira", "s_matikado", "mrs_orange", "venus_okayama", "potya_reen", "torori_angel", "undercover"]
DRY_RUN = os.environ.get("REPLY_DRY_RUN", "true").lower() != "false"
VERIFY = os.environ.get("VERIFY_ORDER", "") == "1"
TOP_N = 15
ACK_WINDOW = timedelta(hours=3)
COUNT_WINDOW = timedelta(hours=48)
JST = timezone(timedelta(hours=9))

FEEDBACK_KEYWORDS = [
    "口コミ", "返信", "ai", "テンプレ", "不自然", "違和感",
    "冷たい", "雑", "機械的", "コピペ", "毎回同じ", "同じ文章",
]
ACK_MESSAGE = "ご報告ありがとうございます！確認して、口コミ返信の改善に活かします🙏"


def clean(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw or "")
    return re.sub(r"<[^>]+>", "", text).strip()


def looks_like_feedback(text: str) -> bool:
    return any(k in text.lower() for k in FEEDBACK_KEYWORDS)


def api(client, path, payload):
    r = client.s.post(
        BASE + path, json=payload, timeout=30,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/C8HimedecoChat.php?shopdir={client.shopdir}"},
    )
    return r.json()


def roster(client, shopdir):
    from bs4 import BeautifulSoup
    r = client._get(f"/C8HimedecoChat.php?shopdir={shopdir}")
    out = []
    for el in BeautifulSoup(r.text, "html.parser").select("div.accordion_header[data-src]"):
        m = re.search(r"girls_id=(\d+)", el.get("data-src", ""))
        if m:
            out.append(int(m.group(1)))
    return out


def talks(client, girls_id):
    res = api(client, "/himedecochat/api/new_list", {"girls_id": girls_id})
    return res.get("talk", []) if res.get("result") == 0 else []


def parse_dt(s):
    try:
        return datetime.strptime(s, "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)
    except Exception:
        return None


def main() -> int:
    now = datetime.now(JST)
    acked = escalated = failed = 0
    outside_recent = 0
    for shopdir in SHOPS:
        try:
            a, p, d = load_credentials(shopdir)
            client = HeavenClient(a, p, direct=d)
            client.login_and_select(shopdir)
            ids = roster(client, shopdir)
            for i, gid in enumerate(ids):
                in_top = i < TOP_N
                if not in_top and not VERIFY:
                    break
                ts = talks(client, gid)
                if not in_top:
                    if any((parse_dt(t.get("create_date", "")) or datetime.min.replace(tzinfo=JST)) > now - timedelta(hours=24) for t in ts):
                        outside_recent += 1
                    continue
                last_shop = max((t.get("id", 0) for t in ts if t.get("sent_from_flg") != 2), default=0)
                for t in ts:
                    if t.get("sent_from_flg") != 2 or t.get("opened_flg") != 0 or t.get("id", 0) < last_shop:
                        continue
                    when = parse_dt(t.get("create_date", ""))
                    if not when or now - when > COUNT_WINDOW:
                        continue
                    text = clean(t.get("body", ""))
                    if not text:
                        continue
                    if looks_like_feedback(text):
                        if now - when <= ACK_WINDOW and not DRY_RUN:
                            res = api(client, "/himedecochat/api/send_message", {"talk_message": ACK_MESSAGE, "girls_id": gid})
                            acked += res.get("result") == 0
                            last_shop = 10**12
                    else:
                        escalated += 1
        except Exception as e:
            print(f"  ! {shopdir}: 処理に失敗しました({type(e).__name__})")
            failed += 1
    print(f"自動返信: {acked}件 / 人の確認待ち: {escalated}件 / 失敗した店舗: {failed}")
    if VERIFY:
        print(f"上位{TOP_N}件より下で直近24時間に動きがあったトーク: {outside_recent}件")
    Path("status.json").write_text(
        json.dumps({"escalated": escalated, "failed_shops": failed}, ensure_ascii=False), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    # 1時間の定期実行の中で、5分おきに繰り返す(GitHubの5分おき定期実行は取りこぼしが多いため)
    loop_min = int(os.environ.get("LOOP_MINUTES", "0") or 0)
    if loop_min <= 0:
        raise SystemExit(main())
    end = time.time() + loop_min * 60
    code = 0
    while True:
        code = main()
        if time.time() + 300 >= end:
            break
        time.sleep(300)
    raise SystemExit(code)
