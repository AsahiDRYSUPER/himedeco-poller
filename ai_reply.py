"""姫デコチャット（女の子）への返事を Claude（Anthropic API）で考える。2026-10-22

なぜ: 決まった言葉に決まった文を返すルールだけでは、意味の取れない連絡に「確認して連絡します」としか返せず、
      会話が止まっていた（一希さん「意味が分からなければ聞き返して会話の中で確認して」「PCを閉じていても自動で返して」）。
どう動くか: watch_chat.py が「質問・その他・外国人（YOASOBI）の話」と判断した連絡だけを渡す。
      直近のやりとりと決まり（ai_knowledge.md＋非公開メモ AI_PRIVATE_NOTES）を Claude に渡し、
      返事の文・送るかどうか・一希さんに知らせることを JSON で受け取る。
鍵:   Secret ANTHROPIC_API_KEY（一希さんが Claude Console で作って貼る。無ければ何もしない＝今まで通り）。
止め方: リポジトリの変数 AI_REPLY を off。モデルは変数 AI_MODEL（既定 claude-opus-5-5）、考える深さは AI_EFFORT（既定 low）。
公開リポジトリなので、名前・本文・返事の中身はログに出さない（件数と結果の種類だけ）。
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
LEARNED_FILE = OUT_DIR / "ai_learned.json"
KNOWLEDGE_FILE = Path(__file__).with_name("ai_knowledge.md")
PUBLIC_RAW = "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data"

MODEL = (os.environ.get("AI_MODEL") or "").strip() or "claude-opus-5-5"
EFFORT = (os.environ.get("AI_EFFORT") or "").strip() or "low"
MAX_PER_RUN = int(os.environ.get("AI_MAX_PER_RUN", "12") or 12)
MAX_REPLY_CHARS = 480           # 姫デコチャット1通は500字まで
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5-5")
YOASOBI_SHOPS = ("cg_kirakira", "s_matikado", "mrs_orange", "potya_reen")

_state = {"calls": 0, "disabled": False, "last_error": ""}

LANG = {"type": "string", "enum": ["0", "1", "2", "3", "4"]}
CAST_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["reply", "reply_and_notify", "notify_only", "no_reply"]},
        "reply": {"type": "string"},
        "owner_note": {"type": "string"},
        "yoasobi": {
            "type": "object",
            "properties": {
                "is_answer": {"type": "boolean"},
                "wants_listed": {"type": "boolean"},
                "lang_en": LANG, "lang_zh": LANG, "lang_kr": LANG,
                "ng": {"type": "array", "items": {"type": "string"}},
                "photo": {"type": "string", "enum": ["same", "hide_eyes", "no_face", "ask_again", "unknown"]},
            },
            "required": ["is_answer", "wants_listed", "lang_en", "lang_zh", "lang_kr", "ng", "photo"],
            "additionalProperties": False,
        },
        "learned": {"type": "string"},
    },
    "required": ["action", "reply", "owner_note", "yoasobi", "learned"],
    "additionalProperties": False,
}

OUTPUT_RULES = """
## 返し方（JSONの欄）
- action：reply＝返事を送る／reply_and_notify＝返事を送って一希さんにも知らせる／notify_only＝送らずに一希さんに知らせるだけ／no_reply＝何もしない
- reply：女の子に送る文（450字以内）。no_reply・notify_only の時は空
- owner_note：一希さんに知らせたいこと（短く、名前は入れてよい。知らせることが無ければ空）
- yoasobi：YOASOBIの3つの質問への答え・外国人OKの申し出が含まれていれば埋める。無関係なら is_answer=false, wants_listed=false, 言葉は "0", ng=[], photo="unknown"
- learned：このやり取りで分かった、今後も使える一般的な決まりがあれば1文（女の子の名前・個人の事情は書かない）。無ければ空
"""


def enabled():
    if _state["disabled"]:
        return False
    if (os.environ.get("AI_REPLY") or "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def yobi(name):
    """呼びかけの名前（shift_auto.yobi と同じ決まり）。肩書きを外し、「さん」を重ねない。"""
    n = re.sub(r"[\[【(（].*?[\]】)）]", "", name or "").strip()
    n = re.sub(r"(?<=[ぁ-んァ-ヶー一-龠])(RSP|RS|SP)$", "", n)
    return n if n.endswith("さん") else n + "さん"


def _clean(t):
    t = re.sub(r"<br\s*/?>", "\n", str(t or ""), flags=re.I)
    t = re.sub(r"<[^>]*>", " ", t)
    t = re.sub(r"[ \t　]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def load_learned():
    try:
        if LEARNED_FILE.exists():
            return json.loads(LEARNED_FILE.read_text(encoding="utf-8")).get("items", [])
        import requests
        r = requests.get(f"{PUBLIC_RAW}/ai_learned.json", timeout=20)
        return r.json().get("items", []) if r.status_code == 200 else []
    except Exception:
        return []


def save_learned(note, name):
    """学んだ決まりを残す（名前らしい文字は消す。新しい順に40件）。"""
    note = (note or "").strip()
    if not note:
        return
    base = re.sub(r"(さん|ちゃん)$", "", name or "")
    if base and base in note:
        return
    if re.search(r"[ぁ-んァ-ヶ一-龠]{2,6}(さん|ちゃん)", note):
        return
    items = load_learned()
    if note in [i.get("note") for i in items]:
        return
    items.insert(0, {"note": note[:200], "at": datetime.now(JST).strftime("%Y-%m-%d")})
    LEARNED_FILE.write_text(json.dumps({"items": items[:40]}, ensure_ascii=False), encoding="utf-8")


def system_prompt():
    know = KNOWLEDGE_FILE.read_text(encoding="utf-8") if KNOWLEDGE_FILE.exists() else ""
    private = (os.environ.get("AI_PRIVATE_NOTES") or "").strip()
    learned = load_learned()[:30]
    parts = [know, OUTPUT_RULES]
    if private:
        parts.append("## 非公開メモ（お店の中の決まり。女の子に聞かれた時だけ使う）\n" + private)
    if learned:
        parts.append("## これまでに分かったこと\n" + "\n".join(f"- {i['note']}" for i in learned))
    return "\n\n".join(parts)


def history_text(cli, base, gid, mid, limit=14):
    """直近のやりとりを古い順に「[月/日 時:分 店|女の子] 本文」で返す。"""
    ref = f"{base}/himedecochat/talk?girls_id={gid}&mid={mid}"
    try:
        t = cli.s.post(base + "/himedecochat/api/new_list", json={"girls_id": int(gid)},
                       headers={"X-Requested-With": "XMLHttpRequest", "Referer": ref}, timeout=30)
        talks = (t.json().get("talk") or []) if t.json().get("result") == 0 else []
    except Exception:
        talks = []
    lines = []
    for m in talks[-limit:]:
        who = "女の子" if m.get("sent_from_flg") == 2 else "店"
        at = str(m.get("create_date") or "")[5:16].replace("-", "/")
        lines.append(f"[{at} {who}] {_clean(m.get('body'))[:600]}")
    return "\n".join(lines)


def ask(shop_label, shopdir, name, history, latest):
    """Claude に聞いて、決まった形の dict を返す。使えない・断られた・壊れた時は None（理由は文字で返す）。"""
    if not enabled():
        return None, "off"
    if _state["calls"] >= MAX_PER_RUN:
        return None, "cap"
    _state["calls"] += 1
    import anthropic
    client = anthropic.Anthropic()
    user = (f"店：{shop_label}（YOASOBIヘブン：{'使っている' if shopdir in YOASOBI_SHOPS else '使っていない'}）\n"
            f"相手の女の子の呼び方：{name}\n"
            f"今の日時：{datetime.now(JST).strftime('%Y-%m-%d %H:%M')}\n\n"
            f"直近のやりとり（古い順）：\n{history or '（読めなかった）'}\n\n"
            f"今回返事をする女の子の発言：\n{latest}\n\n"
            "この発言にどう返すかを、決まりに沿って JSON で答えてください。")
    kw = dict(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": CAST_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    try:
        try:
            if MODEL in FALLBACK_MODELS:
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
            else:
                resp = client.messages.create(**kw)
        except anthropic.APIError as e:
            # 中身は API のエラー文だけ（鍵は入らない）。--check の時に表示する
            _state["last_error"] = f"{type(e).__name__}: {str(getattr(e, 'message', '') or e)[:300]}"
            raise
    except anthropic.AuthenticationError:
        _state["disabled"] = True
        return None, "鍵が無効"
    except anthropic.PermissionDeniedError:
        _state["disabled"] = True
        return None, "鍵の権限が足りない"
    except anthropic.RateLimitError:
        return None, "回数の上限"
    except anthropic.BadRequestError as e:
        msg = str(getattr(e, "message", "") or "")
        if "credit" in msg.lower() or "balance" in msg.lower():
            _state["disabled"] = True
            return None, "残高が足りない"
        return None, "リクエストの形が違う"
    except anthropic.APIStatusError as e:
        return None, f"APIエラー {getattr(e, 'status_code', '')}"
    except anthropic.APIConnectionError:
        return None, "つながらない"
    if resp.stop_reason == "refusal":
        return None, "断られた"
    if resp.stop_reason == "max_tokens":
        return None, "途中で切れた"
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except Exception:
        return None, "形が壊れていた"
    return data, "ok"


def check(data):
    """送ってよい形かを確かめる。返す: (送るか, 理由)"""
    action = data.get("action")
    reply = (data.get("reply") or "").strip()
    if action not in ("reply", "reply_and_notify"):
        return False, action or "no_action"
    if not reply:
        return False, "返事が空"
    if len(reply) > MAX_REPLY_CHARS:
        return False, "長すぎる"
    if re.search(r"(パスワード|暗証番号|口座番号|クレジットカード番号)", reply):
        return False, "送ってはいけない言葉"
    return True, "ok"


def selfcheck():
    """鍵とモデルが通るかを、作り話のやりとりで1回だけ試す。誰にも送らない。
    公開リポジトリなので、ログには結果の種類・長さ・数だけ出す（返事の中身・メモの中身は出さない）。"""
    print("AI: 鍵", "あり" if (os.environ.get("ANTHROPIC_API_KEY") or "").strip() else "なし",
          "/ 非公開メモ", "あり" if (os.environ.get("AI_PRIVATE_NOTES") or "").strip() else "なし",
          "/ モデル", MODEL, "/ 深さ", EFFORT)
    if not enabled():
        print("AI: ❌ 動かない（鍵が無いか、AI_REPLY=off）")
        return 1
    data, why = ask("キラキラ学園", "cg_kirakira", "テストさん",
                    "[10/23 10:00 店] 外国人のお客さんもOKにしてみない？",
                    "外国人OKでお願いします！英語は挨拶くらいならできます")
    if not data:
        print("AI: ❌", why, "|", _state["last_error"] or "-")
        return 1
    ok, w = check(data)
    y = data.get("yoasobi") or {}
    print("AI: ✅ つながった / action", data.get("action"), "/ 送れる形", ok, w,
          "/ 返事", len(data.get("reply") or ""), "字 / 外国人OK", y.get("wants_listed"), "/ 英語★", y.get("lang_en"))
    return 0


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(selfcheck())
