"""非公開リポジトリ cast-mypage の仕組み（裏姫デコの作成）を、必要な時だけ起動する。

なぜ: cast-mypage は非公開で、GitHub Actions の実行時間に上限がある（無料枠は月2,000分）。
      15分ごとに向こうで見に行く形だと枠を使い切るので、見張りは公開側（このリポジトリ、5分ごと・無料）で行い、
      依頼があった時だけ向こうを起動する。
鍵:   Secret CAST_MYPAGE_TOKEN（一希さんが GitHub で作った細かい権限のトークン。cast-mypage の Actions 書き込みだけ）。
      ログには鍵も返事の中身も出さない。
"""
import os
import sys

import requests

REPO = "AsahiDRYSUPER/cast-mypage"
TOKEN = os.environ.get("CAST_MYPAGE_TOKEN", "").strip()


def dispatch(workflow="urahime-auto.yml", ref="main", inputs=None):
    """向こうのワークフローを起動する。返す: (できたか, 短い説明)"""
    if not TOKEN:
        return False, "鍵（CAST_MYPAGE_TOKEN）が未設定"
    body = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    try:
        r = requests.post(
            f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches",
            headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json=body, timeout=30,
        )
    except Exception as e:
        return False, type(e).__name__
    if r.status_code == 204:
        return True, "起動しました"
    hint = {401: "鍵が無効", 403: "鍵の権限が足りない", 404: "鍵の対象に cast-mypage が無い"}.get(r.status_code, "")
    return False, f"HTTP {r.status_code}" + (f"（{hint}）" if hint else "")


if __name__ == "__main__":
    if "--test" in sys.argv:
        if not TOKEN:
            print("鍵（CAST_MYPAGE_TOKEN）が未設定なので、起動の試験は飛ばします")
            sys.exit(0)
        ok, msg = dispatch()
        print(("✅ " if ok else "✗ ") + f"cast-mypage の裏姫デコ作成を起動: {msg}")
        sys.exit(0 if ok else 1)
    print("watch_chat.py から呼ばれます")
