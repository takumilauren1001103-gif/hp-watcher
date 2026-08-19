"""
HP電話番号監視システム
GitHub Actions で定期実行される。
"""

import json
import re
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

JST = timezone(timedelta(hours=9))
HISTORY_FILE = "history.json"
CONFIG_FILE = "config.json"


# ─── 設定を読む ───────────────────────────────
def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # トークンはGitHubのSecretsから受け取る（config.jsonには書かない）
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    return cfg


# ─── 通知履歴を読む・書く ──────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def is_in_cooldown(history, url, phone, cooldown_minutes):
    key = f"{url}|{phone}"
    if key not in history:
        return False
    last = datetime.fromisoformat(history[key])
    elapsed = (datetime.now(JST) - last).total_seconds() / 60
    return elapsed < cooldown_minutes


# ─── ページタイトルを取り出す ──────────────────
class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False


# ─── ページを取得する ─────────────────────────
def fetch_page(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PhoneWatcher/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read().decode(charset, errors="replace")
    except Exception as e:
        print(f"  取得できませんでした: {e}")
        return None, None

    p = TitleParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return html, (p.title.strip() or "(タイトルなし)")


# ─── 電話番号を探す ───────────────────────────
PHONE_PATTERN = re.compile(
    r"(?<!\d)(0[789]0[-\u2010-\u2015\uFF0D\s]?\d{4}[-\u2010-\u2015\uFF0D\s]?\d{4})(?!\d)"
)


def find_phones(html):
    if not html:
        return []
    # スクリプトとスタイルを除去
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    # tel: リンクの番号を拾っておく（タグ除去で消えてしまうため）
    tel_links = " ".join(re.findall(r'tel:([0-9\-\s]+)', text, flags=re.I))
    # タグを除去
    text = re.sub(r"<[^>]+>", " ", text) + " " + tel_links
    # 全角数字を半角に
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    # 各種ハイフンを半角ハイフンに統一
    text = re.sub(r"[\u2010-\u2015\uFF0D\u30FC]", "-", text)
    found = PHONE_PATTERN.findall(text)
    # 重複を消して順番を保つ
    seen = []
    for f in found:
        if f not in seen:
            seen.append(f)
    return seen


# ─── Telegramに送る ──────────────────────────
def send_telegram(token, chat_id, text):
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        api, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print("  Telegramに送信しました")
                return True
            print(f"  Telegramエラー: {result}")
    except Exception as e:
        print(f"  Telegram送信に失敗: {e}")
    return False


def build_message(url, phones, title, now_str):
    lines = "\n".join(f"　・{p}" for p in phones)
    return (
        "【電話番号を検知しました】\n"
        "\n"
        f"監視URL：{url}\n"
        f"検知日時：{now_str}\n"
        f"検出番号：\n{lines}\n"
        f"ページタイトル：{title}\n"
        "\n"
        "至急確認してください。"
    )


# ─── メイン ──────────────────────────────────
def main():
    cfg = load_config()
    token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    urls = cfg.get("target_urls", [])
    cooldown = cfg.get("notification_cooldown_minutes", 30)

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== チェック開始 {now_str} ===")

    if not token or not chat_id:
        print("エラー: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が設定されていません")
        print("GitHubのSecretsを確認してください")
        return

    if not urls:
        print("監視対象URLがありません。config.json に追加してください")
        return

    history = load_history()

    for url in urls:
        print(f"\n[確認中] {url}")
        html, title = fetch_page(url)
        if html is None:
            continue

        phones = find_phones(html)
        if not phones:
            print("  電話番号なし")
            continue

        print(f"  検出: {phones}")

        # まだ通知していない番号だけ抜き出す
        new_phones = [
            p for p in phones if not is_in_cooldown(history, url, p, cooldown)
        ]

        if not new_phones:
            print(f"  通知済み（{cooldown}分の再通知抑止中）")
            continue

        msg = build_message(url, new_phones, title, now_str)
        if send_telegram(token, chat_id, msg):
            for p in new_phones:
                history[f"{url}|{p}"] = datetime.now(JST).isoformat()

    save_history(history)
    print("\n=== チェック終了 ===")


if __name__ == "__main__":
    main()
