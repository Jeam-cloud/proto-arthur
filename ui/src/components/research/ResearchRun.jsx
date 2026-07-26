// The run screen: one lane per sub-question, evidence streaming in beside it.
//
// The lanes are the honesty feature. A single spinner would let the app claim
// progress it has not made; six named lanes with real states mean "thin" and
// "blocked" are visible while they happen rather than being smoothed over in
// the final report. It is also the only way to make a second pass legible --
// a lane that goes thin and then goes back to searching is showing its work.
import React from "react";
import {
  Circle, Search, FileText, CircleCheck, CircleAlert, CircleX, Square, RotateCw,
} from "lucide-react";
import { useResearch } from "../../stores/research";
import EvidencePanel from "./EvidencePanel";

const LANE = {
  queued:    { label: "Queued",    Icon: Circle,      cls: "muted",  detail: () => "not started" },
  searching: { label: "Searching", Icon: Search,      cls: "active", detail: (l) => (l.pass > 1 ? "searching again with a reworded query" : "querying providers") },
  reading:   { label: "Reading",   Icon: FileText,    cls: "active", detail: (l) => `reading ${l.read}/${l.of}` },
  done:      { label: "Done",      Icon: CircleCheck, cls: "good",   detail: (l) => `${l.srcs} source${l.srcs === 1 ? "" : "s"}` },
  thin:      { label: "Thin",      Icon: CircleAlert, cls: "warn",   detail: () => "too little found — second pass queued" },
  blocked:   { label: "Blocked",   Icon: CircleX,     cls: "bad",    detail: () => "paywalled or unreachable" },
};

export default function ResearchRun() {
  const lanes = useResearch((s) => s.lanes);
  const evidence = useResearch((s) => s.evidence);
  const elapsed = useResearch((s) => s.elapsed);
  const question = useResearch((s) => s.question);
  const gapNote = useResearch((s) => s.gapNote);
  const stop = useResearch((s) => s.stop);
  const recents = useResearch((s) => s.recentRows());
  const openRecent = useResearch((s) => s.openRecent);

  const settled = lanes.filter((l) => ["done", "thin", "blocked"].includes(l.state)).length;
  const pct = lanes.length ? Math.round((settled / lanes.length) * 100) : 0;
  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="research-run">
      <div className="research-runlist">
        <div className="micro-label">Runs</div>
        <div className="research-runrow active">
          <span className="research-dot running" />
          <div>
            <div className="research-runrow-title">{question || "Investigation"}</div>
            <div className="research-runrow-meta">running</div>
          </div>
        </div>
        {recents.slice(0, 6).map((r) => (
          <div key={r.id} className="research-runrow" onClick={() => openRecent(r.id)}>
            <span className={`research-dot ${r.status}`} />
            <div>
              <div className="research-runrow-title">{r.title}</div>
              <div className="research-runrow-meta">{r.meta}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="research-lanes-pane">
        <div className="research-progress">
          <div className="research-progress-row">
            <div className="research-progress-titles">
              <div className="research-progress-title">{question || "Investigation"}</div>
              <div className="research-progress-meta mono">
                {mmss} elapsed · {evidence.length} sources found · {pct}%
              </div>
            </div>
            <button className="btn small" onClick={stop}>
              <Square size={13} fill="currentColor" strokeWidth={0} /> Stop
            </button>
          </div>
          <div className="research-bar"><div className="research-bar-fill" style={{ width: `${pct}%` }} /></div>
          <div className="research-progress-note">Stopping keeps everything gathered so far.</div>
        </div>

        <div className="research-lanes">
          {gapNote && (
            <div className="research-gap">
              <RotateCw size={13} strokeWidth={2} />
              <span>{gapNote}</span>
            </div>
          )}

          {lanes.map((l) => {
            const meta = LANE[l.state] || LANE.queued;
            const { Icon } = meta;
            return (
              <div key={l.id} className={`research-lane${l.state === "queued" ? " queued" : ""}`}>
                <span className={`research-lane-icon ${meta.cls}${["searching", "reading"].includes(l.state) ? " pulsing" : ""}`}>
                  <Icon size={13} strokeWidth={2} />
                </span>
                <div className="research-lane-body">
                  <div className="research-lane-text">{l.text}</div>
                  <div className="research-lane-detail">
                    {meta.detail(l)}
                    {l.pass > 1 && l.state !== "searching" && <span className="research-pass">second pass</span>}
                  </div>
                </div>
                <span className={`research-lane-state ${meta.cls}`}>{meta.label}</span>
              </div>
            );
          })}
        </div>
      </div>

      <EvidencePanel variant="run" />
    </div>
  );
}
