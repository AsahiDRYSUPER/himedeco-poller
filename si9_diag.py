"""新着んの実際の様子を読むだけ（保存も送信もしない）。2026-10-23 「新着ん動いてなくない？」の確かめ用。
店ごとに：更新結果（並び替え）の直近の行の時刻とサイト、いちばん新しい回の成功・失敗の数、
is_sorting の返事（更新結果の画面から／並び替えの画面から）を出す。女の子の名前は出さない。"""
import sys
from datetime import datetime
import si9_sort as s


def main():
    shops = [x for x in (sys.argv[1:] or list(s.SHOPS)) if x in s.SHOPS]
    from playwright.sync_api import sync_playwright
    now = datetime.now(s.JST)
    print(f"いま {now.strftime('%H:%M')}")
    with sync_playwright() as p:
        for sd in shops:
            name, no = s.SHOPS[sd]
            b = p.chromium.launch(headless=True)
            try:
                page = b.new_context(locale="ja-JP", timezone_id="Asia/Tokyo").new_page()
                page.on("dialog", lambda d: d.dismiss())
                s.si9_login(page, name)
                rows = s.read_result_rows(page)
                raw1 = page.evaluate("""async () => { try { const r = await $.ajax({type: 'POST', url: '/ajax/girl/data.php', data: {method: 'is_sorting'}});
                    return typeof r === 'string' ? r : JSON.stringify(r); } catch (e) { return 'ERR ' + e.status; } }""")
                times = []
                for h, m, site, _ in rows:
                    t = f"{h:02d}:{m:02d}"
                    if t not in times:
                        times.append(t)
                print(f"== {sd}: 更新結果の行 {len(rows)}・送った時刻（新しい順）{times[:12]}")
                if rows:
                    h, m = rows[0][0], rows[0][1]
                    latest = [r for r in rows if (r[0], r[1]) == (h, m)]
                    print(f"   いちばん新しい回 {h:02d}:{m:02d} のサイト {[r[2] for r in latest]}")
                    for h2, m2, site, href in latest[:10]:
                        page.goto(href, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(1500)
                        n = page.evaluate("""() => { const n = {}; const words = ['成功', '失敗', '対象外', 'エラー', '処理中', '待機'];
                            const cells = document.querySelectorAll('td.girl_result_label');
                            for (const td of (cells.length ? cells : document.querySelectorAll('table tr'))) {
                              const t = (td.innerText || '').replace(/\\s+/g, '');
                              const w = words.find(x => t.startsWith(x)); if (w) n[w] = (n[w] || 0) + 1; } return n; }""")
                        print(f"     {site}: {n}")
                print(f"   is_sorting（更新結果の画面から）: {str(raw1)[:80]}")
                page.goto(f"{s.BASE}/girl/sort.php", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                raw2 = page.evaluate("""async () => { try { const r = await $.ajax({type: 'POST', url: '/ajax/girl/data.php', data: {method: 'is_sorting'}});
                    return typeof r === 'string' ? r : JSON.stringify(r); } catch (e) { return 'ERR ' + e.status; } }""")
                print(f"   is_sorting（並び替えの画面から）: {str(raw2)[:80]}")
            except Exception as e:
                print(f"== {sd}: 読めない {s.safe_err(e, 120)}")
            finally:
                b.close()


if __name__ == "__main__":
    main()
