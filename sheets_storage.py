"""GoogleスプレッドシートをHP電話番号監視の保存先として使うための実装。

設計上の要点:
- Active_URLsはURLハッシュで固定した4シャードを持つ。
- GitHub Actionsの各workerは自分のシャードだけを読み書きする。
- URL行は物理削除せずstatusを更新するため、並列実行時の行ずれを避ける。
- Recent_Runsは48時間・2,000件を超えないよう呼出し側で整理する。
- 認証JSONは必ずGitHub SecretのGOOGLE_SERVICE_ACCOUNT_JSONで渡す。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

JST = timezone(timedelta(hours=9))
SHEET_NAMES = {
    "active": "Active_URLs",
    "notified": "Notified_URLs",
    "recent": "Recent_Runs",
    "removed": "Removed_URLs",
    "settings": "Settings",
}
ACTIVE_HEADERS = [
    "url", "shard", "status", "added_at", "next_due_at", "lease_until", "lease_owner",
    "last_checked_at", "successful_checks", "last_phone_detected_at", "last_result", "last_error", "failure_count", "failure_alerted",
    "crawl_queue_json", "seen_pages_json", "last_discovery_at", "version", "updated_at",
    "phone_pages_json", "phone_absence_counts_json", "last_notified_phone_set_json", "phone_display_json",
]
NOTIFIED_HEADERS = ["url", "notified_at", "phones_json", "status", "updated_at"]
RECENT_HEADERS = ["at", "url", "result", "pages", "phones_json", "error", "run_id", "worker_id"]
REMOVED_HEADERS = ["url", "removed_at", "reason", "successful_checks", "updated_at"]
SETTINGS_HEADERS = ["key", "value"]


class SheetsConfigurationError(RuntimeError):
    """スプレッドシートまたは認証情報が未設定の場合の例外。"""


def shard_for_url(url: str, shard_count: int = 4) -> int:
    """URLを追加・削除しても変わらないシャード番号を返す。"""
    return int(hashlib.sha256(url.encode("utf-8")).hexdigest(), 16) % shard_count


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _cell(row: list[str], index: int, default: str = "") -> str:
    return str(row[index]) if len(row) > index and row[index] not in (None, "") else default


def _row_values(row: dict[str, Any]) -> list[Any]:
    return [row.get(key, "") for key in ACTIVE_HEADERS]


class SheetsStorage:
    """monitor.pyから従来のconfig/historyと同じ形で利用するSheetsアダプター。"""

    def __init__(self, spreadsheet_id: str | None = None, shard_id: int | None = None, shard_count: int = 4):
        self.spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEETS_ID", "").strip()
        self.shard_count = int(os.environ.get("WATCHER_SHARD_COUNT", shard_count))
        self.shard_id = int(os.environ.get("WATCHER_SHARD_ID", shard_id if shard_id is not None else 0))
        if not self.spreadsheet_id:
            raise SheetsConfigurationError("GOOGLE_SHEETS_ID が未設定です")
        if self.shard_id < 0 or self.shard_id >= self.shard_count:
            raise SheetsConfigurationError("WATCHER_SHARD_ID が不正です")
        self.worker_id = f"s{self.shard_id}-{uuid.uuid4().hex[:10]}"
        self.run_id = uuid.uuid4().hex
        self._service = None
        self._active_rows: dict[str, dict[str, Any]] = {}
        self._notified_urls: list[dict[str, Any]] = []
        self._staged_config: dict[str, Any] | None = None

    def _client(self):
        if self._service is not None:
            return self._service
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw:
            raise SheetsConfigurationError("GOOGLE_SERVICE_ACCOUNT_JSON が未設定です")
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SheetsConfigurationError("GOOGLE_SERVICE_ACCOUNT_JSON がJSON形式ではありません") from exc
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise SheetsConfigurationError("Google Sheets用ライブラリが未インストールです") from exc
        credentials = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def _values_get(self, ranges: list[str]) -> dict[str, list[list[str]]]:
        result = self._client().spreadsheets().values().batchGet(
            spreadsheetId=self.spreadsheet_id, ranges=ranges, majorDimension="ROWS"
        ).execute()
        out: dict[str, list[list[str]]] = {}
        for item in result.get("valueRanges", []):
            key = item.get("range", "").split("!")[0].replace("'", "")
            out[key] = item.get("values", [])
        return out

    def _batch_update(self, data: list[dict[str, Any]]) -> None:
        if data:
            self._client().spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()

    def _append(self, tab: str, values: list[list[Any]]) -> None:
        if values:
            self._client().spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{tab}!A:Z",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()

    def initialize(self, settings: dict[str, Any]) -> None:
        """空のスプレッドシートへ必須タブ・見出し・設定値を作成する。再実行しても安全。"""
        service = self._client()
        meta = service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        add = [
            {"addSheet": {"properties": {"title": tab}}}
            for tab in SHEET_NAMES.values() if tab not in existing
        ]
        if add:
            service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": add}).execute()
        self._batch_update([
            {"range": f"{SHEET_NAMES['active']}!A1:W1", "values": [ACTIVE_HEADERS]},
            {"range": f"{SHEET_NAMES['notified']}!A1:E1", "values": [NOTIFIED_HEADERS]},
            {"range": f"{SHEET_NAMES['recent']}!A1:H1", "values": [RECENT_HEADERS]},
            {"range": f"{SHEET_NAMES['removed']}!A1:E1", "values": [REMOVED_HEADERS]},
            {"range": f"{SHEET_NAMES['settings']}!A1:B1", "values": [SETTINGS_HEADERS]},
        ])
        current = self._values_get([f"{SHEET_NAMES['settings']}!A2:B"])
        found = {row[0]: row[1] for row in current.get(SHEET_NAMES["settings"], []) if len(row) >= 2}
        missing = [[str(key), json.dumps(value, ensure_ascii=False)] for key, value in settings.items() if key not in found]
        if missing:
            self._append(SHEET_NAMES["settings"], missing)

    def _load_settings(self) -> dict[str, Any]:
        rows = self._values_get([f"{SHEET_NAMES['settings']}!A2:B"]).get(SHEET_NAMES["settings"], [])
        out: dict[str, Any] = {}
        for row in rows:
            if len(row) < 2:
                continue
            try:
                out[row[0]] = json.loads(row[1])
            except json.JSONDecodeError:
                out[row[0]] = row[1]
        return out

    def load_config(self, defaults: dict[str, Any]) -> dict[str, Any]:
        # 既存シートにも新しい状態列の見出しを安全に追加する。担当0だけが行う。
        if self.shard_id == 0:
            self._batch_update([{"range": f"{SHEET_NAMES['active']}!A1:W1", "values": [ACTIVE_HEADERS]}])
        cfg = dict(defaults)
        cfg.update(self._load_settings())
        rows = self._values_get([
            f"{SHEET_NAMES['active']}!A2:W",
            f"{SHEET_NAMES['notified']}!A2:E",
        ])
        active = rows.get(SHEET_NAMES["active"], [])
        self._active_rows = {}
        urls: list[str] = []
        for sheet_row, row in enumerate(active, start=2):
            url, shard, status = _cell(row, 0), _cell(row, 1), _cell(row, 2)
            if not url or status != "active":
                continue
            if int(shard or 0) != self.shard_id:
                continue
            urls.append(url)
            self._active_rows[url] = {"sheet_row": sheet_row, "raw": row, "version": int(_cell(row, 17, "1") or 1)}
        self._notified_urls = []
        for row in rows.get(SHEET_NAMES["notified"], []):
            if _cell(row, 0) and _cell(row, 3, "notified") == "notified":
                self._notified_urls.append({
                    "url": _cell(row, 0), "notified_at": _cell(row, 1),
                    "phones": _json(_cell(row, 2), []), "status": "notified",
                })
        cfg["target_urls"] = urls
        cfg["notified_urls"] = self._notified_urls
        cfg["max_target_urls"] = max(1, int(cfg.get("max_target_urls", 500)))
        return cfg

    def load_history(self) -> dict[str, Any]:
        history = {"notified": {}, "detected": {}, "failures": {}, "url_state": {}, "removed": [], "recent_runs": [], "batch_cursor": 0}
        for url, source in self._active_rows.items():
            row = source["raw"]
            history["url_state"][url] = {
                "added_at": _cell(row, 3), "next_due_at": _cell(row, 4) or None,
                "lease_until": _cell(row, 5) or None, "successful_checks": int(_cell(row, 8, "0") or 0),
                "last_checked_at": _cell(row, 7) or None, "last_phone_detected_at": _cell(row, 9) or None,
                "last_result": _cell(row, 10, "unknown"), "last_error": _cell(row, 11) or None,
                "failure_count": int(_cell(row, 12, "0") or 0),
                "failure_alerted": _cell(row, 13, "false").lower() == "true",
                "crawl_queue": _json(_cell(row, 14), []), "seen_pages": _json(_cell(row, 15), []),
                "last_discovery_at": _cell(row, 16) or None,
                "phone_pages": _json(_cell(row, 19), {}),
                "phone_absence_counts": _json(_cell(row, 20), {}),
                "last_notified_phone_set": _json(_cell(row, 21), None),
                "phone_display": _json(_cell(row, 22), {}),
            }
            if history["url_state"][url]["failure_count"]:
                history["failures"][url] = {"count": history["url_state"][url]["failure_count"], "alerted": history["url_state"][url]["failure_alerted"]}
        recent = self._values_get([f"{SHEET_NAMES['recent']}!A2:H"]).get(SHEET_NAMES["recent"], [])
        cutoff = datetime.now(JST) - timedelta(hours=48)
        for row in recent[-2000:]:
            try:
                at = datetime.fromisoformat(_cell(row, 0))
            except ValueError:
                continue
            if at >= cutoff:
                history["recent_runs"].append({
                    "at": _cell(row, 0), "url": _cell(row, 1), "result": _cell(row, 2),
                    "pages": int(_cell(row, 3, "0") or 0), "phones": _json(_cell(row, 4), []),
                    "error": _cell(row, 5) or None,
                })
        history["_stored_recent_count"] = len(history["recent_runs"])
        return history

    def stage_config(self, cfg: dict[str, Any]) -> None:
        self._staged_config = cfg

    def _trim_recent(self) -> None:
        """先頭から48時間を過ぎた履歴を行ごと削除し、シート肥大化を防ぐ。"""
        if self.shard_id != 0:
            return
        rows = self._values_get([f"{SHEET_NAMES['recent']}!A2:A"]).get(SHEET_NAMES["recent"], [])
        cutoff = datetime.now(JST) - timedelta(hours=48)
        delete_count = 0
        for row in rows:
            try:
                stamp = datetime.fromisoformat(_cell(row, 0))
            except ValueError:
                delete_count += 1
                continue
            if stamp < cutoff:
                delete_count += 1
            else:
                break
        # 2,000件を上限にする。先頭からの追記順であることを前提に古い方を残さない。
        delete_count = max(delete_count, max(0, len(rows) - 2000))
        if not delete_count:
            return
        meta = self._client().spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties").execute()
        sheet_id = next(
            sheet["properties"]["sheetId"] for sheet in meta.get("sheets", [])
            if sheet["properties"]["title"] == SHEET_NAMES["recent"]
        )
        self._client().spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 1 + delete_count}}}]},
        ).execute()

    def save(self, cfg: dict[str, Any], history: dict[str, Any]) -> None:
        """このworkerが所有するURL状態だけを一括更新し、通知・削除は追記する。"""
        cfg = self._staged_config or cfg
        now = datetime.now(JST).isoformat()
        active_now = set(cfg.get("target_urls", []))
        notified_now = {str(item.get("url")): item for item in cfg.get("notified_urls", []) if isinstance(item, dict)}
        # 管理画面から状態が変わった行は、実行開始時の古い状態で上書きしない。
        latest_rows = self._values_get([f"{SHEET_NAMES['active']}!A2:W"]).get(SHEET_NAMES["active"], [])
        latest_by_url = {str(_cell(row, 0)): row for row in latest_rows if _cell(row, 0)}
        updates: list[dict[str, Any]] = []
        removed: list[list[Any]] = []
        updated_urls: set[str] = set()
        for url, source in self._active_rows.items():
            current = latest_by_url.get(url)
            if not current or _cell(current, 2) != "active" or int(_cell(current, 17, "1") or 1) != source["version"]:
                print(f"  状態変更を検出したため保存を見送り: {url}")
                continue
            state = history.get("url_state", {}).get(url)
            if state and url in active_now:
                row = [
                    url, self.shard_id, "active", state.get("added_at", now), state.get("next_due_at", ""),
                    state.get("lease_until", ""), self.worker_id, state.get("last_checked_at", ""),
                    int(state.get("successful_checks", 0)), state.get("last_phone_detected_at", ""),
                    state.get("last_result", "unknown"), state.get("last_error", ""),
                    int(history.get("failures", {}).get(url, {}).get("count", state.get("failure_count", 0))),
                    str(bool(history.get("failures", {}).get(url, {}).get("alerted", state.get("failure_alerted", False)))).lower(),
                    json.dumps(state.get("crawl_queue", []), ensure_ascii=False),
                    json.dumps(state.get("seen_pages", []), ensure_ascii=False), state.get("last_discovery_at", ""),
                    source["version"] + 1, now,
                    json.dumps(state.get("phone_pages", {}), ensure_ascii=False),
                    json.dumps(state.get("phone_absence_counts", {}), ensure_ascii=False),
                    json.dumps(state.get("last_notified_phone_set"), ensure_ascii=False),
                    json.dumps(state.get("phone_display", {}), ensure_ascii=False),
                ]
            else:
                item = notified_now.get(url)
                status = "notified" if item else "removed"
                row = [url, self.shard_id, status, _cell(source["raw"], 3, now), "", "", self.worker_id, now, _cell(source["raw"], 8, "0"), "", status, "", 0, "false", "[]", "[]", "", source["version"] + 1, now, "{}", "{}", "null", "{}"]
                if status == "removed":
                    removed.append([url, now, "no_phone_after_40_days", _cell(source["raw"], 8, "0"), now])
            updates.append({"range": f"{SHEET_NAMES['active']}!A{source['sheet_row']}:W{source['sheet_row']}", "values": [row]})
            updated_urls.add(url)
        self._batch_update(updates)
        newly_notified = [item for url, item in notified_now.items() if url in updated_urls and url not in {x.get("url") for x in self._notified_urls}]
        self._append(SHEET_NAMES["notified"], [[
            item.get("url"), item.get("notified_at", now), json.dumps(item.get("phones", []), ensure_ascii=False), "notified", now
        ] for item in newly_notified])
        self._append(SHEET_NAMES["removed"], removed)
        stored_count = int(history.get("_stored_recent_count", 0) or 0)
        recent = history.get("recent_runs", [])[stored_count:]
        self._append(SHEET_NAMES["recent"], [[
            row.get("at", now), row.get("url", ""), row.get("result", ""), int(row.get("pages", 0)),
            json.dumps(row.get("phones", []), ensure_ascii=False), row.get("error") or "", self.run_id, self.worker_id,
        ] for row in recent])
        self._trim_recent()


def add_active_url(service: Any, spreadsheet_id: str, url: str, added_at: str | None = None, shard_count: int = 4) -> int:
    """管理画面連携用の共通仕様。呼出し元は同じ23列を追加する。"""
    stamp = added_at or datetime.now(JST).isoformat()
    shard = shard_for_url(url, shard_count)
    values = [[url, shard, "active", stamp, stamp, "", "", "", 0, "", "unknown", "", 0, "false", "[]", "[]", "", 1, stamp, "{}", "{}", "null", "{}"]]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=f"{SHEET_NAMES['active']}!A:W", valueInputOption="RAW",
        insertDataOption="INSERT_ROWS", body={"values": values}
    ).execute()
    return shard
