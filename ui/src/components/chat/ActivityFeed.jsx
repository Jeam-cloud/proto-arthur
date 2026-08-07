// Live tool activity: what Arthur is doing on your machine, as it happens.
// Transparency is the product here.
//
// TWO SHAPES, because one run of tool calls is not like fifteen.
//
//   `list`  — the original. One row per call. Right for Research, Email,
//             Finance, where a turn means one or two actions.
//   `run`   — Code mode while streaming. An autonomous edit makes dozens of
//             calls over minutes, and one row each produces a wall of
//             near-identical "read_file" lines with no sense of progress.
//
// The run shape fixes that by RANKING rather than listing: consecutive reads
// and searches collapse into a single line ("Read 6 files"), while writes and
// edits — the only calls with consequences — each keep their own row and are
// weighted heavier. There is deliberately no task checklist; the ask was for an
// agent that just works, not one that narrates a plan.
import React, { useEffect, useState } from "react";
import {
  BookOpen, Check, CheckCircle2, FilePlus2, Loader2, Pencil, Search,
  ShieldAlert, Terminal, Trash2, XCircle,
} from "lucide-react";

// Calls with no consequence, safe to collapse. Everything else stands alone.
const LIGHT = {
  read_file: { verb: "Read", noun: "files", icon: BookOpen },
  list_files: { verb: "Listed", noun: "folders", icon: BookOpen },
  search_files: { verb: "Searched", noun: "times", icon: Search },
  find_files: { verb: "Looked for", noun: "patterns", icon: Search },
};
const HEAVY_ICON = {
  write_file: FilePlus2, edit_file: Pencil, delete_file: Trash2, run_python: Terminal,
};

export default function ActivityFeed({ items, variant = "list", startedAt, onStop, folder }) {
  if (!items || items.length === 0) return null;
  if (variant !== "run") {
    return (
      <div className="activity">
        {items.map((a) => (
          <div key={a.key} className={`activity-item ${a.flagged ? "flagged" : ""} ${a.ok === false ? "failed" : ""}`}>
            {a.running ? <Loader2 size={13} className="spin" />
              : a.flagged ? <ShieldAlert size={13} />
              : a.ok === false ? <XCircle size={13} />
              : <CheckCircle2 size={13} />}
            <span><strong>{a.name}</strong>{a.summary ? `: ${a.summary}` : ""}</span>
          </div>
        ))}
      </div>
    );
  }
  return <RunBlock items={items} startedAt={startedAt} onStop={onStop} folder={folder} />;
}

function RunBlock({ items, startedAt, onStop, folder }) {
  const rows = group(items);
  const edits = items.filter((a) => a.name in HEAVY_ICON && a.name !== "run_python" && a.ok !== false).length;
  const touched = new Set(
    items.filter((a) => a.summary && a.summary.includes(" ")).map((a) => a.summary),
  ).size;

  return (
    <div className="runblock">
      <div className="runblock-head">
        <span className="pulse" />
        <span className="runblock-title">Working{folder ? ` in ${folder}` : ""}</span>
        <span className="runblock-sum">
          {touched} {touched === 1 ? "step" : "steps"} · {edits} {edits === 1 ? "edit" : "edits"} staged
          <Elapsed since={startedAt} />
        </span>
      </div>

      <div className="runblock-rows">
        {rows.map((r) => (
          <div key={r.key} className={`runrow${r.heavy ? " heavy" : ""}`}>
            <r.Icon size={14} strokeWidth={1.8} />
            <span className="runrow-label">{r.label}</span>
            <span className="runrow-detail">{r.detail}</span>
            {r.running
              ? <span className="runspin" />
              : r.ok === false
                ? <XCircle size={12} className="bad" />
                : <Check size={12} className="good" />}
          </div>
        ))}
      </div>

      <div className="runblock-foot">
        {/* The reassurance sits HERE, mid-run, because that is when the user is
            actually anxious — watching files change is the moment they want to
            know none of it has landed. */}
        <span>Nothing has reached your folder. Every edit is staged for the review below.</span>
        {onStop && <button className="btn tiny" onClick={onStop}>Stop</button>}
      </div>
    </div>
  );
}

// Consecutive light calls of the same kind fold into one row; anything with a
// consequence keeps its own. Only CONSECUTIVE ones fold, so the order of the
// run stays readable — reads, an edit, more reads is three rows, not two.
function group(items) {
  const rows = [];
  for (const a of items) {
    const light = LIGHT[a.name];
    const prev = rows[rows.length - 1];
    if (light && prev && prev.kind === a.name && !prev.running) {
      prev.count += 1;
      prev.label = `${light.verb} ${prev.count} ${light.noun}`;
      prev.names.push(shortName(a.summary));
      prev.detail = prev.names.slice(0, 3).join(", ")
        + (prev.names.length > 3 ? `, +${prev.names.length - 3}` : "");
      prev.running = a.running;
      prev.ok = a.ok;
      continue;
    }
    rows.push({
      key: a.key, kind: a.name, count: 1, running: a.running, ok: a.ok,
      heavy: a.name in HEAVY_ICON,
      Icon: light ? light.icon : HEAVY_ICON[a.name] || CheckCircle2,
      label: a.summary || a.name,
      detail: a.detail || "",
      names: [shortName(a.summary)],
    });
  }
  return rows;
}

function shortName(summary) {
  if (!summary) return "";
  const last = summary.split(" ").pop();
  return last.includes("/") ? last.split("/").pop() : last;
}

// Elapsed time, ticking. A multi-minute run with no clock feels stuck; the
// number is the cheapest possible proof that something is still happening.
function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!since) return undefined;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [since]);
  if (!since) return null;
  const s = Math.floor((Date.now() - since) / 1000);
  return <> · {s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`}</>;
}
