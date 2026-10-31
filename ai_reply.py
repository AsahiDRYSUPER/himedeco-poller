"""姫デコチャット（女の子）への返事を Claude（Anthropic API）で考える。2026-10-22

なぜ: 決まった言葉に決まった文を返すルールだけでは、意味の取れない連絡に返せず、会話が止まっていたため。
どう動くか: watch_chat.py が「質問・その他・外国人（YOASOBI）の話」と判断した連絡だけを渡す。
      直近のやりとりと決まり（鍵つきの指示書 sealed/ai_knowledge.enc・sealed/ai_rules.enc＋非公開メモ AI_PRIVATE_NOTES）を Claude に渡し、
      返事の文・送るかどうか・責任者に知らせることを JSON で受け取る。
鍵:   Secret ANTHROPIC_API_KEY（責任者が Claude Console で作って貼る。無ければ何もしない＝今まで通り）。
止め方: リポジトリの変数 AI_REPLY を off。モデルは変数 AI_MODEL（既定 claude-opus-5-5）、考える深さは AI_EFFORT（既定 low）。
公開リポジトリなので、名前・本文・返事の中身はログに出さない（件数と結果の種類だけ）。
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import secret_box
import data_box     # 学んだこと（ai_learned）は鍵つきで dataブランチへ（2026-10-25）

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
LEARNED_NAME = "ai_learned"   # 学んだ一般的な決まり。鍵つき out/ai_learned.enc.json（2026-10-25〜）
# 指示書は鍵つき（sealed/*.enc、鍵は金庫の KNOWLEDGE_KEY）。開けない時は AI を使わない（2026-10-25）
PUBLIC_RAW = "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data"

MODEL = (os.environ.get("AI_MODEL") or "").strip() or "claude-opus-5-5"
EFFORT = (os.environ.get("AI_EFFORT") or "").strip() or "low"
MAX_PER_RUN = int(os.environ.get("AI_MAX_PER_RUN", "12") or 12)
MAX_REPLY_CHARS = 480           # 姫デコチャット1通は500字まで
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5-5")
YOASOBI_SHOPS = ("cg_kirakira", "s_matikado", "mrs_orange", "potya_reen")

_state = {"calls": 0, "disabled": False, "last_error": ""}

# 返し方の決まり（JSONの欄の意味）と、返させる JSON の形は鍵つき（sealed/ai_rules.enc、正本は非公開側）。開けない時は AI を使わない（2026-10-26）
_RULES = secret_box.data("ai_rules") or {}
CAST_RULES = _RULES.get("cast_output_rules") or ""
CAST_SCHEMA = _RULES.get("cast_schema") or None
CUSTOMER_RULES = _RULES.get("customer_rules") or ""
CUSTOMER_SCHEMA = _RULES.get("customer_schema") or None


def rules_ready():
    """返し方の決まりと JSON の形（鍵つき）がそろっているか"""
    return bool(CAST_RULES and CUSTOMER_RULES and isinstance(CAST_SCHEMA, dict) and isinstance(CUSTOMER_SCHEMA, dict))


def enabled():
    if _state["disabled"]:
        return False
    if (os.environ.get("AI_REPLY") or "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    if not secret_box.text("ai_knowledge") or not rules_ready():
        _state["last_error"] = "指示書・返し方の決まり（鍵つき）が開けない。金庫の KNOWLEDGE_KEY を確かめる"
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
    """手元 → dataブランチ（ai_learned.enc.json → 無ければ移行前の ai_learned.json）の順。読めなければ空"""
    try:
        v = data_box.load(LEARNED_NAME, default=None, out_dir=OUT_DIR, base=PUBLIC_RAW, timeout=20)
        return list(v.get("items", [])) if isinstance(v, dict) else []
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
    # 読めなかった（有るのに開けない・つながらない）時は書かない。前の記録を空から作り直して上書きしないため（2026-10-26）
    state, v = data_box.read(LEARNED_NAME, out_dir=OUT_DIR, base=PUBLIC_RAW, timeout=20)
    if state == "error" or (state == "ok" and not isinstance(v, dict)):
        print("AI: 学んだことの記録が読めないので、今回は書かない")
        return
    items = [i for i in (v.get("items", []) if state == "ok" else []) if isinstance(i, dict)]
    if note in [i.get("note") for i in items]:
        return
    items.insert(0, {"note": note[:200], "at": datetime.now(JST).strftime("%Y-%m-%d")})
    data_box.write(LEARNED_NAME, {"items": items[:40]}, OUT_DIR)


def system_prompt():
    know = secret_box.text("ai_knowledge") or ""
    private = (os.environ.get("AI_PRIVATE_NOTES") or "").strip()
    learned = load_learned()[:30]
    parts = [know, CAST_RULES]
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
    if not secret_box.text("yoasobi_knowledge"):
        return None, "no_knowledge"
    if _state["calls"] >= MAX_PER_RUN:
        return None, "cap"
    _state["calls"] += 1
    user = (f"店：{shop_label}（YOASOBIヘブン：{'使っている' if shopdir in YOASOBI_SHOPS else '使っていない'}）\n"
            f"相手の女の子の呼び方：{name}\n"
            f"今の日時：{datetime.now(JST).strftime('%Y-%m-%d %H:%M')}\n\n"
            f"直近のやりとり（古い順）：\n{history or '（読めなかった）'}\n\n"
            f"今回返事をする女の子の発言：\n{latest}\n\n"
            "この発言にどう返すかを、決まりに沿って JSON で答えてください。")
    return _call(system_prompt(), user, CAST_SCHEMA)


def _call(system, user, schema):
    """Claude に1回聞いて (dict, 理由) を返す。失敗の理由は文字（鍵は出さない）。"""
    import anthropic
    client = anthropic.Anthropic()
    kw = dict(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
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


# ---- お客さん（夜遊びヘブンの多言語チャット）向け ----
def customer_prompt():
    know = secret_box.text("yoasobi_knowledge") or ""
    return know + "\n\n" + CUSTOMER_RULES


def ask_customer(shop_label, name, history, latest, schedule=""):
    """夜遊びヘブンのお客さんの発言にどう返すかを Claude に聞く。返す: (dict, 理由)。女の子向けの非公開メモは渡さない。"""
    if not enabled():
        return None, "off"
    if not secret_box.text("yoasobi_knowledge"):
        return None, "no_knowledge"
    if _state["calls"] >= MAX_PER_RUN:
        return None, "cap"
    _state["calls"] += 1
    user = (f"店：{shop_label}\n相手：{name}（訪日外国人のお客さん。チャットは自動翻訳）\n"
            f"今の日時：{datetime.now(JST).strftime('%Y-%m-%d %H:%M')}\n\n"
            f"直近のやりとり（古い順）：\n{history or '（無し）'}\n\n"
            f"今回返すお客さんの発言：\n{latest}\n\n"
            + (f"今日のCTI（この店の女の子の出勤と予約の時間。予約の時間は埋まっている）：\n{schedule}\n\n" if schedule else "今日のCTI：（写しが無い。空き・変更は受付へ）\n\n")
            + "決まりに沿って JSON で答えてください。")
    return _call(customer_prompt(), user, CUSTOMER_SCHEMA)


def check_customer(data):
    """お客さんに送ってよい形か。返す: (送るか, 理由)"""
    if data.get("action") not in ("reply", "reply_notify") or data.get("category") == "C":
        return False, data.get("action") or "no_action"
    reply = (data.get("reply") or "").strip()
    if not reply:
        return False, "返事が空"
    if len(re.sub(r"https?://\S+", "", reply)) > 300:   # 予約リンク（200字超）は数えない（2026-10-31）
        return False, "長すぎる"
    # 値引きの言葉。free 単独は「空いている」の意味で使われるので止めない（2026-10-31）
    if re.search(r"(割引|値引|discount|for free|free of charge|無料|タダ)", reply, re.I):
        return False, "値引きの言葉"
    return True, "ok"


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
          "/ 指示書（鍵つき）", "あり" if secret_box.text("ai_knowledge") else "なし",
          "/ お客さん用の指示書", "あり" if secret_box.text("yoasobi_knowledge") else "なし",
          "/ 返し方の決まり", "あり" if rules_ready() else "なし",
          "/ 非公開メモ", "あり" if (os.environ.get("AI_PRIVATE_NOTES") or "").strip() else "なし",
          "/ モデル", MODEL, "/ 深さ", EFFORT)
    k = os.environ.get("ANTHROPIC_API_KEY") or ""
    # 鍵の中身は出さない。形だけ（長さ・決まった頭か・空白や改行が混じっていないか）
    print("AI: 鍵の形 / 長さ", len(k), "/ sk-ant- で始まる", k.strip().startswith("sk-ant-"),
          "/ 空白・改行が混じる", bool(re.search(r"\s", k)))
    try:
        import anthropic
        print("AI: 部品の版", anthropic.__version__)
    except Exception:
        pass
    if not enabled():
        print("AI: ❌ 動かない（鍵が無いか、AI_REPLY=off か、指示書が開けない）", _state["last_error"] or "")
        return 1
    data, why = ask("試験店", "shop_test", "テストさん",
                    "[10/23 10:00 店] 試験の連絡です",
                    "試験の返事です。よろしくお願いします")
    if not data:
        print("AI: ❌", why, "|", _state["last_error"] or "-")
        return 1
    ok, w = check(data)
    print("AI: ✅ つながった / action", data.get("action"), "/ 送れる形", ok, w,
          "/ 返事", len(data.get("reply") or ""), "字")
    return 0


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(selfcheck())
