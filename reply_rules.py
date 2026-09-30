"""キャストからの連絡を、人を待たずに返せるものと、人（一希さん）に見せるものに分ける。

種類:
    clear     … 日にち＋時間が揃った出勤の返事 → shift_auto が上げて「上げました」と返す
    thanks    … お礼・了解だけ → 返さない（通知だけ）
    ask_when  … 出勤の話だが日にちか時間が足りない・曖昧 → 「出られそうな日と時間が決まったら教えてください」と返す
    tentative … 日にちだけ（時間なし）の「出勤予定」 → 備考「オキニトークでご確認ください。」を入れて、時間を聞いて返す
    decline   … 声かけへの「来週は難しい」「予定以外は出られない」など、日にちの無い断り → お礼を返す
    later     … 「また決まり次第連絡します」「相談してみます」など、あとで連絡すると言っている → 「分かりました！よろしくお願いします🙏」
                （2026-09-30 一希さん「分かりました！よろしくお願いしますくらいでいいかな」とろ〜り りあらさん）
    question  … 質問（？が付いている） → 人に見せる
    other     … それ以外（事情・当日の欠勤や遅れ・長い話） → 人に見せる

2026-09-28 一希さん「（めとさんに）返信していない。今度からはちゃんと返してね」「（すずらんさんには）協力してくれていることに
ありがとうのスタンスで」。それまで tentative と decline は人に回していて、返事が抜けていた。

決まりの元: メイン作業場/projects/ヘブン運用・自動化/出勤返事の読み方.md（一希さんが直したらここも直す）
"""
import re

from shift_reply import DAYS, DAYSPAN, MD, NEG, WEEKDAY, _norm, read_shift_reply

THANKS_RE = re.compile(
    r"^(?:ありがとうございます|ありがとうございました|ありがとうございまーす|ありがとう|了解です|了解しました|了解いたしました|了解|りょうかいです|りょうかい"
    r"|わかりました|分かりました|承知しました|承知いたしました|承知です|はい|よろしくお願いします|よろしくお願いいたします|お願いします|お願いいたします"
    r"|お疲れ様です|お疲れさまです|おつかれさまです|おつかれさまでした|お疲れ様でした|お疲れさまでした|おっけーです|おっけです|オッケーです|オッケー"
    r"|大丈夫です|助かります|嬉しいです|うれしいです|ですね|です|ます|また|ね|よ|わ|の)+$"
)
SHIFT_WORDS = ("出勤", "出れ", "出られ", "入れ", "行け", "シフト")
NEG2 = ("できなく", "出来なく", "行けなく", "いけなく", "出れなく", "出られなく", "遅れ", "遅刻", "早退", "体調", "熱",
        "病院", "すみません", "すいません", "ごめん", "申し訳")
ASK_REASONS = {
    "日にちはあるが時間が無い", "時間はあるが日にちが無い", "時間が読めない", "日にちか時間を選ばせている",
    "同じ日に時間が2つある", "分が00か30でない", "「かも」がある", "「たぶん」がある", "「多分」がある", "「未定」がある",
    "「決まったら」がある", "「また連絡」がある", "「後で」がある", "「あとで」がある", "「くらい」がある", "「ぐらい」がある",
    "「頃」がある", "「ごろ」がある", "「以降」がある", "「前後」がある", "「ラスト」がある", "「らすと」がある",
    "「最後まで」がある", "「閉店」がある",
}
ASK_TEXT = "出られそうな日と時間が決まったら教えてください🙌"
DECLINE_TEXT = "お返事ありがとうございます😊\nまた出られる日が決まったら教えてくださいね！"
LATER_TEXT = "分かりました！よろしくお願いします🙏"
LATER_WORDS = ("決まり次第", "決まったら連絡", "わかったら連絡", "分かったら連絡", "また連絡", "連絡します", "連絡いたします",
               "連絡しますね", "相談してみます", "相談します")
# 断りでも、自動では返さず人に見せるもの（当日の欠勤・遅れ・体のこと・辞める話）
DECLINE_SKIP = ("今日", "本日", "明日", "あした", "当日", "遅れ", "遅刻", "早退", "欠勤", "体調", "熱", "病院", "生理",
                "インフル", "コロナ", "辞め", "やめます", "やめよう", "やめる", "やめた", "退店", "引退", "卒業")


def _core(t):
    """絵文字・記号・空白を落として、言葉だけにする。"""
    return re.sub(r"[^\w぀-ヿ一-鿿]+", "", t)


def classify(body, now=None, shop=None):
    t = (body or "").strip()
    if not t:
        return "other"
    if "？" in t or "?" in t:
        return "question"
    core = _core(t)
    if core and THANKS_RE.match(core):
        return "thanks"
    r = read_shift_reply(t, now, shop)
    if r["status"] in ("clear", "partial"):
        return "clear"
    if r["status"] == "tentative":
        return "tentative"
    # 日にちの無い断り（「来週は出勤難しいです」「県外なので予定以外でれません」）→ お礼を返す
    if r["status"] == "none" and any(w in t for w in NEG) and len(t) <= 120 and not any(w in t for w in DECLINE_SKIP):
        return "decline"
    if any(w in t for w in NEG) or any(w in t for w in NEG2):
        return "other"
    # 日にちや時間が少しでも書いてあるもの（unclear）は、読み違いを避けるため自動で聞き返さない（2026-09-27 つくしさんの件）
    if r["status"] == "none" and len(t) <= 80 and any(w in t for w in SHIFT_WORDS):
        return "ask_when"
    has_date = any(rx.search(_norm(t, shop)) for rx in (MD, DAYSPAN, DAYS, WEEKDAY))
    if not has_date and r["status"] in ("none", "unclear") and len(t) <= 300 and any(w in t for w in LATER_WORDS):
        return "later"          # 「今日相談してみます」の「今日」は出勤の日ではないので、日付の無い話として扱う
    return "other"


def ask_text(yobi):
    return f"{yobi}、連絡ありがとうございます！\n{ASK_TEXT}"


def decline_text(yobi):
    return f"{yobi}、{DECLINE_TEXT}"
