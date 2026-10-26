#!/usr/bin/env bash
# out/ のファイルを、dataブランチ(履歴なし・毎回1コミット)へ強制的に反映する。
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
keep next_shifts.json
# 鍵つきで出す6つ（2026-10-25〜。data_box.py。鍵は金庫の TODO_KEY）。平文の名前（X.json）はもう引き継がない＝dataブランチから消える。
#   shops（ボードの店ごとの数）・kpi_history（日ごとの店別の数）・si9_status（新着んの記録）・sokuhime_status（即ヒメの生きている印）
#   yoasobi_ok（外国人OKの控え）・ai_learned（AIが学んだ一般的な決まり）
# 切り替え直後でまだ X.enc.json が無い時だけ、前の平文 X.json を引き継いでその場で鍵をかける（鍵が無ければ平文のまま残す＝消さない）
for n in shops kpi_history si9_status sokuhime_status yoasobi_ok ai_learned; do
  keep "$n.enc.json" && continue
  if keep "$n.json"; then
    if "${PY:-python3}" data_box.py seal-file "/tmp/pub/$n.json" "$n"; then rm -f "/tmp/pub/$n.json"; fi
  elif [ "$n" = "shops" ]; then
    echo "ボードのデータ（shops）が手元にも公開にも無いので、公開しない"; exit 1
  fi
done
keep todo.enc.json || true
keep requests.enc.json || true   # 頼みごと(個室・待機場所・迎え)。名前を含むので暗号化済み
keep chat_seen.json || true
# 夜遊びチャットの見張りの記録（スレッドの印と時刻だけ）。今回書けていなければ前のものを引き継ぐ
keep yoasobi_state.json || true
# 即ヒメに時間を付けた記録(名前を含むので暗号化済み)。今回書けていなければ前のものを引き継ぐ
keep sokuhime.enc.json || true
# 夜遊びの空き状況の写し（名前を含むので暗号化済み）
keep cti_avail.enc.json || true
# 姫デコチャットの確認待ちの件数（poll.py）と口コミ返信の件数（reply_reviews.py）。鍵つき。main にはもう置かない（2026-10-26〜）
keep status.enc.json || true
keep reply_status.enc.json || true
cd /tmp/pub
git init -q -b data
git config user.name "collect-bot"
git config user.email "actions@github.com"
git add .
git commit -qm "data"
git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data
