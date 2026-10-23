#!/usr/bin/env bash
# out/ の3つのファイルを、dataブランチ(履歴なし・毎回1コミット)へ強制的に反映する。
set -e
rm -rf /tmp/pub && mkdir /tmp/pub
# 今回作っていないファイルは、dataブランチの「今」のものを引き継ぐ。raw.githubusercontent.com は数分古いことがあるので、まず API で取る
# （2026-10-23 日本側の公開（jp-sokuhime）が chat_seen.json・声かけリスト・頼みごとを引き継がずに消していた → チャットが毎回「初回」になり返信が止まった）
keep() { cp "out/$1" /tmp/pub/ 2>/dev/null && return 0
  if [ -n "${GH_TOKEN:-}" ] && curl -fsS -H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github.raw" \
       "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/$1?ref=data" -o "/tmp/pub/$1" 2>/dev/null; then return 0; fi
  rm -f "/tmp/pub/$1"
  curl -fsS "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/data/$1" -o "/tmp/pub/$1" 2>/dev/null && return 0
  rm -f "/tmp/pub/$1"; return 1; }
# 手で動かす回（si9-check）ではボードのデータを作らないので、無ければ公開されている今のものを引き継ぐ
for f in shops.json next_shifts.json; do keep $f; done
keep todo.enc.json || true
keep requests.enc.json || true   # 頼みごと(個室・待機場所・迎え)。名前を含むので暗号化済み
keep chat_seen.json || true
# AIが学んだ一般的な決まり（名前なし）。今回書けていなければ前のものを引き継ぐ
keep ai_learned.json || true
# 夜遊びチャットの見張りの記録（スレッドの印と時刻だけ）。今回書けていなければ前のものを引き継ぐ
keep yoasobi_state.json || true
# 外国人OKの申し出の控え（キャストID・店・時刻・状態だけ）。今回書けていなければ前のものを引き継ぐ
keep yoasobi_ok.json || true
# 即ヒメに時間を付けた記録(名前を含むので暗号化済み)。今回書けていなければ前のものを引き継ぐ
keep sokuhime.enc.json || true
# 即ヒメの「生きている印」（名前なし。最終確認の時刻と件数）
keep sokuhime_status.json || true   # 通知済みメッセージのID(数字のみ。名前も本文も入っていない)
# 新着んの並び替えの記録（名前なし。時刻・人数・送った結果の数）。今回書けていなければ前のものを引き継ぐ（10分あける判断に使う）
keep si9_status.json || true
# 店舗運営KPI用の日ごとの人数(店ごとの数字だけ)。今回書けていなければ、前のものを引き継ぐ(消えると後から記録できない)
keep kpi_history.json || true
cd /tmp/pub
git init -q -b data
git config user.name "collect-bot"
git config user.email "actions@github.com"
git add .
git commit -qm "data"
git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
