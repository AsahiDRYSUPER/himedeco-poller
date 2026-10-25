#!/usr/bin/env bash
# 公開リポジトリの作業記録（ログ）に、お店の情報（店名・人数・件数・名前）を残さないための包み（2026-10-25 一希さん）。
# 使い方: bash quiet.sh <名前> <コマンド...>
#   コマンドの出力は実行機の中のファイル（QUIET_DIR）にだけ書き、作業記録には「終わり（終了コード）」だけ出す。
#   日本の実行機（Mac）では QUIET_DIR=~/jp-logs（7日分残す）。GitHub のクラウドでは実行の中だけの一時置き場（実行が終わると消える）。
#   調べる時だけ、リポジトリの変数 QUIET_OFF=1 で今まで通り出す（一希さんの了解を取ってから）。
set -u
name="$1"; shift
if [ "${QUIET_OFF:-}" = "1" ]; then
  "$@"
  exit $?
fi
dir="${QUIET_DIR:-${RUNNER_TEMP:-/tmp}}"
mkdir -p "$dir" 2>/dev/null || dir="/tmp"
log="$dir/${name}_$(date +%Y%m%d_%H%M%S).log"
"$@" > "$log" 2>&1
rc=$?
echo "${name}: 終わり（終了コード ${rc}。中身は実行機の中だけ）"
# 古い記録は7日で消す（Mac に溜めない）
find "$dir" -maxdepth 1 -name '*.log' -mtime +7 -delete 2>/dev/null || true
exit $rc
