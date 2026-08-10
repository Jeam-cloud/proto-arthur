// Security status + event log (tracker p4t7). Trust through visibility:
// which scanner is live, whether the sandbox is up, and a feed of every
// block/flag/approval decision the gateway has made.
import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useBackend } from "../../stores/backend";
import { useSettings } from "../../stores/settings";

const SEVERITY_PILL = { info: "ok", warning: "warn", blocked: "off" };

export default function SecurityTab() {
  const [events, setEvents] = useState([]);
  const { status } = useBackend();
  const { values, update } = useSettings();

  const load = () => api.get("/security/events?limit=100").then(setEvents).catch(() => {});
  useEffect(() => { load(); }, []);

  return (
    <>
      <h2>Security</h2>
      <div className="section-sub">What's protecting you right now, and what it has caught.</div>

      <div className="card">
        <div className="card-row">
          <div className="grow">
            <div className="card-title">Message scanner</div>
            <div className="card-sub">
              Checks messages you send for injection patterns. External content (web pages,
              emails) is always scanned and marked untrusted regardless of this setting, and
              risky actions always ask you first; those layers don't cause false blocks.
            </div>
          </div>
          <span className={`pill ${status?.scanner_backend === "heuristic" ? "warn" : "ok"}`}>
            {status?.scanner_backend || "…"}
          </span>
        </div>
        {values && (
          <div className="segmented" style={{ marginTop: 14 }}>
            {[
              ["standard", "Standard", "blocks suspicious messages"],
              ["relaxed", "Relaxed", "warns in the log, never blocks"],
              ["off", "Off", "no scanning of your messages"],
            ].map(([id, label, sub]) => (
              <button
                key={id}
                className={(values.scanner_mode || "standard") === id ? "active" : ""}
                title={sub}
                onClick={() => update({ scanner_mode: id })}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {values?.scanner_mode === "off" && (
          <div className="hint" style={{ color: "var(--orange)", marginTop: 8 }}>
            Off means pasted text from untrusted sources goes to the model unchecked.
            Relaxed gives you no false blocks while still logging what would have been caught.
          </div>
        )}
      </div>

      <div className="card card-row">
        <div className="grow">
          <div className="card-title">Tool sandbox (Docker)</div>
          <div className="card-sub">Research, finance and code execution run in locked-down containers.</div>
        </div>
        <span className={`pill ${status?.docker_up ? "ok" : "off"}`}>
          {status?.docker_up ? "active" : "off"}
        </span>
      </div>

      {values && (
        <div className="card card-row">
          <div className="grow">
            <div className="card-title">Allow research without the sandbox</div>
            <div className="card-sub" style={{ color: "var(--orange)" }}>
              Off is safer. On lets web fetching run in the main process when Docker is unavailable.
            </div>
          </div>
          <label className="switch">
            <input
              type="checkbox"
              checked={!!values.allow_unsandboxed_network_tools}
              onChange={(e) => update({ allow_unsandboxed_network_tools: e.target.checked })}
            />
            <span className="track" /><span className="thumb" />
          </label>
        </div>
      )}

      {values && (
        <div className="card card-row">
          <div className="grow">
            <div className="card-title">Review code changes before saving</div>
            {/* Described as a trade, not as "safer". Off is the default because
                the protection did not disappear when the gate did -- it moved
                to Undo -- and framing this as the safe option would push people
                toward clicking through a review they have stopped reading. */}
            <div className="card-sub">
              Off: Arthur writes files as it works and you can undo. On: edits wait in a
              diff until you apply them.
            </div>
          </div>
          <label className="switch">
            <input
              type="checkbox"
              checked={!!values.code_review_before_apply}
              onChange={(e) => update({ code_review_before_apply: e.target.checked })}
            />
            <span className="track" /><span className="thumb" />
          </label>
        </div>
      )}

      <div className="field-row" style={{ margin: "18px 0 8px" }}>
        <h2 style={{ fontSize: 14 }} className="grow">Event log</h2>
        <button className="btn" style={{ padding: "4px 12px", fontSize: 12 }} onClick={load}>Refresh</button>
        <button className="btn danger" style={{ padding: "4px 12px", fontSize: 12 }}
          onClick={async () => { await api.del("/security/events"); load(); }}>
          Clear
        </button>
      </div>

      {events.length === 0 && <div className="hint">No security events yet, that's a good sign.</div>}
      {events.map((e) => (
        <div key={e.id} className="event-row">
          <span className="event-time">{new Date(e.ts * 1000).toLocaleString()}</span>
          <span className={`pill ${SEVERITY_PILL[e.severity] || "ok"}`}>{e.severity}</span>
          <span style={{ whiteSpace: "nowrap" }}>{e.kind}</span>
          <span className="event-detail">{JSON.stringify(e.detail)}</span>
        </div>
      ))}
    </>
  );
}
