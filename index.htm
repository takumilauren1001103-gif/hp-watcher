import { useState, useEffect, useCallback } from "react";

const STORAGE_KEY = "watcher:state";

export default function PhoneWatcherHP() {
  const [urls, setUrls] = useState([]);
  const [newUrl, setNewUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [toast, setToast] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [ghToken, setGhToken] = useState("");
  const [ghRepo, setGhRepo] = useState("hp-watcher");
  const [ghOwner, setGhOwner] = useState("");
  const [cooldown, setCooldown] = useState(60);
  const [interval, setInterval_] = useState(10);
  const [setupDone, setSetupDone] = useState(false);
  const [log, setLog] = useState([]);

  const showToast = useCallback((msg, ms = 2800) => {
    setToast(msg);
    setTimeout(() => setToast(""), ms);
  }, []);

  // ── Load state ──
  useEffect(() => {
    (async () => {
      try {
        const r = await window.storage.get(STORAGE_KEY);
        if (r?.value) {
          const s = JSON.parse(r.value);
          setUrls(s.urls || []);
          setGhToken(s.ghToken || "");
          setGhRepo(s.ghRepo || "hp-watcher");
          setGhOwner(s.ghOwner || "");
          setCooldown(s.cooldown || 60);
          setInterval_(s.interval || 10);
          setLog(s.log || []);
          if (s.ghToken && s.ghOwner) setSetupDone(true);
        }
      } catch (e) { console.error(e); }
      setLoading(false);
    })();
  }, []);

  // ── Save state ──
  const saveState = async (overrides = {}) => {
    const state = {
      urls: overrides.urls ?? urls,
      ghToken: overrides.ghToken ?? ghToken,
      ghRepo: overrides.ghRepo ?? ghRepo,
      ghOwner: overrides.ghOwner ?? ghOwner,
      cooldown: overrides.cooldown ?? cooldown,
      interval: overrides.interval ?? interval,
      log: overrides.log ?? log,
    };
    try {
      await window.storage.set(STORAGE_KEY, JSON.stringify(state));
    } catch (e) { console.error(e); }
  };

  const addLog = (msg) => {
    const time = new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
    const next = [{ time, msg }, ...log].slice(0, 30);
    setLog(next);
    return next;
  };

  // ── Push config.json to GitHub ──
  const pushToGitHub = async (urlList, opts = {}) => {
    const token = opts.ghToken || ghToken;
    const owner = opts.ghOwner || ghOwner;
    const repo = opts.ghRepo || ghRepo;
    const cd = opts.cooldown ?? cooldown;
    const iv = opts.interval ?? interval;

    if (!token || !owner) {
      showToast("GitHub設定が未完了です");
      setShowSetup(true);
      return false;
    }

    const config = {
      target_urls: urlList,
      check_interval_minutes: iv,
      notification_cooldown_minutes: cd,
    };
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(config, null, 2) + "\n")));
    const apiUrl = `https://api.github.com/repos/${owner}/${repo}/contents/config.json`;

    try {
      // Get current file SHA
      const getResp = await fetch(apiUrl, {
        headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github.v3+json" },
      });
      let sha = null;
      if (getResp.ok) {
        const data = await getResp.json();
        sha = data.sha;
      }

      // Update file
      const putBody = {
        message: `監視URL更新 (${new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" })})`,
        content,
      };
      if (sha) putBody.sha = sha;

      const putResp = await fetch(apiUrl, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github.v3+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(putBody),
      });

      if (putResp.ok) return true;
      const err = await putResp.json();
      console.error("GitHub PUT error:", err);
      showToast("GitHub更新に失敗しました: " + (err.message || putResp.status));
      return false;
    } catch (e) {
      console.error(e);
      showToast("通信エラー: " + e.message);
      return false;
    }
  };

  // ── Add URL ──
  const addUrl = async () => {
    let trimmed = newUrl.trim();
    if (!trimmed) { setUrlError("URLを入力してください"); return; }
    if (!trimmed.startsWith("http")) trimmed = "https://" + trimmed;
    try { new URL(trimmed); } catch { setUrlError("正しいURLを入力してください"); return; }
    if (urls.includes(trimmed)) { setUrlError("登録済みです"); return; }

    setUrlError("");
    setSyncing(true);
    const next = [...urls, trimmed];
    const ok = await pushToGitHub(next);
    if (ok) {
      setUrls(next);
      const newLog = addLog(`追加: ${trimmed}`);
      await saveState({ urls: next, log: newLog });
      setNewUrl("");
      showToast("追加しました。次の巡回から監視開始します");
    }
    setSyncing(false);
  };

  // ── Remove URL ──
  const removeUrl = async (url) => {
    setSyncing(true);
    const next = urls.filter((u) => u !== url);
    const ok = await pushToGitHub(next);
    if (ok) {
      setUrls(next);
      const newLog = addLog(`削除: ${url}`);
      await saveState({ urls: next, log: newLog });
      showToast("削除しました");
    }
    setSyncing(false);
  };

  // ── Save setup ──
  const saveSetup = async () => {
    if (!ghToken.trim() || !ghOwner.trim()) {
      showToast("トークンとユーザー名は必須です");
      return;
    }
    setSyncing(true);
    const ok = await pushToGitHub(urls, {
      ghToken: ghToken.trim(),
      ghOwner: ghOwner.trim(),
      ghRepo: ghRepo.trim(),
      cooldown,
      interval: interval,
    });
    if (ok) {
      await saveState({
        ghToken: ghToken.trim(),
        ghOwner: ghOwner.trim(),
        ghRepo: ghRepo.trim(),
      });
      setSetupDone(true);
      setShowSetup(false);
      showToast("設定を保存しました");
    } else {
      showToast("接続テストに失敗しました。トークンとユーザー名を確認してください");
    }
    setSyncing(false);
  };

  // ── Styles ──
  const c = {
    accent: "#0D9373",
    accentHover: "#0B7D63",
    bg: "var(--surface-2, #fff)",
    card: "var(--surface-1, #f9f9f7)",
    border: "var(--border, #e5e5e3)",
    text: "var(--text-primary, #1a1a1a)",
    sub: "var(--text-secondary, #666)",
    muted: "var(--text-muted, #999)",
    danger: "#c53030",
  };

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "80px 0", color: c.sub, fontSize: 14 }}>
        読み込み中...
      </div>
    );
  }

  // ── Setup screen ──
  if (!setupDone || showSetup) {
    return (
      <div style={{ maxWidth: 520, margin: "0 auto", fontFamily: "var(--font-sans, system-ui)" }}>
        <div style={{ padding: "40px 0 20px" }}>
          <h1 style={{ fontSize: 20, fontWeight: 500, margin: 0, color: c.text }}>
            初期設定
          </h1>
          <p style={{ fontSize: 14, color: c.sub, marginTop: 6 }}>
            GitHubと接続して、画面からURLを追加できるようにします。
          </p>
        </div>

        <div style={{ background: c.card, border: `1px solid ${c.border}`, borderRadius: 12, padding: 20 }}>
          <div style={{ marginBottom: 20 }}>
            <label style={{ fontSize: 13, fontWeight: 500, color: c.sub, display: "block", marginBottom: 6 }}>
              GitHubユーザー名
            </label>
            <input
              style={{ ...inputStyle(c), width: "100%", boxSizing: "border-box" }}
              placeholder="takumilauren1001103-gif"
              value={ghOwner}
              onChange={(e) => setGhOwner(e.target.value)}
            />
            <div style={{ fontSize: 12, color: c.muted, marginTop: 4 }}>
              github.com/○○○/hp-watcher の ○○○ の部分
            </div>
          </div>

          <div style={{ marginBottom: 20 }}>
            <label style={{ fontSize: 13, fontWeight: 500, color: c.sub, display: "block", marginBottom: 6 }}>
              リポジトリ名
            </label>
            <input
              style={{ ...inputStyle(c), width: "100%", boxSizing: "border-box" }}
              value={ghRepo}
              onChange={(e) => setGhRepo(e.target.value)}
            />
          </div>

          <div style={{ marginBottom: 20 }}>
            <label style={{ fontSize: 13, fontWeight: 500, color: c.sub, display: "block", marginBottom: 6 }}>
              Personal Access Token
            </label>
            <input
              style={{ ...inputStyle(c), width: "100%", boxSizing: "border-box" }}
              type="password"
              placeholder="github_pat_..."
              value={ghToken}
              onChange={(e) => setGhToken(e.target.value)}
            />
            <div style={{ fontSize: 12, color: c.muted, marginTop: 4, lineHeight: 1.8 }}>
              作り方：GitHub → Settings → Developer settings →<br />
              Personal access tokens → Fine-grained tokens → Generate new token<br />
              → Repository access で「hp-watcher」を選択<br />
              → Permissions → Contents を「Read and write」に → Generate token
            </div>
          </div>

          <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 13, fontWeight: 500, color: c.sub, display: "block", marginBottom: 6 }}>
                巡回間隔（分）
              </label>
              <input
                style={{ ...inputStyle(c), width: "100%", boxSizing: "border-box" }}
                type="number" min={5} max={60}
                value={interval}
                onChange={(e) => setInterval_(parseInt(e.target.value) || 10)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 13, fontWeight: 500, color: c.sub, display: "block", marginBottom: 6 }}>
                再通知抑止（分）
              </label>
              <input
                style={{ ...inputStyle(c), width: "100%", boxSizing: "border-box" }}
                type="number" min={5} max={1440}
                value={cooldown}
                onChange={(e) => setCooldown(parseInt(e.target.value) || 60)}
              />
            </div>
          </div>

          <button
            style={{ ...btnStyle(c.accent, "#fff"), width: "100%", opacity: syncing ? 0.6 : 1 }}
            onClick={saveSetup}
            disabled={syncing}
          >
            {syncing ? "接続テスト中..." : "接続してはじめる"}
          </button>
        </div>

        {toast && <div style={toastStyle(c.accent)}>{toast}</div>}
      </div>
    );
  }

  // ── Main screen ──
  return (
    <div style={{ maxWidth: 600, margin: "0 auto", fontFamily: "var(--font-sans, system-ui)" }}>
      {/* Header */}
      <div style={{ padding: "28px 0 20px", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 500, margin: 0, color: c.text, display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: c.accent, display: "inline-block", boxShadow: `0 0 0 3px ${c.accent}33` }} />
            HP監視システム
          </h1>
          <p style={{ fontSize: 13, color: c.sub, marginTop: 4 }}>
            電話番号が掲載されたら Telegram に通知
          </p>
        </div>
        <button
          style={{ ...btnStyle("transparent", c.sub), fontSize: 13, padding: "6px 12px", border: `1px solid ${c.border}` }}
          onClick={() => setShowSetup(true)}
        >
          設定
        </button>
      </div>

      {/* URL Input */}
      <div style={{
        background: c.card,
        border: `1px solid ${c.border}`,
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
      }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: c.sub, marginBottom: 8 }}>
          監視URLを追加
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            style={{ ...inputStyle(c), flex: 1 }}
            placeholder="https://example.com/"
            value={newUrl}
            onChange={(e) => { setNewUrl(e.target.value); setUrlError(""); }}
            onKeyDown={(e) => e.key === "Enter" && !syncing && addUrl()}
          />
          <button
            style={{ ...btnStyle(c.accent, "#fff"), opacity: syncing ? 0.6 : 1 }}
            onClick={addUrl}
            disabled={syncing}
          >
            {syncing ? "..." : "追加"}
          </button>
        </div>
        {urlError && <div style={{ fontSize: 13, color: c.danger, marginTop: 6 }}>{urlError}</div>}
      </div>

      {/* URL List */}
      <div style={{
        background: c.card,
        border: `1px solid ${c.border}`,
        borderRadius: 12,
        marginBottom: 16,
      }}>
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${c.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: c.sub }}>
            監視中（{urls.length}件）
          </span>
          <span style={{ fontSize: 12, color: c.muted }}>
            {interval}分ごとに巡回
          </span>
        </div>

        {urls.length === 0 ? (
          <div style={{ padding: "40px 16px", textAlign: "center", color: c.muted, fontSize: 14 }}>
            URLを追加してください
          </div>
        ) : (
          urls.map((url, i) => (
            <div
              key={url}
              style={{
                padding: "12px 16px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                borderBottom: i < urls.length - 1 ? `1px solid ${c.border}` : "none",
                gap: 8,
              }}
            >
              <span style={{ fontSize: 14, color: c.text, wordBreak: "break-all", flex: 1 }}>
                {url}
              </span>
              <button
                style={{
                  ...btnStyle("transparent", c.danger),
                  fontSize: 12,
                  padding: "4px 10px",
                  flexShrink: 0,
                  opacity: syncing ? 0.4 : 1,
                }}
                onClick={() => removeUrl(url)}
                disabled={syncing}
              >
                削除
              </button>
            </div>
          ))
        )}
      </div>

      {/* Log */}
      {log.length > 0 && (
        <div style={{
          background: c.card,
          border: `1px solid ${c.border}`,
          borderRadius: 12,
          marginBottom: 20,
        }}>
          <div style={{ padding: "14px 16px", borderBottom: `1px solid ${c.border}` }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: c.sub }}>操作ログ</span>
          </div>
          {log.slice(0, 8).map((entry, i) => (
            <div
              key={i}
              style={{
                padding: "8px 16px",
                fontSize: 13,
                color: c.sub,
                borderBottom: i < Math.min(log.length, 8) - 1 ? `1px solid ${c.border}` : "none",
              }}
            >
              <span style={{ color: c.muted, fontSize: 12, marginRight: 8 }}>{entry.time}</span>
              {entry.msg}
            </div>
          ))}
        </div>
      )}

      {/* Flow diagram */}
      <div style={{
        padding: "16px",
        borderRadius: 12,
        border: `1px solid ${c.border}`,
        background: c.card,
        marginBottom: 20,
      }}>
        <div style={{ fontSize: 13, color: c.sub, textAlign: "center", lineHeight: 2.2 }}>
          HP発見 → <b style={{ fontWeight: 500 }}>ここでURL入力</b> → 自動巡回（{interval}分ごと）→ 電話番号検知 → Telegram通知
        </div>
      </div>

      {toast && <div style={toastStyle(c.accent)}>{toast}</div>}
    </div>
  );
}

function inputStyle(c) {
  return {
    padding: "10px 14px",
    fontSize: 14,
    border: `1px solid ${c.border}`,
    borderRadius: 8,
    background: "var(--surface-2, #fff)",
    color: "var(--text-primary, #1a1a1a)",
    outline: "none",
    boxSizing: "border-box",
  };
}

function btnStyle(bg, color) {
  return {
    padding: "10px 18px",
    fontSize: 14,
    fontWeight: 500,
    border: "none",
    borderRadius: 8,
    cursor: "pointer",
    background: bg,
    color: color,
    transition: "opacity 0.15s",
  };
}

function toastStyle(bg) {
  return {
    position: "fixed",
    bottom: 24,
    left: "50%",
    transform: "translateX(-50%)",
    background: bg,
    color: "#fff",
    padding: "10px 20px",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 500,
    zIndex: 999,
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
  };
}
