#!/usr/bin/env python3
"""monitor.py v4 のネットワーク不要な回帰テスト。Telegram送信は行わない。"""
import json
import os
import tempfile
import unittest
from datetime import timedelta

import monitor


class MonitorV4Test(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(monitor.DEFAULTS)
        self.cfg.update({"detect_mobile": True, "detect_landline": True, "detect_tollfree": True})

    def test_dnd_tokushoho_phone_is_real(self):
        groups = monitor.find_phones("電話番号 090-1013-9984", self.cfg)
        self.assertEqual([monitor.normalize(x[0]) for x in groups["real"]], ["09010139984"])
        self.assertEqual(groups["excluded"], [])

    def test_known_sample_is_excluded(self):
        groups = monitor.find_phones("090-0000-0000", self.cfg)
        self.assertEqual(len(groups["excluded"]), 1)
        self.assertEqual(groups["real"], [])

    def test_pattern_number_is_suspect_not_discarded(self):
        groups = monitor.find_phones("070-4567-8901", self.cfg)
        self.assertEqual(len(groups["suspect"]), 1)
        self.assertEqual(groups["excluded"], [])

    def test_relevant_link_finds_tokushoho(self):
        html = '''<a href="contact.html">お問い合わせ</a>
                  <a href="privacy.html">プライバシー</a>
                  <a href="tokushoho.html">特商法</a>
                  <a href="assets/rules.pdf">特定商取引資料</a>
                  <a href="https://outside.example/legal">外部</a>'''
        pages, pdfs = monitor.relevant_links(html, "https://dnd-inc.jp/", "https://dnd-inc.jp/", 3, 1)
        self.assertIn("https://dnd-inc.jp/tokushoho.html", pages)
        self.assertIn("https://dnd-inc.jp/contact.html", pages)
        self.assertEqual(pdfs, ["https://dnd-inc.jp/assets/rules.pdf"])
        self.assertNotIn("https://outside.example/legal", pages)

    def test_url_normalization_removes_fragment_and_duplicate_slash(self):
        self.assertEqual(monitor.normalize_target_url("https://example.com/#x"), "https://example.com/")
        self.assertEqual(monitor.normalize_target_url("example.com/path///#y"), "https://example.com/path")
        self.assertIsNone(monitor.normalize_target_url("file:///tmp/x"))
        self.assertIsNone(monitor.normalize_target_url("http://127.0.0.1/test"))

    def test_batch_rotates_without_drop_or_duplicate(self):
        urls = [f"https://example{i}.com/" for i in range(25)]
        history = {"batch_cursor": 0}
        rounds = []
        for _ in range(3):
            rounds.append(monitor.select_batch(urls, history, 10))
        self.assertEqual(rounds[0], urls[:10])
        self.assertEqual(rounds[1], urls[10:20])
        self.assertEqual(rounds[2], urls[20:] + urls[:5])
        self.assertEqual(len(set(rounds[0] + rounds[1] + rounds[2][:5])), 25)

    def test_one_hundred_urls_are_covered_in_ten_runs(self):
        urls = [f"https://site{i}.example/" for i in range(100)]
        history = {"batch_cursor": 0}
        covered = []
        for _ in range(10):
            covered.extend(monitor.select_batch(urls, history, 10))
        self.assertEqual(len(covered), 100)
        self.assertEqual(set(covered), set(urls))

    def test_purge_only_after_40_days_and_successful_checks(self):
        now = monitor.now_jst()
        old = (now - timedelta(days=41)).isoformat()
        history = {
            "notified": {}, "detected": {}, "failures": {}, "batch_cursor": 0, "removed": [],
            "url_state": {
                "https://remove.example/": {"added_at": old, "successful_checks": 5, "last_phone_detected_at": None},
                "https://keep-error.example/": {"added_at": old, "successful_checks": 0, "last_phone_detected_at": None},
                "https://keep-phone.example/": {"added_at": old, "successful_checks": 7, "last_phone_detected_at": old},
            },
        }
        cfg = dict(self.cfg)
        cfg.update({"purge_no_phone_days": 40, "min_successful_checks_before_purge": 5})
        keep, removed = monitor.purge_no_phone_urls(list(history["url_state"].keys()), history, cfg, now)
        self.assertEqual(removed, ["https://remove.example/"])
        self.assertIn("https://keep-error.example/", keep)
        self.assertIn("https://keep-phone.example/", keep)
        self.assertEqual(history["removed"][-1]["reason"], "no_phone_after_40_days")

    def test_config_url_update_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            original = monitor.CONFIG_FILE
            try:
                monitor.CONFIG_FILE = path
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"target_urls": ["https://old.example/"], "max_target_urls": 120, "purge_no_phone_days": 40}, f)
                monitor.save_config_urls(["https://new.example/"])
                with open(path, encoding="utf-8") as f:
                    updated = json.load(f)
                self.assertEqual(updated["target_urls"], ["https://new.example/"])
                self.assertEqual(updated["max_target_urls"], 120)
                self.assertEqual(updated["purge_no_phone_days"], 40)
            finally:
                monitor.CONFIG_FILE = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
