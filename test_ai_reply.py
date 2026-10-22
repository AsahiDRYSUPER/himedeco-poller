"""ai_reply.py と watch_chat.handle_ai の試験（本物のAPIもヘブンも呼ばない）。"""
import json
import os
import sys
import types

os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
os.environ["OUT_DIR"] = "out_test"
os.makedirs("out_test", exist_ok=True)

import anthropic  # noqa: E402

CALLS = []


class FakeResp:
    def __init__(self, payload, stop="end_turn"):
        self.stop_reason = stop
        self.content = [types.SimpleNamespace(type="thinking", thinking=""),
                        types.SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))]


def fake_client(payload, stop="end_turn"):
    class M:
        def create(self, **kw):
            CALLS.append(kw)
            return FakeResp(payload, stop)
    c = types.SimpleNamespace(messages=M(), beta=types.SimpleNamespace(messages=M()))
    return lambda *a, **k: c


import ai_reply  # noqa: E402

ANS = {"action": "reply", "reply": "試験さん、ありがとう！英語は挨拶程度・NGなし・写真はヘブンと同じ、で設定しておくね。終わったら連絡するね。",
       "owner_note": "", "learned": "",
       "yoasobi": {"is_answer": True, "complete": True, "wants_listed": False, "lang_en": "1", "lang_zh": "0", "lang_kr": "0", "ng": [], "photo": "same"}}

ok = True

anthropic.Anthropic = fake_client(ANS)
data, why = ai_reply.ask("キラキラ学園", "cg_kirakira", "試験さん", "[10/22 19:00 店] 3つだけ教えてください", "①英語なら挨拶程度 ②なし ③同じで大丈夫")
ok &= (why == "ok" and data["action"] == "reply")
kw = CALLS[-1]
ok &= kw["model"] == "claude-opus-5-5"
ok &= kw.get("fallbacks") == "default" and "server-side-fallback-2026-07-01" in kw.get("betas", [])
ok &= kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "low"
ok &= "YOASOBIヘブン" in kw["system"][0]["text"]
print("ask:", why, "fallbacks:", kw.get("fallbacks"))

send, w = ai_reply.check(data)
ok &= send
long = dict(ANS, reply="あ" * 600)
ok &= ai_reply.check(long) == (False, "長すぎる")
ok &= ai_reply.check(dict(ANS, action="no_reply"))[0] is False
print("check:", send, w)

anthropic.Anthropic = fake_client(ANS, stop="refusal")
d2, why2 = ai_reply.ask("キラキラ学園", "cg_kirakira", "試験さん", "", "テスト")
ok &= d2 is None and why2 == "断られた"
print("refusal:", why2)

open("out_test/ai_learned.json","w").write('{"items": []}')   # 本番の学んだこと（dataブランチ）を読みに行かないよう、空の控えを置く
ai_reply.save_learned("YOASOBIの単価を聞かれたら、外国人向けコースの料金と取り分を両方答える", "試験さん")
ai_reply.save_learned("試験さんは挨拶程度", "試験さん")      # 名前入りは残さない
items = ai_reply.load_learned()
ok &= len(items) == 1
print("learned:", len(items))

# watch_chat.handle_ai：送信と通知の流れ（送信と通知は差し替え）
sys.modules.setdefault("booth", types.SimpleNamespace())
import watch_chat  # noqa: E402
sent_texts, notes = [], []
watch_chat.yoasobi_reply = lambda cli, shop, gid, text: (sent_texts.append(text) or True)
watch_chat.post_ntfy = lambda title, body, extra=None: notes.append(body)
watch_chat.yoasobi_record = lambda x, s: None
DISPATCH = []
watch_chat.cast_mypage.dispatch = lambda workflow="", ref="main", inputs=None: (DISPATCH.append((workflow, inputs)) or (True, "起動"))
ai_reply.history_text = lambda *a, **k: "[10/22 19:00 店] 3つだけ教えてください"
anthropic.Anthropic = fake_client(ANS)
x = {"shop": "キラキラ学園", "shopdir": "cg_kirakira", "gid": 99999999, "name": "試験まり【新入生】", "body": "①英語なら挨拶程度 ②なし ③同じで大丈夫",
     "raw": "①英語なら挨拶程度 ②なし ③同じで大丈夫", "at": "2026/10/22 18:56:00", "read": {"status": "none", "hint": ""}}
watch_chat.handle_ai(x, object())
ok &= len(sent_texts) == 1 and "設定しておくね" in sent_texts[0]
ok &= any("AIが返しました" in n for n in notes) and any("英★1" in n for n in notes)
ok &= len(DISPATCH) == 1 and DISPATCH[0][0] == "yoasobi-setup.yml" and DISPATCH[0][1]["en"] == "1" and DISPATCH[0][1]["gid"] == "99999999"
print("setup dispatch:", len(DISPATCH))
print("handle_ai: sent", len(sent_texts), "notes", len(notes))

print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
