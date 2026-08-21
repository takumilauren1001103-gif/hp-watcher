import unittest

from datetime import datetime, timedelta, timezone

from monitor import select_due_batch
from sheets_storage import ACTIVE_HEADERS, SHEET_NAMES, SheetsStorage, shard_for_url


class FakeStorage(SheetsStorage):
    def __init__(self, data, shard_id=0):
        self.spreadsheet_id = "test-sheet"
        self.shard_count = 4
        self.shard_id = shard_id
        self.worker_id = "test-worker"
        self.run_id = "test-run"
        self._service = None
        self._active_rows = {}
        self._notified_urls = []
        self._staged_config = None
        self.data = data
        self.updates = []
        self.appended = []
        self.trimmed = False

    def _values_get(self, ranges):
        output = {}
        for target in ranges:
            tab = target.split("!")[0]
            output[tab] = self.data.get(tab, [])
        return output

    def _batch_update(self, data):
        self.updates.extend(data)

    def _append(self, tab, values):
        self.appended.append((tab, values))

    def _trim_recent(self):
        self.trimmed = True


class SheetsStorageTests(unittest.TestCase):
    def test_url_shard_is_stable_and_spans_the_configured_range(self):
        url = "https://example.com/"
        self.assertEqual(shard_for_url(url), shard_for_url(url))
        values = {shard_for_url(f"https://example-{index}.com/") for index in range(50)}
        self.assertTrue(values.issubset({0, 1, 2, 3}))
        self.assertGreaterEqual(len(values), 3)

    def test_due_selection_prioritizes_overdue_urls_before_future_urls(self):
        now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone(timedelta(hours=9)))
        history = {"url_state": {
            "https://future.example/": {"next_due_at": "2026-08-21T14:00:00+09:00", "last_checked_at": "2026-08-21T08:00:00+09:00"},
            "https://old.example/": {"next_due_at": "2026-08-21T09:00:00+09:00", "last_checked_at": "2026-08-21T03:00:00+09:00"},
            "https://new.example/": {"next_due_at": "2026-08-21T11:00:00+09:00", "last_checked_at": "2026-08-21T06:00:00+09:00"},
        }}
        selected = select_due_batch(list(history["url_state"]), history, 2, now)
        self.assertEqual(selected, ["https://old.example/", "https://new.example/"])

    def test_loads_only_the_worker_owned_active_urls_and_state(self):
        own = "https://owned.example/"
        other = "https://other.example/"
        own_shard = shard_for_url(own)
        other_shard = (own_shard + 1) % 4
        data = {
            SHEET_NAMES["settings"]: [["batch_size", "11"], ["max_target_urls", "500"]],
            SHEET_NAMES["active"]: [
                [own, own_shard, "active", "2026-08-01T00:00:00+09:00", "", "", "", "", "3", "", "ok_no_phone", "", "0", "false", "[\"https://owned.example/contact\"]", "[]", "", "2", "2026-08-01T00:00:00+09:00"],
                [other, other_shard, "active", "2026-08-01T00:00:00+09:00", "", "", "", "", "2", "", "ok_no_phone", "", "0", "false", "[]", "[]", "", "1", "2026-08-01T00:00:00+09:00"],
            ],
            SHEET_NAMES["notified"]: [["https://done.example/", "2026-08-20T00:00:00+09:00", "[\"090-****-1234\"]", "notified", ""]],
            SHEET_NAMES["recent"]: [],
        }
        store = FakeStorage(data, own_shard)
        cfg = store.load_config({"batch_size": 10, "max_target_urls": 120})
        history = store.load_history()
        self.assertEqual(cfg["target_urls"], [own])
        self.assertEqual(cfg["notified_urls"][0]["url"], "https://done.example/")
        self.assertEqual(cfg["batch_size"], 11)
        self.assertEqual(history["url_state"][own]["successful_checks"], 3)
        self.assertEqual(history["url_state"][own]["crawl_queue"], ["https://owned.example/contact"])
        self.assertNotIn(other, history["url_state"])

    def test_save_updates_an_owned_url_and_appends_only_new_run_history(self):
        url = "https://owned.example/"
        shard = shard_for_url(url)
        data = {
            SHEET_NAMES["settings"]: [],
            SHEET_NAMES["active"]: [[url, shard, "active", "2026-08-01T00:00:00+09:00", "", "", "", "", "1", "", "unknown", "", "0", "false", "[]", "[]", "", "2", ""]],
            SHEET_NAMES["notified"]: [],
            SHEET_NAMES["recent"]: [],
        }
        store = FakeStorage(data, shard)
        cfg = store.load_config({"batch_size": 11, "max_target_urls": 500})
        history = store.load_history()
        history["url_state"][url].update({
            "successful_checks": 2, "last_result": "phone_changed", "last_checked_at": "2026-08-21T01:00:00+09:00",
            "phone_pages": {url: ["09010139984"]}, "phone_absence_counts": {},
            "last_notified_phone_set": ["09010139984"],
            "phone_display": {"09010139984": {"phone": "090-1013-9984", "kind": "mobile"}},
        })
        history["recent_runs"].append({"at": "2026-08-21T01:00:00+09:00", "url": url, "result": "ok_no_phone", "pages": 2, "phones": [], "error": None})
        store.save(cfg, history)
        self.assertEqual(len(ACTIVE_HEADERS), 23)
        state_updates = [item for item in store.updates if item["range"].startswith("Active_URLs!A2:")]
        self.assertEqual(state_updates[0]["range"], "Active_URLs!A2:W2")
        self.assertEqual(state_updates[0]["values"][0][8], 2)
        self.assertEqual(state_updates[0]["values"][0][19], '{"https://owned.example/": ["09010139984"]}')
        self.assertEqual(state_updates[0]["values"][0][21], '["09010139984"]')
        history_appends = [values for tab, values in store.appended if tab == SHEET_NAMES["recent"]]
        self.assertEqual(len(history_appends), 1)
        self.assertEqual(history_appends[0][0][1], url)
        self.assertTrue(store.trimmed)


if __name__ == "__main__":
    unittest.main()
