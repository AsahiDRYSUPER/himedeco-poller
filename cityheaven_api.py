"""
CityHeaven公式API(http://api.cityheaven.net/)を呼び出す共通モジュール。

一希さんがCityHeavenからもらった公式API仕様書(ヘブンAPI設計)に基づく。
セッションCookie(ログイン)に依存しないため、Playwrightでの
ログインセッション切れの問題が発生しない。

各店舗のshopid・apikeyは、管理画面の「お店情報」→「基本情報」ページ
下部の「店舗ID」「APIキー」に表示されている。
APIキーは画面表示されている文字列をそのまま使う(%2Fや%2Bの記号も含めて)。

本番(GitHub Actions)では環境変数 CITYHEAVEN_API_KEYS(JSON文字列)から読み込む。
ローカル実行時は、このファイルと同じ階層の
cityheaven_api_keys.local.json(gitには含めない)から読み込む。
"""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

API_BASE = "http://api.cityheaven.net"

_ENV_KEYS = os.environ.get("CITYHEAVEN_API_KEYS")
_LOCAL_KEYS_PATH = Path(__file__).parent / "cityheaven_api_keys.local.json"

if _ENV_KEYS:
    SHOPS_API = json.loads(_ENV_KEYS)
elif _LOCAL_KEYS_PATH.exists():
    SHOPS_API = json.loads(_LOCAL_KEYS_PATH.read_text(encoding="utf-8"))
else:
    SHOPS_API = {}


def jst_today_str(offset_days: int = 0) -> str:
    jst = timezone(timedelta(hours=9))
    d = datetime.now(jst) + timedelta(days=offset_days)
    return d.strftime("%Y%m%d")


def fetch_shift_list(shopid: str, apikey: str, base_day: Optional[str] = None) -> ET.Element:
    """週間の出勤情報APIを呼び出し、XMLのルート要素を返す。base_dayからその日を含む7日分。"""
    if base_day is None:
        base_day = jst_today_str()

    resp = requests.post(
        f"{API_BASE}/ApiShukkinList.php",
        data={
            "keyid": apikey,
            "shopid": shopid,
            "mode": "0",
            "base_day": base_day,
            "sort": "2",
        },
        headers={"Accept-Encoding": "gzip"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    error = root.find("error")
    if error is not None:
        code = error.findtext("code")
        message = error.findtext("message")
        raise RuntimeError(f"CityHeaven API エラー {code}: {message}")

    return root


def fetch_reviews(shopid: str, apikey: str, hit_per_page: int = 30, offset_page: int = 1) -> list:
    """口コミAPIを呼び出し、口コミごとの辞書のリストを返す(新しい順)。"""
    resp = requests.post(
        f"{API_BASE}/ApiShopReviewList.php",
        data={
            "keyid": apikey,
            "shopid": shopid,
            "hit_per_page": str(hit_per_page),
            "offset_page": str(offset_page),
        },
        headers={"Accept-Encoding": "gzip"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    error = root.find("error")
    if error is not None:
        code = error.findtext("code")
        message = error.findtext("message")
        raise RuntimeError(f"CityHeaven API エラー {code}: {message}")

    reviews = []
    for r in root.findall("review"):
        review = {child.tag: (child.text or "") for child in r if child.tag != "girls"}
        reviews.append(review)
    return reviews


def parse_girls(root: ET.Element) -> list[dict]:
    """出勤情報APIのレスポンスから、女の子ごとの{name, days: [{date, start_time, end_time}]}を作る。"""
    girls = []
    for g in root.findall("girls"):
        name = g.findtext("name") or ""
        days = []
        for w in g.findall("w_shukkin"):
            year = w.findtext("year")
            month = w.findtext("month")
            day = w.findtext("day")
            start_time = (w.findtext("start_time") or "").strip()
            end_time = (w.findtext("end_time") or "").strip()
            if not (year and month and day):
                continue
            date_str = f"{int(year):04d}{int(month):02d}{int(day):02d}"
            days.append({"date": date_str, "start_time": start_time, "end_time": end_time})
        girls.append({"name": name, "days": days})
    return girls
