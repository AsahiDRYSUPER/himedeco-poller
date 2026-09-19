"""
CityHeaven管理画面(ヘブンマネージャー)の口コミに、自動で返信するスクリプト。

仕組み(2026-09-19 全面変更):
    ブラウザ(Playwright)も、保存したログイン情報(auth_state)も使わない。
    実行のたびに、GitHub Secretsに登録したグループIDのID・パスワードで
    CityHeavenへ自分でログインし直し(heaven_http.py)、管理画面の口コミ一覧から
    「返信がまだ無い口コミ」を直接探して返信する。
    そのため「ログインセッションが数時間で切れて自動返信が止まる」問題が起きない。
    (以前の公式API経由の検出は、新着口コミがAPIに載るまで遅れる問題があったため廃止)

必要な環境変数(GitHub Secretsから渡す):
    CITYHEAVEN_ACCOUNT   グループIDのログインID
    CITYHEAVEN_PASSWORD  そのパスワード

使い方:
    python3 reply_reviews.py
    → まずは REPLY_DRY_RUN=true(デフォルト)のまま実行して、投稿予定の返信文を確認する
    → 内容に問題なければ REPLY_DRY_RUN=false で再実行すると実際に投稿される

返信文について(2026-09-17改訂):
    以前は評価点だけで3パターンの定型文を使い回していたが、「AI感がある」「雑」と
    キャストから指摘があったため、口コミ本文(review_girl/review_play/review_tatal等)
    から実際に書かれている内容を1文引用し、それに触れる返信になるよう変更した。
    あわせて、同じ評価帯でも複数の言い回しをランダムに使い分けて、毎回同じ文面に
    ならないようにしている(review_idを乱数シードにして再実行時も同じ文面になる)。

評価が低い口コミ(3.0点未満)の扱い(2026-09-17変更):
    自動返信はせず、要確認_口コミ.md に一覧化するだけにする。定型文で済ませず、
    本人が内容を読んで個別に対応する方針のため(指示: 「低評価に関しては触らず
    残しててください」)。

3.0〜4.0点の返信文について(2026-09-17再改訂):
    評価点だけで「至らない点があった」等の詫びを含む文面にしていたが、実際には
    不満点が書かれていない口コミにまでその表現が使われてしまい、キャストから
    指摘があった(ほたるさん談: 「至らない点がありましたらという表現は至らない
    ところがない場合には使わないでください」)。口コミは新規客が最初に見る
    集客材料でもあり、実際に無かった落ち度をわざわざ認める表現は避けたい、
    という女の子・お店側の意図がある。
    そのためhas_real_complaint()で口コミ本文(特にreview_shop_ngpoint欄と
    ネガティブワード)を見て、実際に不満点が書かれている場合だけ詫び寄りの
    文面にし、そうでなければお礼ベースの文面にするよう分岐した。

未返信の探し方:
    管理画面の口コミ一覧(新しい順・1ページ目)のうち、返信欄が「済」でなく、
    状態が「未読」または「表示」のものを未返信として扱う。
"""

import functools
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from heaven_http import COMMU_IDS, HeavenClient, HeavenError, LoginError, load_credentials

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

BASE = "https://newmanager.cityheaven.net"
OUT_DIR = Path(__file__).parent

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


_NO_COMPLAINT_TEXTS = {"特になし", "特にありません", "ありません", "なし", "特に無し", "無し", "特にございません"}

NEGATIVE_KEYWORDS = [
    "残念", "微妙", "イマイチ", "いまいち", "不満", "がっかり", "がっくり",
    "良くなかった", "よくなかった", "対応が悪い", "態度が悪い", "冷たい対応",
    "時間にルーズ", "遅刻", "無愛想", "感じが悪い",
]


def has_real_complaint(review: dict) -> bool:
    """口コミに実際の不満点が書かれているかを判定する(評価点の数字だけに頼らない)。

    3.0〜4.0点の口コミは、本当に不満があった場合もあれば、単に控えめな採点
    だっただけの場合もある。後者にまで「至らない点があった」等の詫びを含む
    文面を使うと、実際には無かった落ち度を認めているように読めてしまう
    (キャストからの指摘: 悪い点が無い場合にそうした表現を使わないでほしい)。
    """
    ngpoint = _clean_text(review.get("review_shop_ngpoint", ""))
    if ngpoint and ngpoint not in _NO_COMPLAINT_TEXTS:
        return True
    fields = [review.get(k, "") for k in ["title", "review_flow", "review_girl", "review_play", "review_tatal"]]
    full_text = _clean_text(" ".join(fields))
    return any(kw in full_text for kw in NEGATIVE_KEYWORDS)


_PROFILE_CLAIM_KEYWORDS = [
    "出身", "県民", "生まれ", "血液型", "本名", "歳です", "歳らしい", "才です",
]


def _looks_like_profile_claim(text: str) -> bool:
    """お客様が書いた出身地・年齢等の"事実っぽい記述"かどうか。

    キャストの実際のプロフィール・イメージ(売り方)と食い違っている可能性が
    あり、そのまま引用すると誤った情報を返信内で肯定してしまう
    (キャストからの指摘: 口コミ返信に実際と違う「鳥取出身」と書かれていた)。
    このためハイライト候補からは除外する。
    """
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
# 返信文の生成(評価帯ごとに複数パターンを持ち、review_idでランダムに選ぶ)
# ---------------------------------------------------------------------------

def build_reply(shop: str, cast: str, rating: float, repeat: bool, highlight: str, seed: int, has_complaint: bool = False) -> str:
    """口コミ本文を踏まえた返信文を作る。highlightが取れた場合はそれに触れる。

    文面のバリエーションだけでなく、段落の組み立て方(構成)自体も
    review_idごとに変える(layoutで3パターンの構成から選ぶ)ことで、
    「毎回同じ形」に見えないようにしている。
    """
    rnd = random.Random(seed)
    cast = cast or "キャスト"
    layout = rnd.randint(1, 3)

    if rating >= 4.0:
        greeting = (
            f"いつも{shop}をご利用いただき、本当にありがとうございます。" if repeat
            else f"この度は{shop}をご利用いただき、誠にありがとうございます。"
        )
        if highlight:
            reaction = rnd.choice([
                f"「{highlight}」というお言葉、読んでいてこちらまで嬉しい気持ちになりました。"
                f"{cast}のお時間を楽しんでいただけたことが伝わる一文で、スタッフ一同あたたかい気持ちになりました。",
                f"「{highlight}」とのご感想、{cast}にとって何よりの励みになる言葉です。"
                f"実際にお会いした時の様子が目に浮かぶようで、私たちも大変嬉しく拝読いたしました。",
                f"「{highlight}」というところ、まさに{cast}の魅力が伝わるご感想だと感じました。"
                f"お忙しい中、ここまで具体的に書いていただけたこと自体、本当にありがたく思っております。",
            ])
        else:
            reaction = rnd.choice([
                f"{cast}とのお時間を楽しんでいただけた様子が伝わってきて、スタッフ一同とても嬉しく拝見しました。",
                f"こうして丁寧に感想を残していただけること自体が、{cast}にとって何よりの励みになります。",
            ])
        warmth = rnd.choice([
            f"{cast}だけでなく{shop}全体としても、お客様に気持ちよくお過ごしいただけるよう日々工夫を重ねております。",
            f"こうしたお言葉をいただけると、日々の準備にも一層力が入ります。",
        ])
        invite = rnd.choice([
            f"これからも{shop}らしい心地よいひとときをご提供できるよう努めてまいりますので、"
            f"またお時間が合う際には、ぜひ{cast}にお会いしにいらしてください。",
            f"次にお越しいただく際も、ご満足いただけるひとときをご用意してお待ちしております。"
            f"{cast}ともども、次のご来店を楽しみにしております。",
            f"{shop}スタッフ一同、またのご来店を心よりお待ちしております。",
        ])
        if layout == 1:
            return f"{greeting}\n\n{reaction}{warmth}\n\n{invite}"
        if layout == 2:
            return f"{greeting}{reaction}\n\n{warmth}\n\n{invite}"
        return f"{reaction}\n\n{greeting}{warmth}\n\n{invite}"

    if rating >= 3.0:
        greeting = rnd.choice([
            f"この度は{shop}をご利用いただき、ありがとうございます。",
            f"{shop}へのご来店、口コミのご投稿をありがとうございます。",
        ])
        if has_complaint:
            # 口コミ本文に実際の不満点が書かれている場合だけ、それを踏まえた文面にする。
            if highlight:
                reaction = rnd.choice([
                    f"「{highlight}」というお言葉から、楽しんでいただけた部分もあったようで、まずはほっとしております。"
                    f"率直なご感想として大切に受け止めております。",
                    f"「{highlight}」とのこと、貴重なご意見として参考にさせていただきます。"
                    f"次回さらに満足いただけるお店づくりに努めてまいります。",
                ])
            else:
                reaction = (
                    f"{cast}とのひとときについて率直なご感想をいただき、ありがとうございます。"
                    f"次回さらに満足していただけるよう努めてまいります。"
                )
            warmth = rnd.choice([
                f"至らない点があったとのご指摘は、サービス向上のための大切な材料として活かしてまいります。",
                f"{shop}としても、いただいたお声を真摯に受け止め、今後の運営に反映させていただきます。",
            ])
            invite = rnd.choice([
                f"またご縁がありましたら、ぜひ{shop}にお立ち寄りください。",
                f"次にお会いする機会がありましたら、今回以上にお楽しみいただけるよう努めます。",
            ])
        else:
            # 不満点は書かれていない(単に控えめな採点だっただけ)ので、
            # 落ち度を認めるような表現は使わずお礼ベースの文面にする。
            if highlight:
                reaction = rnd.choice([
                    f"「{highlight}」というお言葉、拝見しました。"
                    f"率直なご感想として大切に受け止めております。",
                    f"「{highlight}」とのこと、貴重なご意見として今後の参考にさせていただきます。",
                ])
            else:
                reaction = (
                    f"{cast}とのひとときについて感想をお寄せいただき、ありがとうございます。"
                )
            warmth = rnd.choice([
                f"いただいたお声は、これからのサービスづくりの参考にさせていただきます。",
                f"こうして感想を残していただけること自体が、{cast}にとっても{shop}にとっても励みになります。",
            ])
            invite = rnd.choice([
                f"またお時間が合う際には、ぜひ{shop}にお立ち寄りください。",
                f"次のご来店も、{cast}ともどもお待ちしております。",
            ])
        if layout == 1:
            return f"{greeting}\n\n{reaction}{warmth}\n\n{invite}"
        if layout == 2:
            return f"{greeting}{reaction}\n\n{warmth}\n\n{invite}"
        return f"{reaction}\n\n{greeting}{warmth}\n\n{invite}"

    raise ValueError(f"build_reply()は3.0点未満の口コミを想定していません(rating={rating})")


# ---------------------------------------------------------------------------
# エスカレーション判定(意思決定の自動化: 深刻な内容は自動返信せず人に回す)
# ---------------------------------------------------------------------------

# ここに引っかかる口コミは、自動返信せずに「要確認」として残す。
# トラブル・安全に関わる可能性のある内容は、定型文でその場をしのぐより
# 一希さん本人が中身を読んで判断すべき、という考え方。
SERIOUS_KEYWORDS = [
    "盗撮", "盗聴", "暴力", "殴", "脅迫", "恐喝", "監禁", "帰さない", "帰してもらえ",
    "警察", "通報", "訴え", "裁判", "犯罪",
    "薬物", "病院", "救急搬送",
    "本番行為を強要", "無理やり", "同意なく", "無断で撮影",
]

# 低評価は定型文で済ませず、本人が内容を読んで個別に対応する(自動返信の対象外)。
LOW_RATING_THRESHOLD = 3.0


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

    if rating < LOW_RATING_THRESHOLD:
        print(f"  ⚠ review_id={review_id} を要確認としてスキップします(低評価: {rating}点)。自動返信しません。")
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


def write_status_json(name: str, data: dict):
    data["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def write_flagged_report(flagged: list):
    """要確認の口コミを一覧化し、Markdownで書き出す(GitHub Actionsからはリポジトリにコミットされる)。"""
    path = OUT_DIR / "要確認_口コミ.md"
    if not flagged:
        if path.exists():
            path.write_text("現在、要確認の口コミはありません。\n", encoding="utf-8")
        return

    jst_now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    lines = ["# 要確認の口コミ(自動返信をスキップしたもの)", "", f"最終更新: {jst_now}", ""]
    for r in flagged:
        url = f"{BASE}/C8ReviewDetail.php?review_id={r['review_id']}&shopdir={r['shopdir']}"
        lines.append(f"## [{r['shop_label']}] review_id={r['review_id']}(理由:「{r['flag_reason']}」)")
        lines.append(f"- 評価: {r.get('rate', '?')}点")
        lines.append(f"- 相手: {_clean_text(r.get('visit_girl', ''))}")
        lines.append(f"- 確認用リンク: {url}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n要確認レポートを書き出しました: {path.name}({len(flagged)}件)")


def main():
    mode = "確認のみ(DRY RUN)" if DRY_RUN else "実際に投稿します"
    print(f"モード: {mode}")
    print()

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

    posted = sum(v["posted"] for v in shops_stat.values())
    _print(f"返信した: {posted}件 / 要確認(自動返信を見送り): {len(flagged)}件 / 失敗した店舗: {len(failed)}")
    Path("reply_status.json").write_text(
        json.dumps({"flagged_open": len(flagged), "failed_shops": len(failed)}, ensure_ascii=False), encoding="utf-8")

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
