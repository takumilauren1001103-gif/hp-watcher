#!/usr/bin/env python3
"""monitor.py v5 のネットワーク不要な全ページ巡回・状態管理テスト。"""
import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

import monitor


class MonitorV5Test(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(monitor.DEFAULTS)
        self.cfg.update({"use_browser": False, "use_ocr": False, "max_pages_per_site_run": 3, "max_pdfs_per_site_run": 1})

    def test_public_links_keeps_same_host_html_and_pdf_only(self):
        html = '''
          <a href="/blog/a.html">記事A</a>
          <a href="/tokushoho.html">特商法</a>
          <a href="/files/rules.pdf">資料</a>
          <a href="https://outside.example/contact">外部</a>
          <a href="/assets/app.js">JS</a>
          <a href="mailto:x@example.com">メール</a>
        '''
        links = monitor.public_links(html, "https://example.com/", "https://example.com/")
        values = [x[1] for x in links]
        self.assertEqual(values[0], "https://example.com/tokushoho.html")
        self.assertIn("https://example.com/blog/a.html", values)
        self.assertIn("https://example.com/files/rules.pdf", values)
        self.assertNotIn("https://outside.example/contact", values)
        self.assertFalse(any(x.endswith("app.js") for x in values))

    def test_scan_site_advances_queue_in_small_steps(self):
        root_html = '''<a href="/tokushoho.html">特商法</a><a href="/a.html">A</a><a href="/b.html">B</a><a href="/c.html">C</a>'''
        def page(url, cfg, allow_browser=False, allow_ocr=False, force_ocr=False):
            return {"url": url, "html": root_html if url.endswith("/") else "", "title": "title", "items": {"real": [], "excluded": [], "suspect": []}, "via": None, "error": None, "status": 200}
        state = {"crawl_queue": [], "seen_pages": [], "last_discovery_at": None}
        with patch.object(monitor, "scan_html_page", side_effect=page), patch.object(monitor.time, "sleep"):
            result = monitor.scan_site("https://example.com/", self.cfg, state, monitor.now_jst())
        self.assertEqual([x["url"] for x in result["pages"]], ["https://example.com/", "https://example.com/tokushoho.html", "https://example.com/a.html"])
        self.assertIn("https://example.com/b.html", result["state"]["crawl_queue"])
        self.assertIn("https://example.com/c.html", result["state"]["crawl_queue"])

    def test_notify_move_removes_active_and_keeps_masked_record(self):
        cfg = {"target_urls": ["https://one.example/", "https://two.example/"], "notified_urls": []}
        now = monitor.now_jst()
        item = ("090-1013-9984", "mobile", "", "https://one.example/tokushoho.html", "html")
        monitor.move_to_notified(cfg, "https://one.example/", [item], now)
        self.assertEqual(cfg["target_urls"], ["https://two.example/"])
        self.assertEqual(cfg["notified_urls"][0]["url"], "https://one.example/")
        self.assertEqual(cfg["notified_urls"][0]["phones"], ["090-****-9984"])

    def test_recent_runs_are_limited_to_48_hours_and_2000_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "history.json")
            original = monitor.HISTORY_FILE
            try:
                monitor.HISTORY_FILE = path
                now = monitor.now_jst()
                history = {"recent_runs": [
                    {"at": (now - timedelta(hours=49)).isoformat(), "url": "https://old.example/"},
                    {"at": (now - timedelta(hours=1)).isoformat(), "url": "https://new.example/"},
                ], "removed": []}
                monitor.save_history(history)
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual([x["url"] for x in saved["recent_runs"]], ["https://new.example/"])
            finally:
                monitor.HISTORY_FILE = original

    def test_phone_notification_is_notified_result_not_plain_detection(self):
        groups = monitor.find_phones("090-1013-9984", self.cfg)
        self.assertEqual(len(groups["real"]), 1)
        self.assertEqual(groups["real"][0][0], "090-1013-9984")


if __name__ == "__main__":
    unittest.main(verbosity=2)
