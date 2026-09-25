"""reply_rules の分け方が、一希さんと決めたとおりかを確かめる（ネット不要）。"""
from datetime import datetime

from reply_rules import classify

NOW = datetime(2026, 9, 24, 23, 0)
CASES = {
    "ありがとうございます🙇‍♀️": "thanks",
    "了解です！": "thanks",
    "わかりました！ よろしくお願いします": "thanks",
    "お疲れ様です！": "thanks",
    "お疲れ様です！ / 今生理中なので、終わっていたら出勤するつもりです😊 / 出勤出来そうだったら連絡します🙏": "ask_when",
    "とどにち土日出勤するかいま悩んでて🥲🥲": "ask_when",
    "来週出勤したいです": "ask_when",
    "26日": "ask_when",
    "26日 12時から": "ask_when",
    "26日 12〜20時かも": "ask_when",
    "26日か27日 12〜20時": "ask_when",
    "26日 12〜ラスト": "ask_when",
    "当日休むっていうのが怖いので当日に言うのでも大丈夫ですか、？": "question",
    "26日 12〜20時で大丈夫ですか？": "question",
    "明日出勤できなくなりました、すみません": "other",
    "26日 休みます": "other",
    "少し遅れそうです": "other",
    "お疲れ様です。。！ / いつもお世話になってるお店のお役に立ちたい気持ちはあって、時間帯によっては出勤できそうな時間もあるんですけど、ここ最近、出勤しても予約をいただけない現状に少し精神を病んでしまって。。": "other",
    "裏姫デコほしいです": "other",
    "26日 12-20時 / 27日 10-17時": "clear",
    "27日 14時〜21時 / お願いします🙇‍♀️": "clear",
}
ok = True
for text, want in CASES.items():
    got = classify(text, NOW)
    if got != want:
        ok = False
        print(f"✗ {want} のはず: {text[:40]!r} → {got}")
print("すべて通りました" if ok else "直すところがあります")
raise SystemExit(0 if ok else 1)
