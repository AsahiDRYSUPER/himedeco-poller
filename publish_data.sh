#!/usr/bin/env bash
# out/ の3つのファイルを、dataブランチ(履歴なし・毎回1コミット)へ強制的に反映する。
set -e
rm -rf /tmp/pub && mkdir /tmp/pub
cp out/shops.json out/next_shifts.json /tmp/pub/
cp out/todo.enc.json /tmp/pub/ 2>/dev/null || true
cp out/requests.enc.json /tmp/pub/ 2>/dev/null || true   # 頼みごと(個室・待機場所・迎え)。名前を含むので暗号化済み
cp out/chat_seen.json /tmp/pub/ 2>/dev/null || true
# 即ヒメに時間を付けた記録(名前を含むので暗号化済み)。今回書けていなければ前のものを引き継ぐ
cp out/sokuhime.enc.json /tmp/pub/ 2>/dev/null || curl -fsS "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/data/sokuhime.enc.json" -o /tmp/pub/sokuhime.enc.json 2>/dev/null || true
# 即ヒメの「生きている印」（名前なし。最終確認の時刻と件数）
cp out/sokuhime_status.json /tmp/pub/ 2>/dev/null || curl -fsS "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/data/sokuhime_status.json" -o /tmp/pub/sokuhime_status.json 2>/dev/null || true   # 通知済みメッセージのID(数字のみ。名前も本文も入っていない)
# 新着んの並び替えの記録（名前なし。時刻・人数・送った結果の数）。今回書けていなければ前のものを引き継ぐ（10分あける判断に使う）
cp out/si9_status.json /tmp/pub/ 2>/dev/null || curl -fsS "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/data/si9_status.json" -o /tmp/pub/si9_status.json 2>/dev/null || true
# 店舗運営KPI用の日ごとの人数(店ごとの数字だけ)。今回書けていなければ、前のものを引き継ぐ(消えると後から記録できない)
cp out/kpi_history.json /tmp/pub/ 2>/dev/null || curl -fsS "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/data/kpi_history.json" -o /tmp/pub/kpi_history.json 2>/dev/null || true
cd /tmp/pub
git init -q -b data
git config user.name "collect-bot"
git config user.email "actions@github.com"
git add .
git commit -qm "data"
git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
