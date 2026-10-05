"""姫デコチャットを5分ごとに見張り、キャストから新しい連絡が来たらスマホへ通知する(ntfy)。
出勤の返事が「日にち＋時間」で揃っていれば、そのまま出勤を上げて本人に「上げました」と返す（shift_auto.py）。

なぜ必要か:
    姫デコチャットの「返事待ち」は、これまでボードを開いたときにしか分からなかった。
    連絡に気づくのが遅れると、次回の出勤を出してくれた子を待たせてしまう。
    出勤は早く上げないと損（予約が入らない）なので、迷いの無い返事は人を待たずに上げる（一希さん 2026-09-24）。

どう判断するか:
    「そのやりとりの最後の発言がキャストかどうか」で見る。ヘブンの既読フラグ(opened_flg)は、
    誰かが管理画面でその子のやりとりを開いた時点で立つので、返事をしたかどうかの判断には使えない。
    出勤の返事は、店が最後に返してから後のキャストの発言をつなげて読む（「26日 12-20時」「お願いします」と分けて送る子がいる）。
    読み分けの決まりは shift_reply.py（元: 出勤返事の読み方.md）。条件付き・曖昧なものは上げずに通知だけ。

通知の重複を防ぐ:
    一度扱ったメッセージのIDを out/chat_seen.json に残し、dataブランチで持ち回る。
    初回(まだファイルが無い)は、通知せずに現状をそのまま記録するだけ。

止めたい時: リポジトリの変数 SHIFT_AUTO を off にすると、上げずに通知だけに戻る。

お礼・了解だけの連絡は返さない。出勤の話で日にちか時間が足りない連絡には
「出られそうな日と時間が決まったら教えてください」と返す（reply_rules.py）。質問や事情・読み切れない連絡には、
10分待ってから「確認して、あらためて連絡しますね」とだけ返し、中身は人に見せる（2026-10-05〜）。
止めたい時はリポジトリの変数 AUTO_REPLY を off。

2026-09-28 から（一希さん「返信していない。今度からはちゃんと返してね」）:
    出勤予定（日にちだけ・「多分」など）→ 備考に「オキニトークでご確認ください。」を入れて、決まったら返してねと返す
    声かけへの断り（「来週は難しいです」）→ お礼を返す
    どちらも、店の直前の発言が出勤の話の時だけ（関係ない話の日付を読み違えないため）。

裏姫デコの依頼（「裏姫デコほしい」）も、ここで見つける。見つけたら非公開の cast-mypage の仕組みを起動して、
ページを作って本人に送ってもらう（cast_mypage.py。鍵が無ければ通知だけ）。

公開リポジトリで動くので、キャスト名やメッセージの中身はログに出さない(出すのは店名と件数だけ)。
chat_seen.json に入れるのもメッセージのID(ただの数字)だけで、名前も本文も入れない。
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from heaven_http import BASE, HeavenClient, LoginError, load_credentials
from shift_reply import read_shift_reply
import cast_mypage
import reply_rules
import requests_log
import booth

JST = timezone(timedelta(hours=9))
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_RAW = os.environ.get("PUBLIC_RAW", "https://raw.githubusercontent.com/AsahiDRYSUPER/himedeco-poller/data")
SEEN_FILE = OUT_DIR / "chat_seen.json"

SHOPS = {
    "cg_kirakira": "キラキラ学園", "s_matikado": "街角レディ", "mrs_orange": "オレンジな気持ち",
    "venus_okayama": "VENUS", "potya_reen": "ぽちゃりーん", "torori_angel": "とろ〜りAngel",
    "undercover": "UNDERCOVER",
}
TOP_N = 20                      # 1店舗あたり、上から何人分のやりとりを見るか
MAX_NOTIFY = 8                  # 1回にスマホへ送る通知の上限(それを超えたらまとめて1通)
SEEN_KEEP = 400                 # 覚えておくメッセージIDの数
JOIN_HOURS = 48                 # キャストの続けての発言を、何時間以内ならつなげて読むか
BOARD_URL = os.environ.get("BOARD_URL", "https://union-boards-4k7q.pages.dev/shops-08eee48153")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_HOST = os.environ.get("NTFY_HOST", "https://ntfy.sh").rstrip("/")
SHIFT_AUTO = os.environ.get("SHIFT_AUTO", "on").strip().lower() not in ("off", "0", "false", "no")
URAHIME_WORDS = ("裏姫デコ", "うら姫デコ", "ウラ姫デコ", "裏ひめデコ", "裏姫でこ", "裏姫ﾃﾞｺ")
AUTO_REPLY = os.environ.get("AUTO_REPLY", "on").strip().lower() not in ("off", "0", "false", "no")
# 個室・迎えなど、出勤を上げるだけでは終わらない頼みごと。人（一希さん）が対応する（2026-09-25 の指摘）
LOGISTICS_WORDS = ("個室", "迎え", "送迎", "待機", "寮", "出張")
# 頼みごとの中身（通知にはっきり書く。待機場所の変更は別のCTIへの入力が要るので人がやる。2026-09-30 一希さん）
LOGISTICS_KINDS = (("待機", "待機場所の変更（CTIへの入力が必要）"), ("個室", "個室（ブース管理表）"),
                   ("迎え", "迎え・送迎"), ("送迎", "迎え・送迎"), ("寮", "寮"), ("出張", "出張"))
LOGISTICS_REPLY = "個室・迎えの件は確認して連絡するね！"
# 一希さんが返し方を決める子（キャストID、金庫 NO_AUTO_IDS にカンマ区切り）。この子には何も自動で送らず、通知だけ
NO_AUTO = {int(x) for x in os.environ.get("NO_AUTO_IDS", "").replace(" ", "").split(",") if x.isdigit()}
WAIT_MIN = 10                   # 続けて送ってくる途中で返さないよう、最後の発言からこの分数は待つ
SHIFT_TALK = ("出勤", "出られ", "出れ", "◯日", "シフト")   # 店の直前の発言が出勤の話かどうか（出勤予定・断りに自動で返す条件）


def clean_name(name: str) -> str:
    """【PREMIUM】などの肩書きを名前から外す。"""
    return re.sub(r"[\[【(（].*?[\]】)）]", "", str(name or "")).strip()


def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(t or ""))).strip()


def clean_lines(t):
    """改行を残して掃除する（「28日 19-23」「30日 未定」のように行ごとに書く子がいるため）。"""
    t = re.sub(r"<br\s*/?>", "\n", str(t or ""), flags=re.I)
    t = re.sub(r"<[^>]*>", " ", t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def when(m):
    try:
        return datetime.strptime(str(m.get("create_date") or ""), "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)
    except Exception:
        return None


def load_seen():
    """前回までに扱ったメッセージIDを読み戻す。初回はNone(通知せずに記録だけする合図)。"""
    try:
        if SEEN_FILE.exists():
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")).get("ids", []))
        r = requests.get(f"{PUBLIC_RAW}/chat_seen.json?t={int(time.time())}", timeout=30)
        if r.status_code != 200:
            return None
        return set(r.json().get("ids", []))
    except Exception as e:
        print("前回の記録を読めませんでした:", type(e).__name__)
        return None


def save_seen(ids):
    """新しい順に SEEN_KEEP 件だけ残して保存する(名前も本文も入れない)。"""
    keep = sorted(ids, reverse=True)[:SEEN_KEEP]
    SEEN_FILE.write_text(json.dumps({"ids": keep, "at": datetime.now(JST).isoformat()}, ensure_ascii=False), encoding="utf-8")


def pending_for_shop(shopdir: str, now):
    """その店で「店がまだ返事をしていない」やりとりを返す。(cli, [{id, gid, name, at, body}])
    body は、店が最後に返してから後のキャストの発言をつなげたもの。"""
    account, password, direct = load_credentials(shopdir)
    cli = HeavenClient(account, password, direct=direct)
    cli.login_and_select(shopdir)

    r = cli._get(f"/C8HimedecoChat.php?shopdir={shopdir}")
    if "C1Login.php" in r.url:
        raise LoginError("姫デコチャットの画面を開けませんでした")

    girls = []
    for block in BeautifulSoup(r.text, "html.parser").select(".accordion_header"):
        m = re.search(r"girls_id=(\d+)", str(block))
        if not m:
            continue
        nm = block.select_one("p.name")
        girls.append((int(m.group(1)), clean_name(nm.get_text(strip=True) if nm else "")))
        if len(girls) >= TOP_N:
            break

    out = []
    cut = now - timedelta(hours=JOIN_HOURS)
    for gid, name in girls:
        try:
            t = cli.s.post(
                BASE + "/himedecochat/api/new_list",
                json={"girls_id": gid},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/C8HimedecoChat.php?shopdir={shopdir}"},
                timeout=30,
            )
            j = t.json()
        except Exception:
            continue
        talks = j.get("talk") or [] if j.get("result") == 0 else []
        if not talks or talks[-1].get("sent_from_flg") != 2:     # 最後が店の発言 = 返事済み
            continue
        run = []
        for m in reversed(talks):
            if m.get("sent_from_flg") != 2:
                break
            run.append(m)
        prev_shop = clean(talks[-len(run) - 1].get("body")) if len(talks) > len(run) else ""
        run.reverse()
        last = run[-1]
        run = [m for m in run if (when(m) or now) >= cut][-5:] or [last]
        body = " / ".join(b for b in (clean(m.get("body")) for m in run) if b)
        raw = " / ".join(b for b in (clean_lines(m.get("body")) for m in run) if b)   # 改行を残した形（読み分け用）
        if not body:
            continue
        out.append({"id": int(last.get("id") or 0), "gid": gid, "name": name,
                    "at": str(last.get("create_date") or ""), "body": body, "raw": raw, "prev_shop": prev_shop[:80]})
    return cli, out


def post_ntfy(title, body, extra=None):
    """スマホへ通知を送る。ntfyのトピック名は金庫(GitHub Secret)から受け取る。"""
    if not NTFY_TOPIC:
        print("通知先が未設定のため、送信は省略しました(NTFY_TOPIC)")
        return
    h = {"Click": BOARD_URL, "Tags": "speech_balloon", "Title": title}
    h.update(extra or {})
    h = {k: v.encode("utf-8") for k, v in h.items()}
    try:
        requests.post(f"{NTFY_HOST}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=h, timeout=20).raise_for_status()
    except Exception as e:
        print("通知を送れませんでした:", type(e).__name__)


def notify_one(x, result=None, urahime=None, auto=None):
    r = x["read"]
    body = x["body"][:300]
    extra = None
    if x.get("logistics"):
        what = list(dict.fromkeys(label for w, label in LOGISTICS_KINDS if w in x["body"]))
        b = x.get("booth_res")
        rest = [w for w in what if not (b and b["ok_all"] and w.startswith("個室"))]
        if b:
            body += ("\n\n✅ 個室を自動で取りました：" + booth.summary(b)) if b["ok_all"] else \
                    ("\n\n！ 個室を自動で取りきれませんでした：" + booth.summary(b) + "\n→ 取れなかった日は手で取ってください")
        if rest:
            body += ("\n\n！ 頼みごとの連絡が来ています：" + "・".join(rest) +
                     "\n→ まだ処理していません。出勤は先に上げます（上げられた時）。この件は手で対応してください" +
                     "\n→ 店舗状況ボードの「頼みごと」にも入れました。済んだらチェックしてください")
    if auto is not None:
        what, ok = auto
        if what == "ask":
            body += ("\n\n→ 日にち・時間がまだなので「" + reply_rules.ASK_TEXT + "」と自動で返しました") if ok else \
                    "\n\n！ 自動の返事が送れませんでした。手で返してください"
            extra = {"Tags": "calendar"} if ok else {"Tags": "warning", "Priority": "high"}
        elif what == "decline":
            body += ("\n\n→ 声かけへの断りなので「" + reply_rules.DECLINE_TEXT.replace("\n", " ") + "」と自動で返しました") if ok else \
                    "\n\n！ 自動の返事が送れませんでした。手で返してください"
            extra = {"Priority": "low"} if ok else {"Tags": "warning", "Priority": "high"}
        elif what == "later":
            body += ("\n\n→ あとで連絡するとのことなので「" + reply_rules.LATER_TEXT + "」と自動で返しました") if ok else \
                    "\n\n！ 自動の返事が送れませんでした。手で返してください"
            extra = {"Priority": "low"} if ok else {"Tags": "warning", "Priority": "high"}
        elif what == "thanks":
            body += "\n\n（お礼・了解のみ。返信は不要と判断）"
            extra = {"Priority": "low"}
        elif what == "hold":
            body += "\n\n→ 要返信（この子は返し方を一希さんが決める子。自動では何も送っていません）"
            extra = {"Priority": "high"}
        elif what == "need":
            body += "\n\n→ 要返信（自動では返していません）"
            extra = {"Priority": "high"}
        elif what == "hold_reply":
            body += ("\n\n→ 読み切れない連絡なので「" + reply_rules.HOLD_TEXT + "」とだけ自動で返しました。中身は手で対応してください") if ok else \
                    "\n\n！ 自動の返事が送れませんでした。手で返してください"
            extra = {"Priority": "high"} if ok else {"Tags": "warning", "Priority": "high"}
    elif urahime is not None:
        ok, msg = urahime
        extra = {"Tags": "sparkles", "Priority": "high"}
        body += "\n\n→ 裏姫デコの依頼。" + ("作成を起動しました（15分ほどで本人に届きます）" if ok else
                                      f"自動で起動できませんでした（{msg}）。クロードに「{x['name']}さんの裏姫デコ作って」と言ってください")
    elif result is not None:
        extra = {"Tags": "calendar", "Priority": "high"}
        if result["ok"] and r["status"] == "tentative":
            body += f"\n\n✅ 出勤予定（備考「オキニトークでご確認ください。」）を入れました: {result['detail']}"
            body += "\n本人に「決まったら返してね」と返信済み" if result["replied"] else \
                    "\n！ 返信だけ失敗しました。本人に一言お願いします"
        elif result["ok"]:
            body += f"\n\n✅ 出勤を上げました: {result['detail']}"
            body += "\n本人に「上げました」と返信済み" if result["replied"] else \
                    "\n！ 返信だけ失敗しました。本人に一言お願いします"
        else:
            body += f"\n\n✗ 自動で上げられませんでした: {result['detail']}\n→ 手で上げるか、クロードに「{x['name']} {r['hint']} で上げて」と言ってください"
    elif r["status"] in ("unclear", "clear"):
        extra = {"Tags": "calendar", "Priority": "high"}
        why = r["reason"] if r["status"] == "unclear" else "自動上げが止めてある"
        body += f"\n\n→ 出勤の返事かも: {r['hint']}\n（{why}ので自動では上げていません）"
        body += f"\n上げるなら「{x['name']} ◯日 ◯時〜◯時 で上げて」とクロードに言ってください"
    if x.get("logistics"):
        extra = {**(extra or {}), "Priority": "urgent", "Tags": "house"}
    post_ntfy(f'{x["shop"]} {x["name"]}', body, extra)


def main():
    now = datetime.now(JST)
    prev = load_seen()
    first_run = prev is None
    seen = set() if first_run else set(prev)

    fresh, all_ids, errors, clis = [], set(), [], {}
    for shopdir, label in SHOPS.items():
        try:
            cli, items = pending_for_shop(shopdir, now)
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}")
            continue
        clis[shopdir] = cli
        for x in items:
            all_ids.add(x["id"])
            if x["id"] not in seen:
                fresh.append({**x, "shop": label, "shopdir": shopdir})
        print(f"{label}: 返事待ち {len(items)}件")

    if errors:
        print("確認できなかった店:", " / ".join(errors))

    if first_run:
        save_seen(all_ids)
        print(f"初回のため、いまの{len(all_ids)}件は通知せずに記録しました(ここから先の新しい連絡だけ通知します)")
        return
    if not fresh:
        save_seen(all_ids | seen)
        print("新しい連絡はありません")
        return

    fresh.sort(key=lambda x: x["at"])
    for x in fresh:
        x["read"] = read_shift_reply(x.get("raw") or x["body"], now.replace(tzinfo=None), x["shopdir"])
    for x in fresh:
        x["urahime"] = any(w in x["body"] for w in URAHIME_WORDS) and x["gid"] not in NO_AUTO
        x["kind"] = "urahime" if x["urahime"] else (
            "clear" if x["read"]["status"] in ("clear", "partial") else
            reply_rules.classify(x.get("raw") or x["body"], now.replace(tzinfo=None), x["shopdir"]))
        x["logistics"] = any(w in x["body"] for w in LOGISTICS_WORDS)
        if x["gid"] in NO_AUTO:
            x["kind"] = "hold"             # 自動では何もしない（出勤も上げない・返事もしない）
        if x["logistics"] and x["kind"] in ("ask_when", "thanks", "tentative", "decline", "later"):
            x["kind"] = "other"            # 頼みごとが混ざっていたら、自動では返さず人に見せる
        if x["kind"] in ("tentative", "decline", "later") and not any(w in x.get("prev_shop", "") for w in SHIFT_TALK):
            x["kind"] = "other"            # 店が出勤の話をしていない時の日付や断りは、読み違いが怖いので人に見せる
    n_clear = sum(1 for x in fresh if x["read"]["status"] == "clear")
    n_ura = sum(1 for x in fresh if x["urahime"])
    print(f"新しい連絡 {len(fresh)}件（うち出勤の返事で条件なし {n_clear}件、裏姫デコの依頼 {n_ura}件）→ "
          + ("上げて通知します" if SHIFT_AUTO else "通知します（自動上げは止めてある）"))
    urahime = None
    if n_ura:
        urahime = cast_mypage.dispatch()
        print(f"裏姫デコの作成（cast-mypage）を起動: {'OK' if urahime[0] else 'NG ' + urahime[1]}")

    many = len(fresh) > MAX_NOTIFY
    if many:
        shops = {}
        for x in fresh:
            shops[x["shop"]] = shops.get(x["shop"], 0) + 1
        post_ntfy("姫デコチャットに新しい連絡", f"{len(fresh)}件 / " + "、".join(f"{k} {v}件" for k, v in shops.items()),
                  {"Priority": "high"})

    # 手をつけた順に記録して、途中で止まっても同じ連絡を二度扱わない
    base = set(seen)
    deferred = set()
    for x in fresh:
        kind = x["kind"]
        try:
            if kind == "hold":
                notify_one(x, auto=("hold", False))
            elif kind == "urahime":
                notify_one(x, urahime=urahime)
            elif kind == "clear" and SHIFT_AUTO:
                import shift_auto
                res = shift_auto.handle(clis.get(x["shopdir"]), x["shopdir"], x["shop"], x["gid"], x["name"],
                                        x["read"]["shifts"], now,
                                        tentative=x["read"].get("tentative", ()), display=x["read"].get("display"),
                                        extra_reply="")   # 個室・迎えのことは返事に書かない（一希さん 9/27）。通知で人が対応する
                x["shift_res"] = res
                # 個室の頼みなら、出勤を上げたあと個室も取る（2026-10-05 一希さん「個室の確保までがセット」）
                if res and res.get("ok") and "個室" in x["body"] and booth.enabled(x["shopdir"]) and x["read"]["shifts"]:
                    b = booth.book(x["shopdir"], x["name"], x["read"]["shifts"], booth.pref_of(x["body"]))
                    x["booth_res"] = b
                    print(f'{x["shop"]}: 個室の自動予約 {sum(1 for i in b["items"] if i["ok"])}/{len(b["items"])}')
                    # 本人には個室のことは送らない（当日までに時間を組み合わせて部屋を入れ替えることがあるため。一希さん 10/5）
                notify_one(x, res)
            elif kind == "ask_when" and AUTO_REPLY:
                age = now - (when({"create_date": x["at"]}) or now)
                if age < timedelta(minutes=WAIT_MIN):
                    deferred.add(x["id"])          # 続きが来るかもしれないので次の回に回す
                    continue
                cli = clis.get(x["shopdir"])
                if "決まったら教えてください" in x.get("prev_shop", "") or cli is None:
                    notify_one(x, auto=("need", False))   # 二度は同じ文を返さない
                else:
                    import shift_auto
                    ok = shift_auto.reply(cli, x["shopdir"], x["gid"], reply_rules.ask_text(shift_auto.yobi(x["name"])))
                    print(f'{x["shop"]}: 日にち・時間を聞く返事を自動送信 {"OK" if ok else "NG"}')
                    notify_one(x, auto=("ask", ok))
            elif kind in ("tentative", "decline", "later") and AUTO_REPLY and (SHIFT_AUTO or kind != "tentative"):
                age = now - (when({"create_date": x["at"]}) or now)
                if age < timedelta(minutes=WAIT_MIN):
                    deferred.add(x["id"])          # 続きが来るかもしれないので次の回に回す
                    continue
                import shift_auto
                cli = clis.get(x["shopdir"])
                if kind == "tentative":
                    res = shift_auto.handle(cli, x["shopdir"], x["shop"], x["gid"], x["name"], [], now,
                                            tentative=x["read"].get("tentative", ()), display=x["read"].get("display"))
                    notify_one(x, res)
                elif kind == "later":
                    if reply_rules.LATER_TEXT[:8] in x.get("prev_shop", "") or cli is None:
                        notify_one(x, auto=("need", False))   # 二度は同じ文を返さない
                    else:
                        ok = shift_auto.reply(cli, x["shopdir"], x["gid"], reply_rules.LATER_TEXT)
                        print(f'{x["shop"]}: 「分かりました」を自動送信 {"OK" if ok else "NG"}')
                        notify_one(x, auto=("later", ok))
                elif "お返事ありがとうございます" in x.get("prev_shop", "") or cli is None:
                    notify_one(x, auto=("need", False))   # 二度は同じお礼を返さない
                else:
                    ok = shift_auto.reply(cli, x["shopdir"], x["gid"], reply_rules.decline_text(shift_auto.yobi(x["name"])))
                    print(f'{x["shop"]}: 断りへのお礼を自動送信 {"OK" if ok else "NG"}')
                    notify_one(x, auto=("decline", ok))
            elif kind == "thanks":
                if not many:
                    notify_one(x, auto=("thanks", True))
            elif kind in ("question", "other") and AUTO_REPLY:
                # 読み切れない連絡でも、女の子を待たせない：まず「確認して連絡します」とだけ返し、中身は人へ（2026-10-05）
                age = now - (when({"create_date": x["at"]}) or now)
                if age < timedelta(minutes=WAIT_MIN):
                    deferred.add(x["id"])          # 続きが来るかもしれないので次の回に回す
                    continue
                cli = clis.get(x["shopdir"])
                if reply_rules.HOLD_TEXT[:10] in x.get("prev_shop", "") or cli is None:
                    notify_one(x, auto=("need", False))   # 同じ受け取りの返事を二度は送らない
                else:
                    import shift_auto
                    ok = shift_auto.reply(cli, x["shopdir"], x["gid"], reply_rules.HOLD_TEXT)
                    print(f'{x["shop"]}: 「確認して連絡します」を自動送信 {"OK" if ok else "NG"}')
                    notify_one(x, auto=("hold_reply", ok))
            elif not many:
                notify_one(x, auto=("need", False))
        except Exception as e:
            print(f"{x['shop']}: 扱えませんでした {type(e).__name__}")
            try:
                post_ntfy(f'{x["shop"]} {x["name"]}', x["body"][:300] + f"\n\n！ 自動の処理で失敗（{type(e).__name__}）。手で確認してください",
                          {"Priority": "high"})
            except Exception:
                pass
        base.add(x["id"])
        save_seen(base)
    # 個室・待機場所・迎えなどの頼みごとは、済むまでボードの「頼みごと」に残す（2026-10-01）
    reqs = []
    for x in fresh:
        if not x.get("logistics") or x["id"] in deferred:
            continue
        r = x.get("shift_res")
        reqs.append({
            "id": x["id"], "shop": x["shop"], "shopdir": x["shopdir"], "name": x["name"], "gid": x["gid"], "at": x["at"],
            "kinds": list(dict.fromkeys(label for w, label in LOGISTICS_KINDS if w in x["body"])),
            "when": " / ".join(x["read"].get("display") or []) or x["read"].get("hint") or "",
            "shift": ("出勤は自動で上げた" if r and r.get("ok") else
                      "出勤は上げられなかった" if r else "出勤は自動では上げていない") +
                     (("／個室は自動で取った（" + booth.summary(x["booth_res"]) + "）") if x.get("booth_res") and x["booth_res"]["ok_all"] else
                      ("／個室は一部取れず（" + booth.summary(x["booth_res"]) + "）") if x.get("booth_res") else ""),
            "body": x["body"][:300],
        })
    try:
        requests_log.record(reqs)
    except Exception as e:
        print("頼みごとの記録に失敗:", type(e).__name__)
    save_seen((base | all_ids) - deferred)


if __name__ == "__main__":
    main()
