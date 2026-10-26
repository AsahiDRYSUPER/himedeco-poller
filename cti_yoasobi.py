"""即ヒメ直しが開いた CTI のついでに、夜遊び（YOASOBIヘブン）の2つの仕事をする（日本の実行機）。2026-10-25

1. 空き状況の写し：対象の店の女の子の出勤と予約の時間を、暗号化して out/cti_avail.enc.json に残す
   （公開の dataブランチに出るが、鍵 TODO_KEY が無いと読めない）。お客さんのチャットの見張りが使う。
2. 配車の連絡：対象の予約を見分けて、決まりに合ったら非公開側（cast-mypage）の仕組みを起動する。
   見分けの言葉は鍵つき（yoasobi_templates の "dispatch_match"）。開けない時は配車の連絡をしない。
   読んだ中身は公開のログに出さない。送った印は実行機（Mac）の中のファイルにだけ残す。
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
YO_SHOPS = ("cg_kirakira", "s_matikado", "mrs_orange", "potya_reen")
OUT_DIR = Path(os.environ.get("OUT_DIR", "out"))
AVAIL_NAME = "cti_avail"   # 空き状況の写し（鍵つき out/cti_avail.enc.json。data_box で書く）
STATE = Path(os.environ.get("CTI_YOASOBI_STATE", str(Path.home() / "jp-state" / "cti_yoasobi.json")))
MAX_OPEN = int(os.environ.get("CTI_YOASOBI_MAX_OPEN", "12"))       # 1回で開く予約の窓の数の上限（即ヒメ直しを遅らせない）
LOOK_AHEAD = int(os.environ.get("CTI_YOASOBI_AHEAD_MIN", "180"))   # 何分先までの予約を見るか
CAST_REPO = "AsahiDRYSUPER/cast-mypage"


def dispatch_match():
    """配車の連絡の見分けの言葉（鍵つき）。返す: {"customer_prefix", "course_word", "self_drive"} か None（開けない・足りない）"""
    try:
        import secret_box
        m = (secret_box.data("yoasobi_templates") or {}).get("dispatch_match") or {}
    except Exception:
        return None
    if not isinstance(m, dict) or not all(isinstance(m.get(k), str) and m.get(k) for k in ("customer_prefix", "course_word", "self_drive")):
        return None
    return m

WIN_JS = r"""() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const modals = Array.from(document.querySelectorAll('.modal, .modal-dialog, [role=dialog]')).filter(vis);
  const m = modals[modals.length - 1] || document.body;
  return (m.innerText || '').replace(/[ \t]+/g, ' ');
}"""


def _now_min(now):
    m = now.hour * 60 + now.minute
    return m + 24 * 60 if m < 5 * 60 else m


def _hhmm(m):
    m = int(m)
    return f"{(m // 60) % 24}:{m % 60:02d}"


# ---------- 1. 空き状況の写し（暗号化） ----------
def save_avail(people, now, head):
    import data_box   # 鍵かけは dataブランチの他の記録と同じ部品で（2026-10-25）
    if not data_box.ready():
        return 0
    girls = []
    for p in people:
        if p.get("shop") not in YO_SHOPS or not p.get("name"):
            continue
        girls.append({"shop": p["shop"], "name": p["name"], "work": list(p["work"]) if p.get("work") else None,
                      "up": bool(p.get("up")), "absent": bool(p.get("absent")),
                      "busy": [[b["s"], b["e"]] for b in p.get("bookings", []) if b.get("s") is not None]})
    payload = {"generated_at": now.isoformat(), "day": head, "girls": girls}
    if not data_box.write(AVAIL_NAME, payload, OUT_DIR, generated_at=now.isoformat()):
        return 0
    return len(girls)


# ---------- 2. 配車の連絡 ----------
def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"done": {}, "not_yo": {}}


def save_state(st, now):
    cut = (now - timedelta(days=3)).isoformat()
    st["done"] = {k: v for k, v in st.get("done", {}).items() if v >= cut}
    st["not_yo"] = {k: v for k, v in st.get("not_yo", {}).items() if v >= cut}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def parse_window(text, prefix=""):
    """予約の窓の文 → {顧客, 国, 日時, 料金, 料金システム, ホテル, 部屋, 送りﾄﾞﾗｲﾊﾞｰ, 女子}。
    prefix＝お客さんの名前の頭の印（鍵つき）。名前が「印（国）」の形なら国を取る"""
    out = {}
    i = text.rfind("顧客データ\n")
    if i >= 0:
        for ln in text[i:i + 400].split("\n")[1:6]:
            if "(利用" in ln:
                out["顧客"] = ln.split("(利用")[0].strip()
                break
    m = re.search(re.escape(prefix) + r"[（(]([^)）]+)[)）]", out.get("顧客", "")) if prefix else None
    out["国"] = m.group(1) if m else ""
    j = text.find("予約データ\n日時")
    blk = text[j:text.find("女子メモ", j)] if j >= 0 else ""
    for ln in blk.split("\n"):
        if "：" in ln:
            k, _, v = ln.partition("：")
            out[k.strip()] = v.strip()
    return out


def read_window(cti, rid, prefix=""):
    page = cti.page
    page.keyboard.press("Escape")
    try:
        cti._close_modals()
    except Exception:
        pass
    loc = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
    if not loc.count():
        return None
    try:
        loc.first.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
        page.wait_for_timeout(400)
        pt = cti._safe_point(loc)
        if pt is None:
            return None
        page.mouse.dblclick(pt[0], pt[1])
        page.wait_for_selector("button.btn-detail:has-text('編集')", state="visible", timeout=8000)
        page.wait_for_timeout(700)
        return parse_window(page.evaluate(WIN_JS) or "", prefix)
    except Exception:
        return None
    finally:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)


def dispatch(info, shop, girl, start_min, send):
    token = os.environ.get("CAST_MYPAGE_TOKEN", "").strip()
    if not token:
        return False, "鍵（CAST_MYPAGE_TOKEN）が無い"
    drv = re.sub(r"\s+", " ", info.get("送りﾄﾞﾗｲﾊﾞｰ", "").replace("　", " ")).strip()
    plate = drv.split(" ", 1)[1].strip() if " " in drv else ""
    m = re.search(r"(翌)?(\d{1,2}):(\d{2})\s*-", info.get("日時", ""))
    start = f"{int(m.group(2))}:{m.group(3)}" if m else _hhmm(start_min)
    inputs = {"shop": shop, "girl": girl, "start": start, "hotel": info.get("ホテル", ""), "room": info.get("部屋", ""),
              "plate": plate, "country": info.get("国", ""), "course": info.get("料金システム", ""),
              "total": info.get("料金", ""), "send": "on" if send else "off"}
    try:
        r = requests.post(f"https://api.github.com/repos/{CAST_REPO}/actions/workflows/yoasobi-dispatch.yml/dispatches",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"},
                          json={"ref": "main", "inputs": inputs}, timeout=30)
        return r.status_code == 204, f"HTTP {r.status_code}"
    except Exception as e:
        return False, type(e).__name__


def handle(cti, now, dry=False):
    """即ヒメ直しのあと、CTI を閉じる前に呼ばれる。ログには件数だけ出す。"""
    people, head = cti.read(now)
    n_avail = save_avail(people, now, head)
    mt = dispatch_match()
    if mt is None:
        print(f"夜遊び：空き状況の写し {n_avail}人／配車の見分けの言葉（鍵つき）が開けないので、配車の連絡はしない")
        return
    st = load_state()
    nm = _now_min(now)
    send = (os.environ.get("AUTO_YOASOBI", "off").strip().lower() in ("on", "1", "true", "yes")) and not dry
    cands = []
    for p in people:
        if p.get("shop") not in YO_SHOPS:
            continue
        for b in p.get("bookings", []):
            rid = b.get("rid")
            if not rid or rid in st["done"] or rid in st["not_yo"]:
                continue
            if any(f in b.get("flags", []) for f in ("終了", "入室")):
                continue
            if b.get("s") is None or not (nm - 15 <= b["s"] <= nm + LOOK_AHEAD):
                continue
            cands.append((p, b))
    cands.sort(key=lambda x: x[1]["s"])
    opened = found = sent = waiting = 0
    for p, b in cands:
        if opened >= MAX_OPEN:
            break
        info = read_window(cti, b["rid"], mt["customer_prefix"])
        opened += 1
        if info is None:
            continue
        is_yo = (mt["course_word"] in info.get("料金システム", "")) or info.get("顧客", "").upper().startswith(mt["customer_prefix"].upper())
        if not is_yo:
            st["not_yo"][b["rid"]] = now.isoformat()
            continue
        found += 1
        drv = info.get("送りﾄﾞﾗｲﾊﾞｰ", "").strip()
        if not drv or drv == mt["self_drive"] or not re.search(r"[\s　]\S", drv):
            waiting += 1                 # まだ決まっていない・送れる形でない
            continue
        ok, why = dispatch(info, p["shop"], p["name"], b["s"], send)
        if ok:
            st["done"][b["rid"]] = now.isoformat()
            sent += 1
        else:
            print(f"  夜遊びの配車の連絡を起動できない（{why}）")
    save_state(st, now)
    print(f"夜遊び：空き状況の写し {n_avail}人／対象の予約 {found}件（開いた窓 {opened}）→ 配車の連絡を起動 {sent}件・待ち {waiting}件"
          + ("（送信はOFF。文を作るだけ）" if not send else ""))
