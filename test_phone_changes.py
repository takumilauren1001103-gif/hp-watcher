#!/usr/bin/env python3
"""初回検出と番号変化通知のネットワーク不要な回帰テスト。"""
import unittest

import monitor


ROOT = "https://example.com/"
CONTACT = "https://example.com/contact"


def page(url, phones=None, error=None, reliable=True):
    items = {"real": [], "excluded": [], "suspect": []}
    for phone, kind in phones or []:
        items["real"].append((phone, kind, ""))
    return {"url": url, "items": items, "error": error, "presence_reliable": reliable, "via": "html"}


class PhoneChangeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(monitor.DEFAULTS)
        self.cfg["phone_change_remove_confirmations"] = 2

    def state(self):
        history = {"detected": {}, "url_state": {}}
        return monitor.ensure_state(history, ROOT, monitor.now_jst())

    def test_known_dummy_048_900_0000_is_never_real(self):
        groups = monitor.find_phones("電話: 048-900-0000 / 0489000000", self.cfg)
        self.assertEqual(groups["real"], [])
        self.assertEqual({monitor.normalize(item[0]) for item in groups["excluded"]}, {"0489000000"})

    def test_first_valid_phone_creates_initial_event(self):
        state = self.state()
        snapshot = monitor.update_phone_pages(state, [page(ROOT, [("090-1013-9984", "mobile")])], self.cfg)
        event = monitor.phone_event(state, snapshot)
        self.assertEqual(event["type"], "initial")
        self.assertEqual(event["added"], ["09010139984"])
        self.assertEqual(event["removed"], [])

    def test_addition_after_notification_creates_change_event(self):
        state = self.state()
        state["phone_pages"] = {ROOT: ["09010139984"]}
        state["phone_display"] = {"09010139984": {"phone": "090-1013-9984", "kind": "mobile", "page_url": ROOT, "via": "html"}}
        state["last_notified_phone_set"] = ["09010139984"]
        snapshot = monitor.update_phone_pages(state, [page(ROOT, [("090-1013-9984", "mobile"), ("03-4567-8901", "landline")])], self.cfg)
        event = monitor.phone_event(state, snapshot)
        self.assertEqual(event["type"], "changed")
        self.assertEqual(event["added"], ["0345678901"])
        self.assertEqual(event["removed"], [])

    def test_removal_requires_two_successful_empty_checks(self):
        state = self.state()
        state["phone_pages"] = {ROOT: ["09010139984"]}
        state["phone_display"] = {"09010139984": {"phone": "090-1013-9984", "kind": "mobile", "page_url": ROOT, "via": "html"}}
        state["last_notified_phone_set"] = ["09010139984"]
        first = monitor.update_phone_pages(state, [page(ROOT)], self.cfg)
        self.assertEqual(first["current"], {"09010139984"})
        self.assertIsNone(monitor.phone_event(state, first))
        second = monitor.update_phone_pages(state, [page(ROOT)], self.cfg)
        event = monitor.phone_event(state, second)
        self.assertEqual(second["current"], set())
        self.assertEqual(event["removed"], ["09010139984"])

    def test_partial_crawl_preserves_numbers_from_unvisited_pages(self):
        state = self.state()
        state["phone_pages"] = {CONTACT: ["0345678901"]}
        state["phone_display"] = {"0345678901": {"phone": "03-4567-8901", "kind": "landline", "page_url": CONTACT, "via": "html"}}
        state["last_notified_phone_set"] = ["0345678901"]
        snapshot = monitor.update_phone_pages(state, [page(ROOT, [("090-1013-9984", "mobile")])], self.cfg)
        event = monitor.phone_event(state, snapshot)
        self.assertEqual(snapshot["current"], {"0345678901", "09010139984"})
        self.assertEqual(event["added"], ["09010139984"])
        self.assertEqual(event["removed"], [])

    def test_fetch_or_render_failure_never_erases_prior_page_numbers(self):
        state = self.state()
        state["phone_pages"] = {ROOT: ["09010139984"]}
        state["phone_display"] = {"09010139984": {"phone": "090-1013-9984", "kind": "mobile", "page_url": ROOT, "via": "html"}}
        state["last_notified_phone_set"] = ["09010139984"]
        for failed_page in [page(ROOT, error="HTTP 503"), page(ROOT, reliable=False)]:
            snapshot = monitor.update_phone_pages(state, [failed_page], self.cfg)
            self.assertEqual(snapshot["current"], {"09010139984"})
            self.assertIsNone(monitor.phone_event(state, snapshot))

    def test_change_message_explains_added_and_removed_numbers(self):
        state = self.state()
        state["phone_pages"] = {ROOT: ["09010139984"]}
        state["phone_display"] = {"09010139984": {"phone": "090-1013-9984", "kind": "mobile", "page_url": ROOT, "via": "html"}}
        state["last_notified_phone_set"] = ["09010139984"]
        snapshot = monitor.update_phone_pages(state, [page(ROOT, [("03-4567-8901", "landline")])], {**self.cfg, "phone_change_remove_confirmations": 1})
        event = monitor.phone_event(state, snapshot)
        text = monitor.build_change_message(ROOT, event, snapshot, "テスト", monitor.now_jst())
        self.assertIn("電話番号の変化", text)
        self.assertIn("03-4567-8901", text)
        self.assertIn("090-1013-9984", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
