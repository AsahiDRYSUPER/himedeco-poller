"""CTIの予約の窓の中身を読む（読むだけ。保存・編集は押さない）。2026-10-25

なぜ：YOASOBIのお客さんの予約に「送りドライバー」が入ったら、お客さんのチャットへ車のナンバー・着く時刻・合流の仕方を送るため
     （一希さん：送りドライバーの欄に「名前　番号」が入る。番号＝車のナンバー。ホテル名の後ろに「出迎え」があれば出迎えのホテル）。
     その前に、予約の窓のどこに 送りドライバー・ホテル・お客様の名前・媒体 が出ているかを確かめる。
どこに出すか：公開リポジトリなので、ログには件数だけ。中身は実行機（このMac）の中のファイルにだけ書く
     （PROBE_OUT、既定 ~/jp-probe/cti_resv.json。リポジトリの外なので公開されない）。
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from cti_schedule import CTI, JST, business_day, OFFICE

OUT = Path(os.environ.get("PROBE_OUT", str(Path.home() / "jp-probe" / "cti_resv.json")))
MAX = int(os.environ.get("PROBE_MAX", "40"))

BOX_JS = r"""() => Array.from(document.querySelectorAll('apo-resv')).map(h => {
  const it = h.querySelector('.resv-item'); if (!it) return null;
  return {rid: h.getAttribute('resv-id') || '', text: (it.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 400),
          attrs: Array.from(h.attributes).map(a => a.name + '=' + String(a.value).slice(0, 60))};
}).filter(Boolean)"""

WIN_JS = r"""() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const modals = Array.from(document.querySelectorAll('.modal, .modal-dialog, [role=dialog]')).filter(vis);
  const m = modals[modals.length - 1] || document.body;
  const pairs = [];
  // 見出し（th / dt / label / .title 等）と中身の組
  m.querySelectorAll('tr').forEach(tr => { const c = Array.from(tr.children).map(x => (x.innerText || '').replace(/\s+/g, ' ').trim()); if (c.join('').trim()) pairs.push(c.slice(0, 6)); });
  m.querySelectorAll('dl').forEach(dl => { const dt = Array.from(dl.querySelectorAll('dt')), dd = Array.from(dl.querySelectorAll('dd')); dt.forEach((d, i) => pairs.push([(d.innerText || '').trim(), ((dd[i] || {}).innerText || '').trim()])); });
  const inputs = Array.from(m.querySelectorAll('input, select, textarea')).map(i => ({n: i.name || i.id || '', t: i.type || i.tagName, v: i.tagName === 'SELECT' ? ((i.options[i.selectedIndex] || {}).text || '') : String(i.value || '').slice(0, 80),
      label: ((i.closest('label') && i.closest('label').innerText) || (i.closest('tr') && i.closest('tr').innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 60)}));
  return {text: (m.innerText || '').replace(/[ \t]+/g, ' ').slice(0, 6000), pairs: pairs.slice(0, 120), inputs: inputs.slice(0, 120)};
}"""


def main():
    cti = CTI().open()
    out = {"at": datetime.now(JST).isoformat(), "boxes": [], "windows": []}
    try:
        people, head = cti.read()
        page = cti.page
        out["head"] = head
        boxes = page.evaluate(BOX_JS)
        out["boxes"] = boxes
        print(f"予約の箱 {len(boxes)} 件（中身はファイルにだけ書く）")
        n_ok = 0
        for b in boxes[:MAX]:
            rid = b["rid"]
            if not rid:
                continue
            page.keyboard.press("Escape")
            cti._close_modals()
            loc = page.locator(f'apo-resv[resv-id="{rid}"] .resv-item')
            if not loc.count():
                continue
            try:
                loc.first.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
                page.wait_for_timeout(400)
                pt = cti._safe_point(loc)
                if pt is None:
                    continue
                page.mouse.dblclick(pt[0], pt[1])
                page.wait_for_selector("button.btn-detail:has-text('編集')", state="visible", timeout=8000)
                page.wait_for_timeout(800)
                win = page.evaluate(WIN_JS)
                out["windows"].append({"rid": rid, **win})
                n_ok += 1
            except Exception as e:
                out["windows"].append({"rid": rid, "error": type(e).__name__})
            finally:
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
        print(f"予約の窓を読めた {n_ok} 件")
    finally:
        cti.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("書き出した（実行機の中だけ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
