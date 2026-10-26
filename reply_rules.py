"""キャストからの連絡を、人を待たずに返せるものと、人（担当）に見せるものに分ける。

種類: clear（出勤を上げる）／thanks（返さない）／ask_when・tentative・decline・later（決まった返事を返す）／
      question・other（人に見せる）。返事の文は鍵つき（sealed/reply_texts.enc）。開けない時は返さず人へ。

決まりの元は非公開の作業場のパターン集（直したらここも直す）
"""
import re

import secret_box
from shift_reply import DAYS, DAYSPAN, MD, NEG, WEEKDAY, _norm, read_shift_reply

# 返事の定型文は鍵つき（sealed/reply_texts.enc、鍵は金庫 KNOWLEDGE_KEY）。開けない時は空＝自動では返さず人へ（2026-10-25）
_TX = secret_box.data("reply_texts") or {}

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
ASK_TEXT = _TX.get("ask_text", "")
DECLINE_TEXT = _TX.get("decline_text", "")
LATER_TEXT = _TX.get("later_text", "")
# 見張りが読み切れない連絡には、まず受け取ったことだけ返す
HOLD_TEXT = _TX.get("hold_text", "")
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
    # 日にちの無い断り（「来週は難しいです」のような形）→ お礼を返す
    if r["status"] == "none" and any(w in t for w in NEG) and len(t) <= 120 and not any(w in t for w in DECLINE_SKIP):
        return "decline"
    if any(w in t for w in NEG) or any(w in t for w in NEG2):
        return "other"
    # 日にちや時間が少しでも書いてあるもの（unclear）は、読み違いを避けるため自動で聞き返さない（2026-09-27 ある子の件）
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
