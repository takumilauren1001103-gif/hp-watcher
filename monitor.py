"""HP電話番号監視システム v5

現実的な全ページ巡回:
- 登録ドメイン内の公開HTMLをリンクから少しずつたどる。
- 1回は最大10ルートURL、各ルート最大3ページだけ処理する。
- 発見した未巡回ページはキューに保存し、次回以降に続きから巡回する。
- 番号通知に成功したルートURLは「通知済み」へ移し、通常巡回を停止する。
"""
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

JST = timezone(timedelta(hours=9))
CONFIG_FILE = "config.json"
HISTORY_FILE = "history.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

DEFAULTS = {
    "target_urls": [], "notified_urls": [], "max_target_urls": 120,
    "batch_size": 10, "check_interval_minutes": 30,
    "notification_cooldown_minutes": 60,
    "detect_mobile": True, "detect_landline": True, "detect_tollfree": True,
    "use_browser": True, "use_ocr": True, "ocr_max_images": 2,
    "max_pages_per_site_run": 3, "max_crawl_queue_per_site": 200,
    "max_pdfs_per_site_run": 1, "max_seconds_per_site": 45,
    "request_interval_seconds": 2, "rediscover_days": 7,
    "purge_no_phone_days": 40, "min_successful_checks_before_purge": 5,
    "error_alert_after": 3,
}

ASSET_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".mjs", ".map", ".zip", ".mp4", ".mp3", ".woff", ".woff2", ".ttf")
PDF_SUFFIX = ".pdf"
PRIORITY_WORDS = ("tokushoho", "特商法", "特定商取引", "specified", "contact", "inquiry", "お問い合わせ", "company", "会社概要", "privacy", "プライバシー", "terms", "利用規約", "legal")


# ──────────────────────────────────────────────
# 設定・履歴
# ──────────────────────────────────────────────
def now_jst():
    return datetime.now(JST)


def atomic_json_write(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp, path)


def load_config():
    cfg = dict(DEFAULTS)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg.update(json.load(f))
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    return cfg


def save_config(cfg):
    """秘密情報を除き、未知の設定を保ったまま保存する。"""
    payload = {k: v for k, v in cfg.items() if k not in ("telegram_bot_token", "telegram_chat_id")}
    atomic_json_write(CONFIG_FILE, payload)


def save_config_urls(urls):
    """既存呼び出しとの互換用。URL一覧だけを更新して他設定を保持する。"""
    cfg = load_config()
    cfg["target_urls"] = urls
    save_config(cfg)


def use_sheets_storage():
    """保存先をGoogleスプレッドシートに切り替える明示的なフラグ。"""
    return os.environ.get("STORAGE_BACKEND", "json").strip().lower() == "sheets"


def open_storage():
    """設定された保存先を開く。既定では従来のJSON保存を維持する。"""
    if not use_sheets_storage():
        return None
    from sheets_storage import SheetsStorage
    return SheetsStorage()


def load_history():
    base = {"notified": {}, "detected": {}, "failures": {}, "url_state": {}, "removed": [], "recent_runs": [], "batch_cursor": 0}
    if not os.path.exists(HISTORY_FILE):
        return base
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        # 破損時は既存ファイルを残し、最小状態で再開する。
        try:
            os.replace(HISTORY_FILE, HISTORY_FILE + ".corrupt")
        except Exception:
            pass
        return base
    for key, value in base.items():
        history.setdefault(key, value)
    return history


def save_history(history):
    # 48時間・最大2,000件だけを保持する。
    cutoff = now_jst() - timedelta(hours=48)
    rows = []
    for row in history.get("recent_runs", []):
        stamp = parse_stamp(row.get("at"))
        if stamp and stamp >= cutoff:
            rows.append(row)
    history["recent_runs"] = rows[-2000:]
    history["removed"] = history.get("removed", [])[-200:]
    atomic_json_write(HISTORY_FILE, history)


def parse_stamp(value):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def normalize_target_url(value, strip_query=False):
    raw = (value or "").strip()
    if not raw:
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and not re.match(r"^https?://", raw, re.I):
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        p = urllib.parse.urlsplit(raw)
    except Exception:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower()
    if host == "localhost" or host.endswith(".local") or re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.)", host):
        return None
    port = p.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = "" if strip_query else p.query
    return urllib.parse.urlunsplit((p.scheme.lower(), netloc, path, query, ""))


def same_host(url, root):
    try:
        return urllib.parse.urlsplit(url).hostname.lower() == urllib.parse.urlsplit(root).hostname.lower()
    except Exception:
        return False


def normalized_urls(values, limit):
    out, seen = [], set()
    for value in values:
        url = normalize_target_url(value)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= limit:
            break
    return out


def ensure_state(history, url, now):
    state = history["url_state"].setdefault(url, {})
    state.setdefault("added_at", history["detected"].get(url, now.isoformat()))
    state.setdefault("successful_checks", 0)
    state.setdefault("last_checked_at", None)
    state.setdefault("last_phone_detected_at", history["detected"].get(url))
    state.setdefault("last_result", "unknown")
    state.setdefault("last_error", None)
    state.setdefault("crawl_queue", [])
    state.setdefault("seen_pages", [])
    state.setdefault("last_discovery_at", None)
    state.setdefault("next_due_at", None)
    state.setdefault("lease_until", None)
    return state


def select_due_batch(urls, history, size, now):
    """保存先が分散しても、最も長く未巡回のURLから公平に選ぶ。"""
    size = max(1, min(int(size), len(urls))) if urls else 0
    far_future = datetime.max.replace(tzinfo=JST)
    def rank(url):
        state = history.get("url_state", {}).get(url, {})
        due = parse_stamp(state.get("next_due_at"))
        checked = parse_stamp(state.get("last_checked_at"))
        return (0 if not due or due <= now else 1, due or checked or datetime.min.replace(tzinfo=JST), checked or datetime.min.replace(tzinfo=JST), url)
    return sorted(urls, key=rank)[:size]


def select_batch(urls, history, size):
    if not urls:
        return []
    size = max(1, min(int(size), len(urls)))
    cursor = int(history.get("batch_cursor", 0)) % len(urls)
    batch = [urls[(cursor + i) % len(urls)] for i in range(size)]
    history["batch_cursor"] = (cursor + size) % len(urls)
    return batch


def purge_no_phone_urls(urls, history, cfg, now):
    keep, removed = [], []
    days = int(cfg.get("purge_no_phone_days", 40))
    min_checks = int(cfg.get("min_successful_checks_before_purge", 5))
    for url in urls:
        state = ensure_state(history, url, now)
        added = parse_stamp(state.get("added_at"))
        age = (now - added).total_seconds() / 86400 if added else 0
        if age >= days and not state.get("last_phone_detected_at") and int(state.get("successful_checks", 0)) >= min_checks:
            removed.append(url)
            history["removed"].append({"url": url, "removed_at": now.isoformat(), "reason": "no_phone_after_40_days", "successful_checks": state.get("successful_checks", 0)})
            history["url_state"].pop(url, None)
            history["failures"].pop(url, None)
        else:
            keep.append(url)
    return keep, removed


def recent_run(history, now, url, result, pages=0, phones=None, error=None):
    history["recent_runs"].append({
        "at": now.isoformat(), "url": url, "result": result, "pages": pages,
        "phones": [mask_phone(p) for p in (phones or [])], "error": (error or "")[:160] or None,
    })


# ──────────────────────────────────────────────
# 電話番号
# ──────────────────────────────────────────────
def normalize(phone):
    return re.sub(r"\D", "", phone)


def mask_phone(phone):
    digits = normalize(phone)
    if len(digits) in (10, 11):
        return f"{digits[:3]}-****-{digits[-4:]}"
    return "***"


def classify(phone):
    d = normalize(phone)
    if len(d) == 11:
        if d[:4] == "0800":
            return "tollfree"
        return "mobile" if d[:3] in ("090", "080", "070") else None
    if len(d) == 10:
        if d[:4] == "0120":
            return "tollfree"
        if d[0] == "0" and d[1] in "123456789" and d[:3] not in ("090", "080", "070", "050"):
            return "landline"
    return None


LABEL = {"mobile": "携帯", "landline": "固定電話", "tollfree": "フリーダイヤル"}
KNOWN_DUMMIES = {"09012345678", "08012345678", "07012345678", "09000000000", "08000000000", "07000000000", "09011111111", "09012341234", "09098765432", "0312345678", "0698765432", "0612345678", "0120000000", "0120123456", "08001234567", "0123456789", "0111111111"}


def phone_disposition(phone):
    d, kind = normalize(phone), classify(phone)
    if kind is None:
        return "excluded", "対象外の電話種別"
    if d in KNOWN_DUMMIES:
        return "excluded", "既知の見本番号"
    tail = d[-8:]
    if len(set(tail)) == 1:
        return "excluded", "末尾8桁が同一"
    if kind == "mobile" and tail[:4] == "0000":
        return "excluded", "携帯番号の中央4桁が0000"
    if len(set(tail)) <= 2:
        return "suspect", "末尾8桁が2種類以下"
    asc = "".join(str((int(tail[0]) + i) % 10) for i in range(8))
    desc = "".join(str((int(tail[0]) - i) % 10) for i in range(8))
    if tail in (asc, desc) or tail[:4] == tail[4:]:
        return "suspect", "連番または反復パターン"
    return "real", ""


def is_dummy(phone):
    return phone_disposition(phone)[0] == "excluded"


CANDIDATE = re.compile(r"(?<!\d)(0[\d\-\u2010-\u2015\uFF0D\u30FC\s\(\)]{8,16}\d)(?!\d)")


def clean_text(html):
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    tel = " ".join(re.findall(r"tel:([0-9\-\s\+]+)", text, flags=re.I))
    text = re.sub(r"<[^>]+>", " ", text) + " " + tel
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"[\u2010-\u2015\uFF0D\u30FC]", "-", text)


def find_phones(text, cfg):
    wanted = set()
    if cfg.get("detect_mobile", True): wanted.add("mobile")
    if cfg.get("detect_landline", True): wanted.add("landline")
    if cfg.get("detect_tollfree", True): wanted.add("tollfree")
    groups, seen = {"real": [], "excluded": [], "suspect": []}, {"real": set(), "excluded": set(), "suspect": set()}
    for raw in CANDIDATE.findall(text):
        phone, kind = raw.strip(), classify(raw)
        if kind not in wanted:
            continue
        disp, reason = phone_disposition(phone)
        key = normalize(phone)
        if key not in seen[disp]:
            seen[disp].add(key)
            groups[disp].append((phone, kind, reason))
    return groups


# ──────────────────────────────────────────────
# 取得・抽出
# ──────────────────────────────────────────────
class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.inside = False; self.title = ""
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title": self.inside = True
    def handle_endtag(self, tag):
        if tag.lower() == "title": self.inside = False
    def handle_data(self, data):
        if self.inside: self.title += data


def get_title(html):
    parser = TitleParser()
    try: parser.feed(html)
    except Exception: pass
    return parser.title.strip() or "(タイトルなし)"


def decode_html(raw, header_charset=None):
    for charset in [header_charset, "utf-8", "cp932", "euc-jp", "iso-2022-jp"]:
        if not charset: continue
        try: return raw.decode(charset)
        except Exception: continue
    return raw.decode("utf-8", errors="replace")


def fetch_simple(url, timeout=8, max_bytes=3_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes: return None, "本文が上限を超過", response.status
            content_type = response.headers.get_content_type()
            if "html" not in content_type and content_type not in ("text/plain", "application/xhtml+xml"):
                return None, f"HTMLではないコンテンツ: {content_type}", response.status
            return decode_html(data, response.headers.get_content_charset()), None, response.status
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code} {err.reason}", err.code
    except Exception as err:
        return None, str(err), None


def fetch_binary(url, timeout=6, max_bytes=4_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            return (None, "ファイルが上限を超過") if len(data) > max_bytes else (data, None)
    except Exception as err:
        return None, str(err)


def looks_js_rendered(html):
    if not html: return True
    body = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", html, flags=re.I)
    return len(re.sub(r"\s+", " ", body).strip()) < 120 or any(x in html for x in ("__NUXT__", "__NEXT_DATA__", 'id="root"', 'id="app"', "wix.com", "studio.design", "peraichi"))


def fetch_browser(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwrightが未インストール"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
            page.goto(url, timeout=15_000, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            html = page.content(); browser.close()
            return html, None
    except Exception as err:
        return None, f"ブラウザ描画に失敗: {err}"


def collect_images(html, base_url, limit):
    candidates = re.findall(r'<img[^>]+(?:src|data-src|data-lazy-src)=["\']([^"\']+)["\']', html, flags=re.I)
    candidates += re.findall(r'(?:srcset|data-srcset)=["\']([^"\']+)["\']', html, flags=re.I)
    out = []
    for value in candidates:
        value = value.split(",")[0].strip().split(" ")[0]
        full = normalize_target_url(urllib.parse.urljoin(base_url, value), strip_query=False)
        if full and same_host(full, base_url) and full not in out:
            out.append(full)
        if len(out) >= limit: break
    return out


def ocr_images(html, base_url, limit):
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return "", "OCRライブラリが未インストール"
    texts = []
    for url in collect_images(html, base_url, limit):
        data, error = fetch_binary(url)
        if not data: continue
        try:
            image = Image.open(io.BytesIO(data))
            if image.width < 60 or image.height < 20: continue
            if image.width < 600: image = image.resize((image.width * 2, image.height * 2))
            texts.append(pytesseract.image_to_string(image, lang="eng", timeout=5))
        except Exception: continue
    return "\n".join(texts), None


def extract_pdf_phones(pdf_url, cfg):
    data, error = fetch_binary(pdf_url, timeout=10)
    if not data: return {"real": [], "excluded": [], "suspect": []}, error
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:10])
        return find_phones(text, cfg), None
    except Exception as err:
        return {"real": [], "excluded": [], "suspect": []}, f"PDF抽出失敗: {err}"


def priority(url, label=""):
    marker = (url + " " + label).lower()
    if any(x in marker for x in ("tokushoho", "特商法", "特定商取引", "specified")): return 4
    if any(x in marker for x in ("contact", "inquiry", "お問い合わせ")): return 3
    if any(x in marker for x in ("company", "会社概要", "about")): return 2
    return 1


def public_links(html, page_url, root_url):
    """同一ドメインの公開HTML/PDFだけを返す。外部URLと資産は巡回しない。"""
    found, seen = [], set()
    pattern = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', re.I)
    for href, label_html in pattern.findall(html):
        url = normalize_target_url(urllib.parse.urljoin(page_url, href), strip_query=True)
        if not url or not same_host(url, root_url) or url in seen: continue
        path = urllib.parse.urlsplit(url).path.lower()
        if path.endswith(ASSET_SUFFIXES): continue
        seen.add(url)
        label = re.sub(r"<[^>]+>", " ", label_html)
        found.append((priority(url, label), url, path.endswith(PDF_SUFFIX)))
    return sorted(found, key=lambda row: (-row[0], row[1]))


def relevant_links(html, page_url, root_url, max_pages, max_pdfs):
    """後方互換用: 優先語を含むリンクを返す。"""
    links = public_links(html, page_url, root_url)
    pages = [url for score, url, is_pdf in links if not is_pdf and score > 1][:max_pages]
    pdfs = [url for score, url, is_pdf in links if is_pdf and score > 1][:max_pdfs]
    return pages, pdfs


def scan_html_page(page_url, cfg, allow_browser=False, allow_ocr=False):
    html, error, status = fetch_simple(page_url)
    empty = {"real": [], "excluded": [], "suspect": []}
    if html is None:
        return {"url": page_url, "html": "", "title": None, "items": empty, "via": None, "error": error, "status": status}
    title, items, via = get_title(html), find_phones(clean_text(html), cfg), None
    if items["real"]: via = "html"
    if allow_browser and not items["real"] and cfg.get("use_browser", True) and looks_js_rendered(html):
        rendered, _ = fetch_browser(page_url)
        if rendered:
            html = rendered; title = get_title(rendered) or title
            fresh = find_phones(clean_text(html), cfg)
            for key in items: items[key].extend(x for x in fresh[key] if x not in items[key])
            if items["real"]: via = "browser"
    if allow_ocr and not items["real"] and cfg.get("use_ocr", True):
        otext, _ = ocr_images(html, page_url, int(cfg.get("ocr_max_images", 2)))
        if otext:
            fresh = find_phones(clean_text(otext), cfg)
            for key in items: items[key].extend(x for x in fresh[key] if x not in items[key])
            if fresh["real"]: via = "ocr"
    return {"url": page_url, "html": html, "title": title, "items": items, "via": via, "error": None, "status": status}


# ──────────────────────────────────────────────
# 全ページを少しずつ巡回
# ──────────────────────────────────────────────
def reset_discovery_if_due(state, root_url, cfg, now):
    last = parse_stamp(state.get("last_discovery_at"))
    due = not last or (now - last).total_seconds() >= int(cfg.get("rediscover_days", 7)) * 86400
    if due:
        state["crawl_queue"] = []
        state["seen_pages"] = [root_url]
        state["last_discovery_at"] = now.isoformat()
    return due


def add_links_to_queue(state, links, cfg):
    seen = list(state.get("seen_pages", []))
    queue = list(state.get("crawl_queue", []))
    known = set(seen) | set(queue)
    max_queue = int(cfg.get("max_crawl_queue_per_site", 200))
    pdfs = 0
    for _score, url, is_pdf in links:
        if url in known: continue
        if is_pdf and pdfs >= int(cfg.get("max_pdfs_per_site_run", 1)): continue
        if len(queue) >= max_queue: break
        queue.append(url); known.add(url)
        if is_pdf: pdfs += 1
    state["crawl_queue"] = queue[:max_queue]
    state["seen_pages"] = seen[-max_queue:]


def scan_site(root_url, cfg, state=None, now=None):
    """ルート＋保存済みキューを最大ページ数だけ処理する。"""
    now = now or now_jst()
    state = state if state is not None else {"crawl_queue": [], "seen_pages": [], "last_discovery_at": None}
    reset_discovery_if_due(state, root_url, cfg, now)
    deadline = time.monotonic() + max(15, int(cfg.get("max_seconds_per_site", 45)))
    root = scan_html_page(root_url, cfg, allow_browser=True, allow_ocr=True)
    if root["error"]:
        return {"error": root["error"], "title": None, "real": [], "excluded": [], "suspect": [], "pages": [], "state": state}
    state["seen_pages"] = list(dict.fromkeys([root_url] + state.get("seen_pages", [])))[:int(cfg.get("max_crawl_queue_per_site", 200))]
    add_links_to_queue(state, public_links(root["html"], root_url, root_url), cfg)
    pages = [root]
    max_pages = max(1, int(cfg.get("max_pages_per_site_run", 3)))
    pdf_count = 0
    while state["crawl_queue"] and len(pages) < max_pages and time.monotonic() < deadline:
        page_url = state["crawl_queue"].pop(0)
        if page_url in state["seen_pages"]:
            continue
        state["seen_pages"].append(page_url)
        time.sleep(0.5)
        if page_url.lower().endswith(PDF_SUFFIX):
            if pdf_count >= int(cfg.get("max_pdfs_per_site_run", 1)): continue
            groups, error = extract_pdf_phones(page_url, cfg); pdf_count += 1
            pages.append({"url": page_url, "html": "", "title": "PDF", "items": groups, "via": "pdf", "error": error, "status": 200 if not error else None})
            continue
        page = scan_html_page(page_url, cfg, allow_browser=False, allow_ocr=False)
        pages.append(page)
        if not page["error"]:
            add_links_to_queue(state, public_links(page["html"], page_url, root_url), cfg)
    state["seen_pages"] = list(dict.fromkeys(state["seen_pages"]))[-int(cfg.get("max_crawl_queue_per_site", 200)):]

    out, seen = {"error": None, "title": root["title"], "real": [], "excluded": [], "suspect": [], "pages": pages, "state": state}, {"real": set(), "excluded": set(), "suspect": set()}
    for page in pages:
        for kind in seen:
            for phone, phone_kind, reason in page["items"][kind]:
                key = normalize(phone)
                if key not in seen[kind]:
                    seen[kind].add(key)
                    out[kind].append((phone, phone_kind, reason, page["url"], page["via"] or "html"))
    return out


# ──────────────────────────────────────────────
# 通知と状態移動
# ──────────────────────────────────────────────
def is_in_cooldown(history, root_url, phone, minutes):
    stamp = history["notified"].get(f"{root_url}|{normalize(phone)}")
    last = parse_stamp(stamp) if stamp else None
    return bool(last and (now_jst() - last).total_seconds() < minutes * 60)


def send_telegram(token, chat_id, text, tries=3):
    for chunk in [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]:
        sent = False
        for attempt in range(tries):
            try:
                body = json.dumps({"chat_id": chat_id, "text": chunk}).encode("utf-8")
                request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    sent = bool(json.loads(response.read()).get("ok"))
                if sent: break
            except Exception as err:
                print(f"  Telegram送信に失敗（{attempt + 1}/{tries}）: {err}")
            time.sleep(2 * (attempt + 1))
        if not sent: return False
    print("  Telegramに送信しました")
    return True


def build_message(root_url, items, title, now):
    lines = []
    for phone, kind, _reason, page_url, via in items:
        source = {"browser": "JS描画", "ocr": "画像OCR", "pdf": "PDF本文"}.get(via, "HTML")
        page_line = "" if page_url == root_url else f"\n　　掲載ページ: {page_url}"
        lines.append(f"　・{phone}（{LABEL[kind]}・{source}）{page_line}")
    return ("【電話番号を検知しました】\n\n" + f"監視URL：{root_url}\n検知日時：{now.strftime('%Y-%m-%d %H:%M:%S')}\n" + f"検出番号：\n{'\n'.join(lines)}\n" + f"トップページタイトル：{title or '(タイトルなし)'}\n\n" + "※通知後、このURLは管理画面の「通知済み」へ移動します。")


def build_error_message(url, count, error, now):
    return f"【監視エラー】\n\n監視URL：{url}\n発生日時：{now.strftime('%Y-%m-%d %H:%M:%S')}\n連続失敗：{count}回\n内容：{error[:180]}"


def move_to_notified(cfg, root_url, items, now):
    cfg["target_urls"] = [u for u in cfg.get("target_urls", []) if normalize_target_url(u) != root_url]
    existing = [x for x in cfg.get("notified_urls", []) if isinstance(x, dict) and normalize_target_url(x.get("url")) != root_url]
    existing.append({"url": root_url, "notified_at": now.isoformat(), "phones": [mask_phone(x[0]) for x in items], "status": "notified"})
    cfg["notified_urls"] = existing[-120:]


# ──────────────────────────────────────────────
# 実行
# ──────────────────────────────────────────────
def main():
    storage = open_storage()
    cfg = storage.load_config(DEFAULTS) if storage else load_config()
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    now = now_jst()
    token, chat_id = cfg["telegram_bot_token"], cfg["telegram_chat_id"]
    if not token or not chat_id:
        print("エラー: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が未設定です"); return
    urls = normalized_urls(cfg.get("target_urls", []), int(cfg.get("max_target_urls", 120)))
    cfg["target_urls"] = urls
    history = storage.load_history() if storage else load_history()
    for url in urls: ensure_state(history, url, now)
    urls, removed = purge_no_phone_urls(urls, history, cfg, now)
    cfg["target_urls"] = urls
    batch = select_due_batch(urls, history, cfg.get("batch_size", 10), now) if storage else select_batch(urls, history, cfg.get("batch_size", 10))
    cycle_minutes = max(int(cfg.get("check_interval_minutes", 30)), int(cfg.get("check_interval_minutes", 30)) * ((len(urls) + max(1, int(cfg.get("batch_size", 10))) - 1) // max(1, int(cfg.get("batch_size", 10)))))
    print(f"=== チェック開始 / 全{len(urls)}件中 {len(batch)}件 ===")
    config_changed, send_failed = bool(removed), 0

    for idx, url in enumerate(batch):
        if idx: time.sleep(max(1, int(cfg.get("request_interval_seconds", 2))))
        print(f"\n[確認中] {url}")
        state = ensure_state(history, url, now)
        state["lease_until"] = (now + timedelta(minutes=max(15, cycle_minutes))).isoformat()
        state["next_due_at"] = (now + timedelta(minutes=cycle_minutes)).isoformat()
        try: result = scan_site(url, cfg, state, now)
        except Exception as err: result = {"error": f"想定外のエラー: {type(err).__name__}: {err}", "real": [], "excluded": [], "suspect": [], "pages": [], "title": None}
        if result["error"]:
            record = history["failures"].get(url, {"count": 0, "alerted": False}); record["count"] += 1; record["last_error"] = result["error"][:200]
            history["failures"][url] = record; state.update({"last_checked_at": now.isoformat(), "last_result": "fetch_error", "last_error": result["error"][:200]})
            recent_run(history, now, url, "fetch_error", error=result["error"])
            print(f"  失敗（連続{record['count']}回）: {result['error'][:120]}")
            if record["count"] >= int(cfg.get("error_alert_after", 3)) and not record.get("alerted") and send_telegram(token, chat_id, build_error_message(url, record["count"], result["error"], now)): record["alerted"] = True
            continue
        history["failures"].pop(url, None); state["successful_checks"] = int(state.get("successful_checks", 0)) + 1; state.update({"last_checked_at": now.isoformat(), "last_error": None})
        if result["excluded"]: print(f"  見本番号として除外: {[p for p, *_ in result['excluded']]}")
        if result["suspect"]: print(f"  要確認パターン: {[p for p, *_ in result['suspect']]}")
        if not result["real"]:
            state["last_result"] = "suspect_only" if result["suspect"] else "ok_no_phone"; recent_run(history, now, url, state["last_result"], len(result["pages"])); print(f"  通知対象なし（今回{len(result['pages'])}ページ、待機キュー{len(state['crawl_queue'])}件）"); continue
        state["last_result"] = "phone_detected"; state["last_phone_detected_at"] = now.isoformat(); history["detected"].setdefault(url, now.isoformat())
        fresh = [x for x in result["real"] if not is_in_cooldown(history, url, x[0], cfg.get("notification_cooldown_minutes", 60))]
        if not fresh:
            recent_run(history, now, url, "phone_detected", len(result["pages"]), [x[0] for x in result["real"]]); print("  通知済み（再通知抑止中）"); continue
        print(f"  検出: {[p for p, *_ in fresh]}")
        if send_telegram(token, chat_id, build_message(url, fresh, result["title"], now)):
            for phone, *_ in fresh: history["notified"][f"{url}|{normalize(phone)}"] = now.isoformat()
            move_to_notified(cfg, url, fresh, now); history["url_state"].pop(url, None); recent_run(history, now, url, "notified", len(result["pages"]), [x[0] for x in fresh]); config_changed = True
        else:
            send_failed += 1; recent_run(history, now, url, "notification_failed", len(result["pages"]), [x[0] for x in fresh])

    if storage:
        storage.stage_config(cfg)
        storage.save(cfg, history)
    else:
        if config_changed:
            save_config(cfg)
        save_history(history)
    print("\n=== チェック終了 ===")
    if send_failed: raise SystemExit(1)


if __name__ == "__main__":
    main()
