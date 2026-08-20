"""
HP電話番号監視システム v3

機能
  1. JS描画ページ対応（Wix / STUDIO / ペライチ 等）
  2. 画像内の番号をOCRで読み取り
  3. 携帯・固定電話・フリーダイヤルを検出
  4. ダミー番号を除外（架電できるものだけ通知）
  5. 取得失敗が続いたらTelegramで警告
  6. 検出から4日後に監視対象から自動削除
"""

import json
import re
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

JST = timezone(timedelta(hours=9))
HISTORY_FILE = "history.json"
CONFIG_FILE = "config.json"
PURGE_DAYS = 4
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"


# ══════════════════════════════════════════════
#  設定
# ══════════════════════════════════════════════
DEFAULTS = {
    "target_urls": [],
    "check_interval_minutes": 30,
    "notification_cooldown_minutes": 60,
    "detect_mobile": True,        # 090/080/070
    "detect_landline": True,      # 03-xxxx-xxxx 等
    "detect_tollfree": True,      # 0120 / 0800
    "use_browser": True,          # JS描画ページに対応
    "use_ocr": True,              # 画像内の番号を読む
    "ocr_max_images": 8,          # 1ページあたりのOCR対象画像数
    "error_alert_after": 3,       # 連続何回失敗したら警告するか
    "request_interval_seconds": 3,  # サイト間のアクセス間隔（負荷対策）
}


def load_config():
    cfg = dict(DEFAULTS)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg.update(json.load(f))
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    return cfg


def save_config_urls(urls):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["target_urls"] = urls
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ══════════════════════════════════════════════
#  履歴
# ══════════════════════════════════════════════
def load_history():
    base = {"notified": {}, "detected": {}, "failures": {}}
    if not os.path.exists(HISTORY_FILE):
        return base
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            h = json.load(f)
    except Exception:
        return base
    for k, v in base.items():
        h.setdefault(k, v)
    return h


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_in_cooldown(history, url, phone, minutes):
    stamp = history["notified"].get(f"{url}|{phone}")
    if not stamp:
        return False
    try:
        last = datetime.fromisoformat(stamp)
    except Exception:
        return False
    return (datetime.now(JST) - last).total_seconds() / 60 < minutes


# ══════════════════════════════════════════════
#  番号の種別判定
# ══════════════════════════════════════════════
def normalize(phone):
    return re.sub(r"\D", "", phone)


def classify(phone):
    """mobile / landline / tollfree / None を返す"""
    d = normalize(phone)

    if len(d) == 11:
        # 0800 はフリーダイヤル。携帯の080は4桁目が0にならないため先に判定する
        if d[:4] == "0800":
            return "tollfree"
        if d[:3] in ("090", "080", "070"):
            return "mobile"
        return None

    if len(d) == 10:
        if d[:4] == "0120":
            return "tollfree"
        # 固定電話：0で始まり、2桁目が1-9
        if d[0] == "0" and d[1] in "123456789":
            # 携帯・IP電話の短縮形は除く
            if d[:3] in ("090", "080", "070", "050"):
                return None
            return "landline"
        return None

    return None


LABEL = {"mobile": "携帯", "landline": "固定電話", "tollfree": "フリーダイヤル"}


# ══════════════════════════════════════════════
#  ダミー判定
# ══════════════════════════════════════════════
KNOWN_DUMMIES = {
    "09012345678", "08012345678", "07012345678",
    "09000000000", "08000000000", "07000000000",
    "09011111111", "09012341234", "09098765432",
    "0312345678", "0698765432", "0612345678",
    "0120000000", "0120123456", "08001234567",
    "0123456789", "0111111111",
}


def is_dummy(phone):
    """架電できない見本番号なら True"""
    d = normalize(phone)

    if classify(phone) is None:
        return True

    if d in KNOWN_DUMMIES:
        return True

    tail = d[-8:]                 # 末尾8桁で判定

    # 全部同じ数字
    if len(set(tail)) == 1:
        return True

    # 数字が2種類以下（01010101 / 12121212）
    if len(set(tail)) <= 2:
        return True

    # 連番（昇順・降順）
    asc = "".join(str((int(tail[0]) + i) % 10) for i in range(8))
    desc = "".join(str((int(tail[0]) - i) % 10) for i in range(8))
    if tail in (asc, desc):
        return True

    mid, last = tail[:4], tail[4:]

    # 前半・後半がそれぞれゾロ目
    if len(set(mid)) == 1 and len(set(last)) == 1:
        return True

    # 前半と後半が同一
    if mid == last:
        return True

    # 携帯のみ：中4桁が0000（見本用の予約帯）
    if classify(phone) == "mobile" and mid == "0000":
        return True

    # 定番の並び
    if (mid, last) in (("1234", "5678"), ("0123", "4567")):
        return True

    return False


# ══════════════════════════════════════════════
#  ページ取得
# ══════════════════════════════════════════════
class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in = True

    def handle_data(self, data):
        if self._in:
            self.title += data

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in = False


def get_title(html):
    p = TitleParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.title.strip() or "(タイトルなし)"


def decode_html(raw, header_charset):
    """ヘッダ・metaタグ・BOMから文字コードを判定してデコードする"""
    # ヘッダに指定があればそれを優先
    if header_charset:
        try:
            return raw.decode(header_charset, errors="replace")
        except Exception:
            pass
    # metaタグから探す
    head = raw[:4096].decode("ascii", errors="ignore")
    m1 = re.search(r'charset=["\']?\s*([\w\-]+)', head, flags=re.I)
    if m1:
        try:
            return raw.decode(m1.group(1), errors="replace")
        except Exception:
            pass
    # 順に試す
    for cs in ("utf-8", "cp932", "euc-jp", "iso-2022-jp"):
        try:
            return raw.decode(cs)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_simple(url):
    """通常のHTTP取得。(HTML, エラー文, ステータスコード) を返す"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return decode_html(r.read(), r.headers.get_content_charset()), None, r.status
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.reason}", e.code
    except Exception as e:
        return None, str(e), None


def fetch_browser(url):
    """ブラウザで描画してからHTMLを取る（JS対応）"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwrightが未インストール"

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = b.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
            page.goto(url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(1500)
            html = page.content()
            b.close()
            return html, None
    except Exception as e:
        return None, f"ブラウザ描画に失敗: {e}"


def looks_js_rendered(html):
    """本文がほぼ空＝JSで描画している可能性が高い"""
    if not html:
        return True
    body = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    body = re.sub(r"<style[\s\S]*?</style>", " ", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", body).strip()
    if len(text) < 120:
        return True
    for sign in ("__NUXT__", "__NEXT_DATA__", "id=\"root\"", "id=\"app\"",
                 "wix.com", "studio.design", "peraichi"):
        if sign in html:
            return True
    return False


# ══════════════════════════════════════════════
#  OCR（画像内の番号を読む）
# ══════════════════════════════════════════════
def collect_images(html, base_url, limit):
    srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    srcs += re.findall(r'url\(["\']?([^"\')]+\.(?:png|jpe?g|webp))["\']?\)',
                       html, flags=re.I)
    out = []
    for s in srcs:
        if s.startswith("data:"):
            continue
        full = urllib.parse.urljoin(base_url, s)
        if full not in out:
            out.append(full)
        if len(out) >= limit:
            break
    return out


def ocr_images(html, base_url, limit):
    """画像から読み取れたテキストを連結して返す"""
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        return "", "OCRライブラリが未インストール"

    texts = []
    for src in collect_images(html, base_url, limit):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(4_000_000)
            img = Image.open(io.BytesIO(data))
            if img.width < 60 or img.height < 20:
                continue
            # 小さすぎる画像は拡大すると精度が上がる
            if img.width < 600:
                img = img.resize((img.width * 2, img.height * 2))
            texts.append(pytesseract.image_to_string(img, lang="eng"))
        except Exception:
            continue
    return "\n".join(texts), None


# ══════════════════════════════════════════════
#  番号の抽出
# ══════════════════════════════════════════════
CANDIDATE = re.compile(r"(?<!\d)(0[\d\-\u2010-\u2015\uFF0D\u30FC\s\(\)]{8,16}\d)(?!\d)")


def clean_text(html):
    t = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    tel = " ".join(re.findall(r"tel:([0-9\-\s\+]+)", t, flags=re.I))
    t = re.sub(r"<[^>]+>", " ", t) + " " + tel
    t = t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    t = re.sub(r"[\u2010-\u2015\uFF0D\u30FC]", "-", t)
    return t


def find_phones(text, cfg):
    """(本物のリスト, ダミーのリスト) を返す。各要素は (番号, 種別)"""
    want = set()
    if cfg.get("detect_mobile", True):
        want.add("mobile")
    if cfg.get("detect_landline", True):
        want.add("landline")
    if cfg.get("detect_tollfree", True):
        want.add("tollfree")

    real, dummy = [], []
    seen_real, seen_dummy = set(), set()

    for raw in CANDIDATE.findall(text):
        cand = raw.strip()
        kind = classify(cand)
        if kind is None or kind not in want:
            continue
        n = normalize(cand)
        if is_dummy(cand):
            if n not in seen_dummy:
                seen_dummy.add(n)
                dummy.append((cand, kind))
        else:
            if n not in seen_real:
                seen_real.add(n)
                real.append((cand, kind))
    return real, dummy


# ══════════════════════════════════════════════
#  Telegram
# ══════════════════════════════════════════════
TG_LIMIT = 3800          # Telegramの上限4096より余裕をもたせる


def send_telegram(token, chat_id, text, tries=3):
    """失敗しても数回やり直す。長文は分割して送る"""
    # 長すぎる場合は分割
    chunks = []
    while len(text) > TG_LIMIT:
        cut = text.rfind("\n", 0, TG_LIMIT)
        if cut < TG_LIMIT // 2:
            cut = TG_LIMIT
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in chunks:
        ok = False
        for attempt in range(1, tries + 1):
            body = json.dumps({"chat_id": chat_id, "text": part}).encode("utf-8")
            req = urllib.request.Request(
                api, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    if json.loads(r.read()).get("ok"):
                        ok = True
                        break
            except Exception as e:
                print(f"  Telegram送信に失敗（{attempt}/{tries}）: {e}")
            if attempt < tries:
                time.sleep(3 * attempt)
        if not ok:
            return False
    print("  Telegramに送信しました")
    return True


def build_message(url, items, title, now_str, via):
    lines = "\n".join(f"　・{p}（{LABEL[k]}）" for p, k in items)
    src = {"html": "", "browser": "\n※JS描画ページから検出",
           "ocr": "\n※画像から検出"}.get(via, "")
    return (
        "【電話番号を検知しました】\n\n"
        f"監視URL：{url}\n"
        f"検知日時：{now_str}\n"
        f"検出番号：\n{lines}\n"
        f"ページタイトル：{title}{src}\n\n"
        "至急確認してください。\n"
        f"※このURLは{PURGE_DAYS}日後に監視対象から自動削除されます"
    )


def build_error_message(url, count, err, now_str):
    return (
        "【監視エラー】\n\n"
        f"監視URL：{url}\n"
        f"発生日時：{now_str}\n"
        f"連続失敗：{count}回\n"
        f"内容：{err}\n\n"
        "サイトが閉鎖された、URLが変更された、\n"
        "アクセスが拒否された等の可能性があります。\n"
        "確認して、不要なら監視画面から削除してください。"
    )


# ══════════════════════════════════════════════
#  4日経過したURLの掃除
# ══════════════════════════════════════════════
def drop_orphans(urls, history):
    """監視対象から外れたURLの履歴を削除する（管理画面での手動削除に対応）"""
    alive = set(urls)
    removed = 0

    for key in [k for k in history["detected"] if k not in alive]:
        history["detected"].pop(key, None)
        removed += 1
    for key in [k for k in history["failures"] if k not in alive]:
        history["failures"].pop(key, None)
        removed += 1
    for key in [k for k in history["notified"]
                if k.rsplit("|", 1)[0] not in alive]:
        history["notified"].pop(key, None)
        removed += 1

    return removed


def purge_old(urls, history):
    now = datetime.now(JST)
    keep, removed = [], []
    for url in urls:
        stamp = history["detected"].get(url)
        if not stamp:
            keep.append(url)
            continue
        try:
            first = datetime.fromisoformat(stamp)
        except Exception:
            keep.append(url)
            continue
        if (now - first).total_seconds() >= PURGE_DAYS * 86400:
            removed.append(url)
        else:
            keep.append(url)

    for url in removed:
        history["detected"].pop(url, None)
        history["failures"].pop(url, None)
        for k in [k for k in history["notified"] if k.startswith(url + "|")]:
            history["notified"].pop(k, None)
    return keep, removed


# ══════════════════════════════════════════════
#  1URLの処理
# ══════════════════════════════════════════════
def inspect(url, cfg):
    """(本物, ダミー, タイトル, 経路, エラー) を返す"""
    html, err, status = fetch_simple(url)

    # 404/410 はページが存在しない。ブラウザで試しても無駄
    if status in (404, 410):
        return [], [], None, None, err

    if html is None:
        if not cfg.get("use_browser", True):
            return [], [], None, None, err
        print("  通常取得に失敗 → ブラウザで再試行")
        html, err2 = fetch_browser(url)
        if html is None:
            return [], [], None, None, err

    title = get_title(html)
    real, dummy = find_phones(clean_text(html), cfg)
    if real:
        return real, dummy, title, "html", None

    # ── JS描画の可能性があればブラウザで取り直す ──
    if cfg.get("use_browser", True) and looks_js_rendered(html):
        print("  JS描画の可能性 → ブラウザで再取得")
        html2, err2 = fetch_browser(url)
        if html2:
            html = html2
            # 元のタイトルが取れていればそちらを優先（文字コード判定が正確なため）
            if not title or title == "(タイトルなし)":
                title = get_title(html2)
            real, dummy2 = find_phones(clean_text(html2), cfg)
            dummy = dummy or dummy2
            if real:
                return real, dummy, title, "browser", None

    # ダミーが見つかっている＝本文は読めている。画像を見る必要はない
    if dummy:
        return [], dummy, title, None, None

    # ── それでも無ければ画像をOCR ──
    if cfg.get("use_ocr", True):
        print("  テキストになし → 画像をOCR")
        otext, oerr = ocr_images(html, url, cfg.get("ocr_max_images", 8))
        if otext:
            r2, d2 = find_phones(clean_text(otext), cfg)
            dummy = dummy or d2
            if r2:
                return r2, dummy, title, "ocr", None

    return [], dummy, title, None, None


# ══════════════════════════════════════════════
#  メイン
# ══════════════════════════════════════════════
def main():
    cfg = load_config()
    token, chat_id = cfg["telegram_bot_token"], cfg["telegram_chat_id"]
    urls = cfg.get("target_urls", [])
    cooldown = cfg.get("notification_cooldown_minutes", 60)
    alert_after = cfg.get("error_alert_after", 3)

    now = datetime.now(JST)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== チェック開始 {now_str} ===")

    if not token or not chat_id:
        print("エラー: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が未設定です")
        return

    history = load_history()

    # 管理画面から手で削除されたURLの履歴を掃除
    orphans = drop_orphans(urls, history)
    if orphans:
        print(f"\n[掃除] 監視対象外の履歴 {orphans}件を削除しました")

    urls, removed = purge_old(urls, history)
    if removed:
        print(f"\n[自動削除] 検出から{PURGE_DAYS}日経過:")
        for u in removed:
            print(f"  - {u}")
        save_config_urls(urls)

    if not urls:
        print("\n監視対象URLがありません")
        save_history(history)
        return

    send_failed = 0

    for idx, url in enumerate(urls):
        # 相手サイトに連続アクセスしないよう間隔をあける
        if idx > 0:
            time.sleep(cfg.get("request_interval_seconds", 3))

        print(f"\n[確認中] {url}")

        # 1つのURLで何が起きても、他のURLの監視は続ける
        try:
            real, dummy, title, via, err = inspect(url, cfg)
        except Exception as e:
            real, dummy, title, via = [], [], None, None
            err = f"想定外のエラー: {type(e).__name__}: {e}"

        # ── 失敗の記録と警告 ──
        if err:
            rec = history["failures"].get(url, {"count": 0, "alerted": False})
            rec["count"] += 1
            rec["last_error"] = err[:200]
            print(f"  失敗（連続{rec['count']}回）: {err[:120]}")
            if rec["count"] >= alert_after and not rec.get("alerted"):
                if send_telegram(token, chat_id,
                                 build_error_message(url, rec["count"], err[:200], now_str)):
                    rec["alerted"] = True
            history["failures"][url] = rec
            continue

        # 成功したら失敗カウントをリセット
        if url in history["failures"]:
            print("  復旧しました")
            history["failures"].pop(url, None)

        if dummy:
            print(f"  ダミーとして除外: {[p for p, _ in dummy]}")

        if not real:
            print("  架電可能な番号なし")
            continue

        print(f"  検出({via}): {[f'{p}[{LABEL[k]}]' for p, k in real]}")

        if url not in history["detected"]:
            history["detected"][url] = now.isoformat()
            print(f"  → {PURGE_DAYS}日後に監視終了予定")

        fresh = [(p, k) for p, k in real
                 if not is_in_cooldown(history, url, p, cooldown)]
        if not fresh:
            print(f"  通知済み（{cooldown}分の再通知抑止中）")
            continue

        if send_telegram(token, chat_id,
                         build_message(url, fresh, title, now_str, via)):
            for p, _ in fresh:
                history["notified"][f"{url}|{p}"] = now.isoformat()
        else:
            send_failed += 1
            print("  !! 通知を送れませんでした。次回あらためて送信します")

    save_history(history)
    print("\n=== チェック終了 ===")

    # 通知が送れていない場合は、GitHub Actions上で失敗として残す
    if send_failed:
        print(f"\n!! {send_failed}件の通知が送信できませんでした")
        print("!! Telegramのトークン・Chat IDを確認してください")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
