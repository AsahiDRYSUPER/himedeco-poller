"""
CityHeaven管理画面（ヘブンマネージャー）の口コミに、自動で返信するスクリプト。

仕組み: 実行のたびに金庫（GitHub Secrets）のログイン情報で管理画面に入り直し（heaven_http.py）、
    口コミ一覧から「返信がまだ無い口コミ」を探して返信する。
返す・返さないの決まり、言葉の一覧、返信の文の型は鍵つき（sealed/ops_reviews.enc、正本は非公開側）。
    開けない時は自動返信をしないで、要確認に回してスマホ（ntfy）に知らせるだけ。
使い方:
    python3 reply_reviews.py
    → REPLY_DRY_RUN=true（デフォルト）は確認だけ。REPLY_DRY_RUN=false で実際に投稿する
未返信の探し方:
    管理画面の口コミ一覧（新しい順・1ページ目）のうち、返信欄が「済」でなく、状態が「未読」または「表示」のもの。
"""

import functools
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from heaven_http import COMMU_IDS, HeavenClient, HeavenError, LoginError, load_credentials
import secret_box   # 決まり・言葉・文の型は鍵つき（2026-10-25）
import data_box     # 状態（reply_status）は鍵つきで dataブランチへ（2026-10-26）

_print = functools.partial(print, flush=True)
_SAFE = ("モード", "===", "未返信", "  → 投稿しました", "失敗した店舗", "全店舗", "  ! ログイン", "  ! 口コミ一覧")


def print(*args, **kwargs):
    """このリポジトリは公開なので、口コミ本文・返信文・キャスト名は絶対にログへ出さない。
    件数と店舗名だけを通し、口コミIDも伏せる。"""
    text = " ".join(str(a) for a in args)
    if not text.strip():
        return
    if text.startswith(_SAFE):
        _print(re.sub(r"review_id=\d+", "review_id=***", text)[:160], **kwargs)

OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))

SHOPS = {
    "cg_kirakira": "キラキラ学園",
    "s_matikado": "街角レディ",
    "mrs_orange": "オレンジな気持ち",
    "venus_okayama": "VENUS",
    "potya_reen": "ぽちゃりーん",
    "torori_angel": "とろ〜りAngel",
    "undercover": "UNDERCOVER",
}

# 本当に投稿するかどうか。手動実行時は必ずTrue(確認のみ)がデフォルト。
# 定期実行(自律実行)の時だけ、環境変数 REPLY_DRY_RUN=false を渡して本番投稿する。
DRY_RUN = os.environ.get("REPLY_DRY_RUN", "true").lower() != "false"

# 安全のため、1回の実行で処理する件数の上限(店舗ごと)
MAX_PER_SHOP = 30


# ---------------------------------------------------------------------------
# 口コミ本文から、返信で触れる一文を拾う
# ---------------------------------------------------------------------------

def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# 決まり・言葉の一覧・文の型（鍵つき sealed/ops_reviews.enc）。開けない時は空＝自動返信しない
_OPS = secret_box.data("ops_reviews") or {}
_NO_COMPLAINT_TEXTS = set(_OPS.get("no_complaint_texts") or ())
NEGATIVE_KEYWORDS = list(_OPS.get("negative_keywords") or ())


def ops_ready() -> bool:
    """決まり（鍵つき）が開けているか。言葉の一覧・点数の線・文の型がそろっている時だけ True"""
    return bool(NEGATIVE_KEYWORDS and SERIOUS_KEYWORDS and isinstance(LOW_RATING_THRESHOLD, (int, float))
                and isinstance(HIGH_RATING_FROM, (int, float)) and REPLY_LAYOUTS
                and all(isinstance(_OPS.get(k), dict) for k in ("high", "mid", "mid_complaint")))


def has_real_complaint(review: dict) -> bool:
    """口コミに実際の不満点が書かれているかを判定する（評価点の数字だけに頼らない。言葉の一覧は鍵つき）。"""
    ngpoint = _clean_text(review.get("review_shop_ngpoint", ""))
    if ngpoint and ngpoint not in _NO_COMPLAINT_TEXTS:
        return True
    fields = [review.get(k, "") for k in ["title", "review_flow", "review_girl", "review_play", "review_tatal"]]
    full_text = _clean_text(" ".join(fields))
    return any(kw in full_text for kw in NEGATIVE_KEYWORDS)


_PROFILE_CLAIM_KEYWORDS = list(_OPS.get("profile_claim_keywords") or ())


def _looks_like_profile_claim(text: str) -> bool:
    """引用すると誤った情報を返信で肯定してしまう種類の記述かどうか（言葉の一覧は鍵つき）。引用の候補から外す。"""
    return any(kw in text for kw in _PROFILE_CLAIM_KEYWORDS)


def extract_highlight(review: dict) -> str:
    """口コミ本文の中から、返信で具体的に引用できる一文を探す。見つからなければ空文字。"""
    candidates = [
        review.get("review_tatal", ""),
        review.get("review_play", ""),
        review.get("review_girl", ""),
        review.get("review_shop_goodpoint", ""),
        review.get("title", ""),
    ]
    for raw in candidates:
        text = _clean_text(raw)
        if not text:
            continue
        parts = re.split(r"[。！\n]", text)
        for part in parts:
            part = part.strip(" 　♥️🍴🙏💕❤️~〜♪☆★!！")
            if 8 <= len(part) <= 45 and not _looks_like_profile_claim(part):
                return part
    return ""


# ---------------------------------------------------------------------------
# 返信文の生成（文の型は鍵つき。評価帯ごとに複数の型を持ち、review_id を種にして選ぶ＝再実行しても同じ文）
# ---------------------------------------------------------------------------

REPLY_LAYOUTS = list(_OPS.get("layouts") or ())


def _pick(rnd, xs):
    """型を1つ選ぶ。1つしか無い時は乱数を使わない（選び方の順番を前と同じに保つ）"""
    xs = list(xs or ())
    if not xs:
        return ""
    return xs[0] if len(xs) == 1 else rnd.choice(xs)


def build_reply(shop: str, cast: str, rating: float, repeat: bool, highlight: str, seed: int, has_complaint: bool = False) -> str:
    """口コミ本文を踏まえた返信文を作る。highlight が取れた場合はそれに触れる。
    文面だけでなく段落の組み立て方も review_id ごとに変える。"""
    if not ops_ready():
        raise ValueError("返信の決まり（鍵つき）が開けない")
    if rating < LOW_RATING_THRESHOLD:
        raise ValueError(f"build_reply()は自動返信しない点数の口コミを想定していません(rating={rating})")
    rnd = random.Random(seed)
    cast = cast or _OPS.get("default_cast") or ""
    layout = rnd.randint(1, len(REPLY_LAYOUTS))
    band = "high" if rating >= HIGH_RATING_FROM else ("mid_complaint" if has_complaint else "mid")
    t = _OPS[band]
    if "greeting_repeat" in t or "greeting_first" in t:
        greeting = _pick(rnd, t.get("greeting_repeat") if repeat else t.get("greeting_first"))
    else:
        greeting = _pick(rnd, t.get("greeting"))
    reaction = _pick(rnd, t.get("reaction_quote") if highlight else t.get("reaction_plain"))
    warmth = _pick(rnd, t.get("warmth"))
    invite = _pick(rnd, t.get("invite"))
    vals = {"shop": shop, "cast": cast, "highlight": highlight}
    parts = {k: v.format(**vals) for k, v in (("greeting", greeting), ("reaction", reaction), ("warmth", warmth), ("invite", invite))}
    return REPLY_LAYOUTS[layout - 1].format(**parts)


# ---------------------------------------------------------------------------
# エスカレーション判定(意思決定の自動化: 深刻な内容は自動返信せず人に回す)
# ---------------------------------------------------------------------------

# ここに引っかかる口コミ・点数の線より下の口コミは、自動返信せずに「要確認」として残す（一覧と線は鍵つき）。
SERIOUS_KEYWORDS = list(_OPS.get("serious_keywords") or ())
LOW_RATING_THRESHOLD = _OPS.get("low_rating_threshold")
HIGH_RATING_FROM = _OPS.get("high_rating_from")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
_notified = []


def notify_ops_missing(n):
    """決まり（鍵つき）が開けず、未返信の口コミを自動で返せなかった時に、スマホへ1回だけ知らせる（中身は入れない）"""
    if _notified or not n:
        return
    _notified.append(1)
    if not NTFY_TOPIC:
        _print("口コミ: 通知先が未設定なので知らせない")
        return
    try:
        import requests
        requests.post(f"{NTFY_HOST}/{NTFY_TOPIC}", data=f"未返信の口コミ {n}件を自動で返していません。金庫の KNOWLEDGE_KEY を確かめ、口コミは手で返してください".encode("utf-8"),
                      headers={"Title": "口コミの自動返信: 決まりが開けない".encode("utf-8"), "Tags": b"warning", "Priority": b"high"}, timeout=20).raise_for_status()
        _print("口コミ: 決まりが開けないことをスマホに知らせた")
    except Exception as e:
        _print("口コミ: 知らせられなかった", type(e).__name__)


def flag_reason(review: dict) -> str:
    """深刻な内容が含まれていそうなら、その理由(引っかかったキーワード)を返す。無ければ空文字。"""
    fields = [
        review.get("title", ""),
        review.get("review_flow", ""),
        review.get("review_girl", ""),
        review.get("review_play", ""),
        review.get("review_tatal", ""),
        review.get("review_shop_goodpoint", ""),
        review.get("review_shop_ngpoint", ""),
        review.get("body", ""),
    ]
    full_text = _clean_text(" ".join(fields))
    for kw in SERIOUS_KEYWORDS:
        if kw in full_text:
            return kw
    return ""


# ---------------------------------------------------------------------------
# 口コミの取得・返信投稿(管理画面へHTTPで直接アクセス)
# ---------------------------------------------------------------------------

def process_review(client: HeavenClient, shopdir: str, item: dict, shop_label: str, flagged: list):
    review_id = item["review_id"]
    try:
        rating = float(item["rate"])
    except ValueError:
        rating = 5.0

    detail = client.get_detail(review_id)
    review = {
        "review_id": review_id,
        "title": detail.get("タイトル", ""),
        "review_flow": detail.get("受付からプレイ開始までの流れ", ""),
        "review_girl": detail.get("お相手の女性について", ""),
        "review_play": detail.get("プレイ内容", ""),
        "review_tatal": detail.get("今回の総評", ""),
        "review_shop_goodpoint": detail.get("お店のいいところ", ""),
        "review_shop_ngpoint": detail.get("お店の改善してほしいところ", "") or detail.get("お店の悪いところ", ""),
        "body": "",
    }
    info = {**review, "rate": rating, "visit_girl": item["girl"], "shop_label": shop_label, "shopdir": shopdir}

    if not ops_ready():
        flagged.append({**info, "flag_reason": "決まり（鍵つき）が開けない"})
        return "flagged"
    if rating < LOW_RATING_THRESHOLD:
        print(f"  ⚠ review_id={review_id} を要確認としてスキップします(点数: {rating}点)。自動返信しません。")
        flagged.append({**info, "flag_reason": f"低評価({rating}点)"})
        return "flagged"
    reason = flag_reason(review)
    if reason:
        print(f"  ⚠ review_id={review_id} を要確認としてスキップします(キーワード:「{reason}」)。自動返信しません。")
        flagged.append({**info, "flag_reason": reason})
        return "flagged"

    cast_name = _clean_text(detail.get("遊んだ女の子", "")).split("【")[0].strip()
    usage = detail.get("お店の利用回数", "")
    repeat = not ("初" in usage or usage.startswith("1回"))
    highlight = extract_highlight(review)
    complaint = has_real_complaint(review)
    reply_text = build_reply(shop_label, cast_name, rating, repeat, highlight, seed=int(review_id), has_complaint=complaint)

    print(f"--- [{shop_label}] review_id={review_id} 評価={rating} 相手={cast_name} 引用元={'あり' if highlight else 'なし'} 不満点={'あり' if complaint else 'なし'} ---")
    print(reply_text)
    print()

    if DRY_RUN:
        return "dry"

    client.post_reply(review_id, reply_text)
    if not client.is_replied(review_id):
        raise HeavenError("投稿後に返信済みになっていることを確認できませんでした")
    print(f"  → 投稿しました(review_id={review_id})")
    return "posted"


def process_shop(shopdir: str, shop_label: str, flagged: list, stat: dict) -> bool:
    try:
        account, password, direct = load_credentials(shopdir)
        client = HeavenClient(account, password, direct=direct)
        client.login_and_select(shopdir)
        items = [
            r for r in client.list_reviews()
            if not r["replied"] and r["status"] in ("未読", "表示")
        ][:MAX_PER_SHOP]
    except LoginError as e:
        print(f"  ! ログインできませんでした: {e}")
        print("    ログイン情報(GitHub Secrets)が正しいか確認してください。")
        stat["error"] = "ログインできませんでした"
        return False
    except Exception as e:
        print(f"  ! 口コミ一覧の取得に失敗しました: {e}")
        stat["error"] = "口コミ一覧を取得できませんでした"
        return False

    print(f"未返信: {len(items)}件")
    stat["found"] = len(items)
    succeeded = True
    for item in items:
        try:
            outcome = process_review(client, shopdir, item, shop_label, flagged)
            if outcome == "posted":
                stat["posted"] += 1
            elif outcome == "flagged":
                stat["flagged"] += 1
        except Exception as e:
            print(f"  ! review_id={item['review_id']} の処理に失敗しました: {e}")
            stat["failed"] += 1
            succeeded = False
        time.sleep(0.5)
    return succeeded


def main():
    mode = "確認のみ(DRY RUN)" if DRY_RUN else "実際に投稿します"
    print(f"モード: {mode}")
    print()
    if not ops_ready():
        _print("口コミ: 返信の決まり（鍵つき）が開けないので自動返信しない（未返信は要確認に回して知らせるだけ）")

    flagged = []
    failed = []
    shops_stat = {}
    for shopdir, shop_label in SHOPS.items():
        print(f"=== {shop_label} ({shopdir}) を確認中 ===")
        stat = {"label": shop_label, "found": 0, "posted": 0, "flagged": 0, "failed": 0, "error": ""}
        shops_stat[shopdir] = stat
        if not process_shop(shopdir, shop_label, flagged, stat):
            failed.append(shop_label)
        print()

    if not ops_ready():
        notify_ops_missing(sum(v["found"] for v in shops_stat.values()))
    posted = sum(v["posted"] for v in shops_stat.values())
    _print(f"返信した: {posted}件 / 要確認(自動返信を見送り): {len(flagged)}件 / 失敗した店舗: {len(failed)}")
    # 状態（件数）は鍵つきで dataブランチの reply_status.enc.json へ（公開の場所に数を平文で置かない。main にはコミットしない。2026-10-26）
    data_box.write("reply_status", {"flagged_open": len(flagged), "failed_shops": len(failed),
                                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    **({"rules_missing": True} if not ops_ready() else {})}, OUT_DIR)

    if failed:
        print("失敗した店舗: " + "、".join(failed))
        print("全店舗の確認は完了していません。エラー内容を確認してください。")
        return 1
    print("全店舗の処理が正常に完了しました。")
    return 0


if __name__ == "__main__":
    loop_min = int(os.environ.get("LOOP_MINUTES", "0") or 0)
    if loop_min <= 0:
        raise SystemExit(main())
    end = time.time() + loop_min * 60
    code = 0
    while True:
        code = main()
        if time.time() + 600 >= end:
            break
        time.sleep(600)
    raise SystemExit(code)
