"""
CityHeaven管理画面(ヘブンマネージャー)を、ブラウザを使わずHTTPだけで操作する小さなクライアント。

背景:
    以前はPlaywright(ブラウザ)で保存済みログイン情報(Cookie)を使い回していたが、
    ログインセッションが数時間で切れてしまい、一希さんが再ログインするまで
    自動返信が止まる問題があった。
    このクライアントは、実行のたびに自分でログインし直す(IDとパスワードは
    GitHub Secretsから環境変数で受け取る)ので、「保存したログインが切れる」
    という状態自体が起きない。ブラウザも不要。

仕組み(2026-09-19に実機で確認):
    ・ログイン: C1Login.php に txt_account / txt_password をPOSTするだけ(CSRF・画像認証なし)
    ・店舗選択: C1GroupLogin.php?commuId=<店舗ID>&login=1 をGET
    ・口コミ返信: C8ReviewDetail.php?review_id=..&shopdir=.. をGETして「編集中の口コミ」を
      サーバー側セッションに記憶させ、続けて同じセッションでC8ReviewDetail.php?shopdir=..
      にフォームをPOSTする(フォームにreview_idは含まれない)
    ・グループIDで別店舗に切り替えるとセッションが不安定になるため、店舗ごとに
      新しいセッションでログインし直す。
"""

import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://newmanager.cityheaven.net"

COMMU_IDS = {
    "cg_kirakira": "1800000524",
    "s_matikado": "1810000550",
    "mrs_orange": "1810054632",
    "venus_okayama": "1810020233",
    "potya_reen": "1810001943",
    "torori_angel": "1810023303",
    "undercover": "3649",
}

DETAIL_HEADINGS = [
    "投稿日", "訪問日", "投稿者", "タイトル", "評価点", "料金の総額", "お店の利用回数",
    "受付からプレイ開始までの流れ", "お相手の女性について", "プレイ内容", "今回の総評",
    "お店のいいところ", "お店の改善してほしいところ", "お店の悪いところ", "遊んだ女の子", "返信",
]


class HeavenError(RuntimeError):
    pass


class LoginError(HeavenError):
    pass


def _is_login_page(r) -> bool:
    """ログイン画面に飛ばされた(=未ログイン/ログイン切れ)かどうか。"""
    return "C1Login.php" in r.url or 'class="oldLogin"' in r.text


def load_credentials(shopdir: str):
    """(ログインID, パスワード, 店舗別ログインかどうか) を環境変数から返す。

    CITYHEAVEN_SHOP_CREDS(JSON: {"店舗dir": ["ID", "パスワード"], ...}) があれば店舗別のID、
    無ければ CITYHEAVEN_ACCOUNT / CITYHEAVEN_PASSWORD(グループID)を使う。
    """
    import json
    import os

    raw = os.environ.get("CITYHEAVEN_SHOP_CREDS", "")
    if raw:
        creds = json.loads(raw)
        if shopdir in creds:
            return creds[shopdir][0], creds[shopdir][1], True
    account = os.environ.get("CITYHEAVEN_ACCOUNT", "")
    password = os.environ.get("CITYHEAVEN_PASSWORD", "")
    if not account or not password:
        raise LoginError("ログイン情報が設定されていません(GitHub Secretsを確認してください)")
    return account, password, False


class HeavenClient:
    def __init__(self, account: str, password: str, direct: bool = False):
        self.account = account
        self.password = password
        self.direct = direct  # True: 店舗別IDで直接ログイン(店舗選択の手順は不要)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (compatible; heaven-auto/1.0)"
        self.shopdir: Optional[str] = None

    def _get(self, path: str, **kw) -> requests.Response:
        r = self.s.get(BASE + path, timeout=30, **kw)
        r.encoding = "utf-8"
        return r

    def login_and_select(self, shopdir: str):
        """新しいセッションでグループIDにログインし、指定店舗を選択する。"""
        self.s.cookies.clear()
        r = self._get("/C1Login.php")
        soup = BeautifulSoup(r.text, "html.parser")
        data = {"txt_account": self.account, "txt_password": self.password, "login": "ログイン"}
        sid = soup.find("input", {"name": "PHPSESSID"})
        if sid and sid.get("value"):
            data["PHPSESSID"] = sid["value"]
        r = self.s.post(BASE + "/C1Login.php", data=data, timeout=30)
        r.encoding = "utf-8"
        if _is_login_page(r):
            raise LoginError("ログインに失敗しました(IDまたはパスワードが違う、またはサイト側で拒否された)")

        if not self.direct:
            commu_id = COMMU_IDS[shopdir]
            r = self._get(f"/C1GroupLogin.php?commuId={commu_id}&login=1")
            if _is_login_page(r):
                raise LoginError("店舗の選択に失敗しました(ログインが維持されていない)")
        self.shopdir = shopdir
        # 選択した店舗のページであることを確認
        r = self._get(f"/C8ReviewList.php?shopdir={shopdir}")
        if _is_login_page(r) or f"shopdir={shopdir}" not in r.text:
            raise LoginError("対象店舗の口コミページを開けませんでした")

    def list_reviews(self) -> list:
        """口コミ一覧の1ページ目(新しい順)を返す。"""
        r = self._get(f"/C8ReviewList.php?shopdir={self.shopdir}")
        if _is_login_page(r):
            raise LoginError("口コミ一覧の取得中にログインが切れました")
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for tr in soup.find_all("tr"):
            a = tr.find("a", href=re.compile(r"C8ReviewDetail"))
            if not a:
                continue
            m = re.search(r"review_id=(\d+)", a["href"])
            cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all("td")]
            if not m or len(cells) < 8:
                continue
            out.append({
                "review_id": m.group(1),
                "status": cells[1],
                "title": cells[3],
                "girl": cells[4],
                "rate": cells[5],
                "replied": cells[7].strip() == "済",
            })
        return out

    def _detail_html(self, review_id: str) -> str:
        r = self._get(f"/C8ReviewDetail.php?review_id={review_id}&shopdir={self.shopdir}")
        if _is_login_page(r):
            raise LoginError("口コミ詳細の取得中にログインが切れました")
        return r.text

    def get_detail(self, review_id: str) -> dict:
        """口コミ詳細を、見出しごとの本文に分けて返す。"""
        html = self._detail_html(review_id)
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "textarea"]):
            t.decompose()
        lines = [l.strip() for l in soup.get_text("\n").splitlines()]
        sections, current = {}, None
        for line in lines:
            if line in DETAIL_HEADINGS:
                current = line
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        return {k: "\n".join(v).strip() for k, v in sections.items()}

    def post_reply(self, review_id: str, text: str):
        """返信を投稿し、表示設定を「全て表示」にする。"""
        html = self._detail_html(review_id)  # 「編集中の口コミ」をサーバーに記憶させる
        soup = BeautifulSoup(html, "html.parser")
        ta = soup.find("textarea", {"name": "reply_body"})
        if ta is None:
            raise HeavenError("返信欄が見つかりませんでした")
        form = ta.find_parent("form")
        data = {}
        for el in form.find_all(["input", "textarea", "select"]):
            name = el.get("name")
            if not name:
                continue
            typ = (el.get("type") or "").lower()
            if typ == "button":
                continue
            if typ == "submit":
                if name == "update":
                    data[name] = el.get("value") or "更新する"
                continue
            if typ == "radio":
                if el.get("id") == "display_flg_2":  # 「全て表示」(画面のJSは、これを選ぶと girl_display_flg を 02 にする)
                    data[name] = el.get("value")
                    data["girl_display_flg"] = "02"
                continue
            if typ == "checkbox":
                if el.has_attr("checked"):
                    data[name] = el.get("value") or "on"
                continue
            if name == "reply_body":
                data[name] = text
                continue
            if name == "girl_display_flg":
                continue
            data[name] = el.get_text() if el.name == "textarea" else (el.get("value") or "")
        action = form.get("action") or f"C8ReviewDetail.php?shopdir={self.shopdir}"
        url = BASE + "/" + action.lstrip("./")
        r = self.s.post(url, data=data, timeout=30, headers={"Referer": BASE + f"/C8ReviewDetail.php?review_id={review_id}&shopdir={self.shopdir}"})
        r.encoding = "utf-8"
        if r.status_code != 200 or _is_login_page(r):
            raise HeavenError(f"返信の投稿に失敗しました(HTTP {r.status_code})")

    def is_replied(self, review_id: str) -> bool:
        for rv in self.list_reviews():
            if rv["review_id"] == review_id:
                return rv["replied"]
        return False
