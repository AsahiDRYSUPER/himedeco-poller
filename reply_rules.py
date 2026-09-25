"""キャストからの連絡を、人を待たずに返せるものと、人（一希さん）に見せるものに分ける。

種類:
    clear     … 日にち＋時間が揃った出勤の返事 → shift_auto が上げて「上げました」と返す
    thanks    … お礼・了解だけ → 返さない（通知だけ）
    ask_when  … 出勤の話だが日にちか時間が足りない・曖昧 → 「出られそうな日と時間が決まったら教えてください」と返す
    question  … 質問（？が付いている） → 人に見せる
    other     … それ以外（事情・断り・長い話） → 人に見せる

決まりの元: メイン作業場/projects/ヘブン運用・自動化/出勤返事の読み方.md（一希さんが直したらここも直す）
"""
import re

from shift_reply import NEG, read_shift_reply

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


def _core(t):
    """絵文字・記号・空白を落として、言葉だけにする。"""
    return re.sub(r"[^\w぀-ヿ一-鿿]+", "", t)


def classify(body, now=None):
    t = (body or "").strip()
    if not t:
        return "other"
    if "？" in t or "?" in t:
        return "question"
    core = _core(t)
    if core and THANKS_RE.match(core):
        return "thanks"
    r = read_shift_reply(t, now)
    if r["status"] == "clear":
        return "clear"
    if any(w in t for w in NEG) or any(w in t for w in NEG2):
        return "other"
    if len(t) > 80:
        return "other"
    if r["status"] == "unclear" and r["reason"] in ASK_REASONS:
        return "ask_when"
    if r["status"] == "none" and any(w in t for w in SHIFT_WORDS):
        return "ask_when"
    return "other"


def ask_text(yobi):
    return f"{yobi}、連絡ありがとうございます！\n{ASK_TEXT}"
