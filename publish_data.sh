#!/usr/bin/env bash
# out/ の3つのファイルを、dataブランチ(履歴なし・毎回1コミット)へ強制的に反映する。
set -e
rm -rf /tmp/pub && mkdir /tmp/pub
cp out/shops.json out/next_shifts.json /tmp/pub/
cp out/todo.enc.json /tmp/pub/ 2>/dev/null || true
cp out/chat_seen.json /tmp/pub/ 2>/dev/null || true   # 通知済みメッセージのID(数字のみ。名前も本文も入っていない)
cd /tmp/pub
git init -q -b data
git config user.name "collect-bot"
git config user.email "actions@github.com"
git add .
git commit -qm "data"
git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
