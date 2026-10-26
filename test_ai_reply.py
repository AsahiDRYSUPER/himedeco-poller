"""ai_reply.py と watch_chat.handle_ai の試験（本物のAPIもヘブンも呼ばない）。店・名前・文はすべて作りもの。"""
import base64
import json
import os
import sys
import types

os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
os.environ["OUT_DIR"] = "out_test"
os.environ["AI_PRIVATE_NOTES"] = "試験の非公開メモ"   # お客さん向けには渡さないことを確かめる目印
os.environ.setdefault("TODO_KEY", base64.b64encode(os.urandom(32)).decode())   # 学んだことは鍵つきで書く（試し用の鍵）
os.makedirs("out_test", exist_ok=True)
# 本番の学んだこと（dataブランチ）を読みに行かないよう、最初から空の控えを置き、前の試しの残り（別の鍵）は消す
open("out_test/ai_learned.json", "w").write('{"items": []}')
if os.path.exists("out_test/ai_learned.enc.json"):
    os.remove("out_test/ai_learned.enc.json")

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
import secret_box  # noqa: E402


def learned_ok():
    """学んだこと：鍵つき（ai_learned.enc.json）で書いて読める・名前入りは残さない・平文は残さない"""
    open("out_test/ai_learned.json", "w").write('{"items": []}')   # 本番の学んだこと（dataブランチ）を読みに行かないよう、空の控えを置く
    if os.path.exists("out_test/ai_learned.enc.json"):
        os.remove("out_test/ai_learned.enc.json")                    # 前の試しの残り（別の鍵）を消す
    ai_reply.save_learned("決まりの例：短く答える", "試験さん")
    ai_reply.save_learned("試験さんは試験中", "試験さん")      # 名前入りは残さない
    items = ai_reply.load_learned()
    print("learned:", len(items))
    ok_ = len(items) == 1 and os.path.exists("out_test/ai_learned.enc.json") and not os.path.exists("out_test/ai_learned.json")
    # 前の記録が「有るのに開けない」時は書かない（空から作り直して上書きしない）
    open("out_test/ai_learned.enc.json", "w").write('{"iv": "AAAA", "ct": "AAAA"}')
    before = open("out_test/ai_learned.enc.json").read()
    ai_reply.save_learned("決まりの例：二つ目", "試験さん")
    ok_ &= open("out_test/ai_learned.enc.json").read() == before
    os.remove("out_test/ai_learned.enc.json")
    # まだ一度も書いていない（どこにも無い）時だけ空から始める
    import data_box
    real_fetch = data_box.fetch
    data_box.fetch = lambda name, base=None, timeout=20: ("missing", None)
    ai_reply.save_learned("決まりの例：三つ目", "試験さん")
    ok_ &= [i["note"] for i in ai_reply.load_learned()] == ["決まりの例：三つ目"]
    data_box.fetch = lambda name, base=None, timeout=20: ("error", None)
    os.remove("out_test/ai_learned.enc.json")
    ai_reply.save_learned("決まりの例：四つ目", "試験さん")
    ok_ &= not os.path.exists("out_test/ai_learned.enc.json")          # つながらない時は書かない
    data_box.fetch = real_fetch
    return ok_


if not secret_box.text("ai_knowledge") or not ai_reply.rules_ready():
    # 鍵（KNOWLEDGE_KEY）が無い時：AI は「使わない」に倒れることと、学んだことの書き読みだけ確かめる
    d0, w0 = ai_reply.ask("試験店", "shop_test", "試験さん", "", "テスト")
    ok0 = d0 is None and w0 == "off" and not CALLS
    ok0 &= learned_ok()
    print("鍵なし: AIは使わない", "ALL OK" if ok0 else "FAILED")
    sys.exit(0 if ok0 else 1)

ANS = {"action": "reply", "reply": "試験さん、試験の返事です。",
       "owner_note": "", "learned": "",
       "yoasobi": {"is_answer": True, "complete": True, "wants_listed": False, "lang_en": "1", "lang_zh": "0", "lang_kr": "0", "ng": [], "photo": "same", "all_options_ok": True, "three_p": "ok", "qa": {"q1": "試験の答え", "q2": "", "q3": "", "q4": "", "q5": "", "q6": "", "q7": ""}}}

ok = True

anthropic.Anthropic = fake_client(ANS)
data, why = ai_reply.ask("試験店", "shop_test", "試験さん", "[10/22 19:00 店] 試験の質問です", "試験の答えです")
ok &= (why == "ok" and data["action"] == "reply")
kw = CALLS[-1]
ok &= kw["model"] == "claude-opus-5-5"
ok &= kw.get("fallbacks") == "default" and "server-side-fallback-2026-07-01" in kw.get("betas", [])
ok &= kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "low"
ok &= secret_box.text("ai_knowledge")[:30] in kw["system"][0]["text"] and ai_reply.CAST_RULES[:30] in kw["system"][0]["text"]
ok &= kw["output_config"]["format"]["schema"] == ai_reply.CAST_SCHEMA
ok &= "試験の非公開メモ" in kw["system"][0]["text"]
print("ask:", why, "fallbacks:", kw.get("fallbacks"))

send, w = ai_reply.check(data)
ok &= send
long = dict(ANS, reply="あ" * 600)
ok &= ai_reply.check(long) == (False, "長すぎる")
ok &= ai_reply.check(dict(ANS, action="no_reply"))[0] is False
print("check:", send, w)

anthropic.Anthropic = fake_client(ANS, stop="refusal")
d2, why2 = ai_reply.ask("試験店", "shop_test", "試験さん", "", "テスト")
ok &= d2 is None and why2 == "断られた"
print("refusal:", why2)

ok &= learned_ok()

# watch_chat.handle_ai：送信と通知の流れ（送信と通知は差し替え）
sys.modules.setdefault("booth", types.SimpleNamespace())
import watch_chat  # noqa: E402
import heaven_http  # noqa: E402
heaven_http.COMMU_IDS["shop_test"] = 0                 # 作りものの店
watch_chat.YOASOBI_SHOPS = ("shop_test",)
sent_texts, notes = [], []
watch_chat.yoasobi_reply = lambda cli, shop, gid, text: (sent_texts.append(text) or True)
watch_chat.post_ntfy = lambda title, body, extra=None: notes.append(body)
watch_chat.yoasobi_record = lambda x, s: None
DISPATCH = []
watch_chat.cast_mypage.dispatch = lambda workflow="", ref="main", inputs=None: (DISPATCH.append((workflow, inputs)) or (True, "起動"))
ai_reply.history_text = lambda *a, **k: "[10/22 19:00 店] 試験の質問です"
anthropic.Anthropic = fake_client(ANS)
x = {"shop": "試験店", "shopdir": "shop_test", "gid": 99999999, "name": "試験まり【新入生】", "body": "試験の答えです",
     "raw": "試験の答えです", "at": "2026/10/22 18:56:00", "read": {"status": "none", "hint": ""}}
watch_chat.handle_ai(x, object())
ok &= len(sent_texts) == 1 and "試験の返事" in sent_texts[0]
ok &= any("AIが返しました" in n for n in notes) and any("英★1" in n for n in notes)
ok &= len(DISPATCH) == 1 and DISPATCH[0][0] == "yoasobi-setup.yml" and DISPATCH[0][1]["en"] == "1" and DISPATCH[0][1]["gid"] == "99999999"
ok &= "試験の答え" in DISPATCH[0][1]["qa"] and DISPATCH[0][1]["courses"] == "3p_ok,all_ok"
print("setup dispatch:", len(DISPATCH))
print("handle_ai: sent", len(sent_texts), "notes", len(notes))

print("cast:", "ALL OK" if ok else "FAILED")

# お客さん（夜遊び）向け：決まりの中なら reply、判断が要るなら draft、送ってよい形の確かめ
CUST = {"category": "A", "action": "reply", "reply": "試験のお客さん向けの返事です。", "owner_note": "", "reason": "試験"}
anthropic.Anthropic = fake_client(CUST)
c, whyc = ai_reply.ask_customer("試験店", "サンプル太郎", "[客] 試験の質問です", "試験の質問の続きです")
ok2 = (whyc == "ok" and c["category"] == "A")
ok2 &= secret_box.text("yoasobi_knowledge")[:30] in CALLS[-1]["system"][0]["text"] and ai_reply.CUSTOMER_RULES[:30] in CALLS[-1]["system"][0]["text"]
ok2 &= "試験の非公開メモ" not in CALLS[-1]["system"][0]["text"]            # 女の子向けの非公開メモは渡さない
ok2 &= CALLS[-1]["output_config"]["format"]["schema"] == ai_reply.CUSTOMER_SCHEMA
ok2 &= ai_reply.check_customer(c) == (True, "ok")
ok2 &= ai_reply.check_customer(dict(CUST, category="B", action="draft"))[0] is False
ok2 &= ai_reply.check_customer(dict(CUST, reply="特別に割引します"))[0] is False
print("customer:", whyc, "ALL OK" if ok2 else "FAILED")
sys.exit(0 if (ok and ok2) else 1)
