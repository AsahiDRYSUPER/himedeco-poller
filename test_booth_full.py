"""空きが無かった日だけ本人に伝える文になるか（通信なし）。python3 test_booth_full.py"""
from datetime import date

import booth

booth.FULL_TEXT = "{days}:X"
a, b, c = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)
item = lambda d, ok, err="": {"day": d, "ok": ok, "booth": "A1" if ok else "", "already": False, "error": err}
assert booth.full_text({"ok_all": True, "items": [item(a, True)]}) == ""
assert booth.full_text({"ok_all": False, "items": [item(a, False, "full")]}) == "5日:X"
assert booth.full_text({"ok_all": False, "items": [item(a, True), item(b, False, "no slot"), item(c, False, "full_1f")]}) == "6日・7日:X"
# 通信の失敗などは本人に言わない（人が手で取れるかもしれない）
for err in ("ConnectionError", "exception", "busy", "forbidden", "past", ""):
    assert booth.full_text({"ok_all": False, "items": [item(a, False, err)]}) == "", err
assert booth.full_text(None) == ""
booth.FULL_TEXT = ""
assert booth.full_text({"ok_all": False, "items": [item(a, False, "full")]}) == ""
print("OK")
