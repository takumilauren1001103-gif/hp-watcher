"""既存のconfig.json / history.jsonをGoogleスプレッドシートへ一度だけ移す。

実行前にGOOGLE_SHEETS_IDとGOOGLE_SERVICE_ACCOUNT_JSONを環境変数へ設定する。
繰り返しても同じURLや履歴を重複追加しないよう、既存データを確認してから追記する。
"""
from __future__ import annotations

import json
from datetime import datetime

from monitor import DEFAULTS, load_config, load_history, normalize_target_url, now_jst
from sheets_storage import SHEET_NAMES, SheetsStorage, shard_for_url


def active_row(url, state, failures, now):
    failure = failures.get(url, {})
    return [
        url, shard_for_url(url), "active", state.get("added_at") or now, state.get("next_due_at") or now,
        state.get("lease_until") or "", "", state.get("last_checked_at") or "",
        int(state.get("successful_checks", 0)), state.get("last_phone_detected_at") or "",
        state.get("last_result", "unknown"), state.get("last_error") or "", int(failure.get("count", 0)),
        str(bool(failure.get("alerted", False))).lower(), json.dumps(state.get("crawl_queue", []), ensure_ascii=False),
        json.dumps(state.get("seen_pages", []), ensure_ascii=False), state.get("last_discovery_at") or "", 1, now,
    ]


def main():
    storage = SheetsStorage(shard_id=0)
    cfg = load_config()
    history = load_history()
    settings = {
        key: value for key, value in cfg.items()
        if key not in {"target_urls", "notified_urls", "telegram_bot_token", "telegram_chat_id", "max_target_urls", "batch_size"}
    }
    settings.update({"max_target_urls": 500, "batch_size": 11, "check_interval_minutes": 30})
    storage.initialize(settings)

    all_rows = storage._values_get([
        f"{SHEET_NAMES['active']}!A2:A", f"{SHEET_NAMES['notified']}!A2:A",
        f"{SHEET_NAMES['recent']}!A2:A", f"{SHEET_NAMES['removed']}!A2:A",
    ])
    existing_active = {row[0] for row in all_rows.get(SHEET_NAMES["active"], []) if row}
    existing_notified = {row[0] for row in all_rows.get(SHEET_NAMES["notified"], []) if row}
    now = now_jst().isoformat()
    targets = []
    seen = set()
    for raw in cfg.get("target_urls", []):
        url = normalize_target_url(raw)
        if url and url not in seen:
            seen.add(url)
            targets.append(url)
    appended_active = [
        active_row(url, history.get("url_state", {}).get(url, {}), history.get("failures", {}), now)
        for url in targets if url not in existing_active
    ]
    storage._append(SHEET_NAMES["active"], appended_active)

    notified = []
    for item in cfg.get("notified_urls", []):
        if not isinstance(item, dict):
            continue
        url = normalize_target_url(item.get("url"))
        if not url or url in existing_notified:
            continue
        notified.append([url, item.get("notified_at") or now, json.dumps(item.get("phones", []), ensure_ascii=False), "notified", now])
    storage._append(SHEET_NAMES["notified"], notified)

    existing_recent = {row[0] + "|" + row[1] for row in all_rows.get(SHEET_NAMES["recent"], []) if len(row) >= 2}
    recent = []
    for row in history.get("recent_runs", [])[-2000:]:
        key = str(row.get("at", "")) + "|" + str(row.get("url", ""))
        if not row.get("at") or key in existing_recent:
            continue
        recent.append([
            row.get("at"), row.get("url", ""), row.get("result", ""), int(row.get("pages", 0)),
            json.dumps(row.get("phones", []), ensure_ascii=False), row.get("error") or "", "migration", "migration",
        ])
    storage._append(SHEET_NAMES["recent"], recent)

    existing_removed = {row[0] for row in all_rows.get(SHEET_NAMES["removed"], []) if row}
    removed = [
        [row.get("url"), row.get("removed_at") or now, row.get("reason", ""), int(row.get("successful_checks", 0)), now]
        for row in history.get("removed", [])[-200:] if row.get("url") and row.get("url") not in existing_removed
    ]
    storage._append(SHEET_NAMES["removed"], removed)
    storage._trim_recent()
    print(f"移行完了: 監視中 {len(appended_active)}件 / 通知済み {len(notified)}件 / 履歴 {len(recent)}件 / 削除履歴 {len(removed)}件")


if __name__ == "__main__":
    main()
