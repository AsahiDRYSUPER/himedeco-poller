#!/usr/bin/env bash
# out/ のファイルを、dataブランチ(履歴なし・毎回1コミット)へ反映する。
# 2026-11-12〜 押す前に dataブランチの今の先頭を控え、引き継ぐファイルもその先頭のものを読み、
#   押す時は「先頭が控えたまま（その間に別の公開が押していない）」の時だけ上書きする（--force-with-lease）。
#   変わっていたら、最初から読み直してもう一度（3回まで）。同時に走った2つの公開で、後の方が先の方の新しい記録を古いもので消していた
set -e
REMOTE="${PUB_REMOTE:-https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git}"
API="${PUB_API:-https://api.github.com/repos/${GITHUB_REPOSITORY}/contents}"
RAW="${PUB_RAW:-https://raw.githubusercontent.com/${GITHUB_REPOSITORY}}"
PUB="${PUB_DIR:-/tmp/pub}"
SHA=""
# dataブランチ（控えた先頭）のファイルを $PUB に取る。返す 0＝取れた／4＝無い（404・まだブランチが無い）／1＝読めない。
# まず API（鍵つき）で、だめなら raw で。どちらも先頭の番号で読むので、古い写しにはならない
fetch() {
  [ -n "$SHA" ] || return 4
  local code=000
  if [ -n "${GH_TOKEN:-}" ]; then
    code=$(curl -sS --max-time 30 -H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github.raw" -w '%{http_code}' \
           -o "$PUB/$1" "$API/$1?ref=$SHA" 2>/dev/null) || code=000
    [ "$code" = 200 ] && return 0
    rm -f "$PUB/$1"
    [ "$code" = 404 ] && return 4
  fi
  code=$(curl -sS --max-time 30 -w '%{http_code}' -o "$PUB/$1" "$RAW/$SHA/$1" 2>/dev/null) || code=000
  [ "$code" = 200 ] && return 0
  rm -f "$PUB/$1"
  [ "$code" = 404 ] && return 4
  return 1
}
# 今回作っていないファイルは、dataブランチの「今」のものを引き継ぐ
# （2026-10-23 日本側の公開（jp-sokuhime）が chat_seen.json・声かけリスト・頼みごとを引き継がずに消していた → チャットが毎回「初回」になり返信が止まった）
keep() { cp "out/$1" "$PUB/" 2>/dev/null && return 0
  local rc=0
  fetch "$1" || rc=$?
  return $rc; }
# 夜遊びの見張りの記録（印と時刻だけ）。公開には鍵をかけて出す（yoasobi_state.enc.json。2026-11-12〜）。
#   手元に今回の記録があれば、yoasobi_watch.py が書いた鍵つきの写しを出す（平文は出さない。鍵の無い所だけ平文のまま＝前と同じ）。
#   無ければ dataブランチのものを引き継ぐ。引き継げない時は黙って落とさず、この回は公開しない（dataブランチは前のまま。
#   落とすと次の回が「まだ一度も書いていない」と読み、印が消える）。
#   yoasobi_state_safe は、記録が読めなかった回に付けた印（次に読めた回で合わさる）
keep_state() {
  local n="$1" rc=0
  if [ -f "out/$n.enc.json" ]; then cp "out/$n.enc.json" "$PUB/"; return 0; fi
  if [ -f "out/$n.json" ] && [ -z "${TODO_KEY:-}" ]; then cp "out/$n.json" "$PUB/"; return 0; fi
  fetch "$n.enc.json" || rc=$?
  [ "$rc" = 0 ] && return 0
  if [ "$rc" != 4 ]; then echo "${n}.enc.json を dataブランチから読めない → この回は公開しない"; exit 1; fi
  rc=0
  fetch "$n.json" || rc=$?      # 切り替え直後（まだ鍵つきが無い）：前の平文を引き継ぎ、鍵があればその場でかける
  if [ "$rc" = 0 ]; then
    if "${PY:-python3}" data_box.py seal-file "$PUB/$n.json" "$n" >/dev/null 2>&1; then rm -f "$PUB/$n.json"; fi
    return 0
  fi
  [ "$rc" = 4 ] && return 0
  echo "${n}.json を dataブランチから読めない → この回は公開しない"; exit 1; }

try=0
while :; do
  try=$((try + 1))
  heads=$(git ls-remote "$REMOTE" refs/heads/data 2>/dev/null) || { echo "dataブランチの先頭が読めない → この回は公開しない"; exit 1; }
  SHA=$(printf '%s\n' "$heads" | cut -f1)
  rm -rf "$PUB" && mkdir "$PUB"
  # 手で動かす回（si9-check）ではボードのデータを作らないので、無ければ公開されている今のものを引き継ぐ
  keep next_shifts.json
  # 鍵つきで出す6つ（2026-10-25〜。data_box.py。鍵は金庫の TODO_KEY）。平文の名前（X.json）はもう引き継がない＝dataブランチから消える。
  #   shops（ボードの店ごとの数）・kpi_history（日ごとの店別の数）・si9_status（新着んの記録）・sokuhime_status（即ヒメの生きている印）
  #   yoasobi_ok（外国人OKの控え）・ai_learned（AIが学んだ一般的な決まり）
  # 切り替え直後でまだ X.enc.json が無い時だけ、前の平文 X.json を引き継いでその場で鍵をかける（鍵が無ければ平文のまま残す＝消さない）
  for n in shops kpi_history si9_status sokuhime_status yoasobi_ok ai_learned; do
    keep "$n.enc.json" && continue
    if keep "$n.json"; then
      if "${PY:-python3}" data_box.py seal-file "$PUB/$n.json" "$n"; then rm -f "$PUB/$n.json"; fi
    elif [ "$n" = "shops" ]; then
      echo "ボードのデータ（shops）が手元にも公開にも無いので、公開しない"; exit 1
    fi
  done
  keep todo.enc.json || true
  keep requests.enc.json || true   # 頼みごと(個室・待機場所・迎え)。名前を含むので暗号化済み
  keep chat_seen.json || true
  keep_state yoasobi_state
  keep_state yoasobi_state_safe
  # 即ヒメに時間を付けた記録(名前を含むので暗号化済み)。今回書けていなければ前のものを引き継ぐ
  keep sokuhime.enc.json || true
  # 夜遊びの空き状況の写し（名前を含むので暗号化済み）
  keep cti_avail.enc.json || true
  # 姫デコチャットの確認待ちの件数（poll.py）と口コミ返信の件数（reply_reviews.py）。鍵つき。main にはもう置かない（2026-10-26〜）
  keep status.enc.json || true
  keep reply_status.enc.json || true
  (cd "$PUB" && git init -q -b data && git config user.name "collect-bot" && git config user.email "actions@github.com" \
     && git add . && git commit -qm "data")
  if (cd "$PUB" && git push -q --force-with-lease="data:$SHA" "$REMOTE" data 2>/dev/null); then exit 0; fi
  if [ "$try" -ge 3 ]; then echo "dataブランチへ押せない（3回）→ この回は公開しない"; exit 1; fi
  echo "公開のあいだに dataブランチが変わった（か押せなかった）→ 読み直してもう一度（${try}回目）"
  sleep $((try * 3))
done
