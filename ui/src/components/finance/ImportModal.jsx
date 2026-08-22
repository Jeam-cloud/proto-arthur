// Import holdings from a CSV.
//
// TWO STEPS, ALWAYS. Pick a file, see exactly what Arthur read out of it, then
// decide. Import is the one operation in Finance mode that can destroy data
// which exists nowhere else — it was typed by hand and cannot be re-fetched —
// so it never commits on the same click that selects the file.
//
// APPEND IS THE DEFAULT and replace is a deliberate second choice behind a
// confirmation. Defaulting the other way would let one misread click delete a
// portfolio with no backup.
import React, { useRef, useState } from "react";
import { AlertCircle, FileUp, Upload } from "lucide-react";
import { useFinance } from "../../stores/finance";
import { useConfirm } from "../../stores/confirm";

export default function ImportModal({ onClose }) {
  const { previewImport, applyImport, holdings } = useFinance();
  const ask = useConfirm((s) => s.ask);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const take = async (f) => {
    if (!f) return;
    setFile(f);
    setPreview(null);
    setBusy(true);
    setPreview(await previewImport(f));
    setBusy(false);
  };

  const commit = async (replace) => {
    setBusy(true);
    const res = await applyImport(file, replace);
    setBusy(false);
    if (res) onClose();
  };

  const run = (replace) => {
    if (!replace) return commit(false);
    // The only irreversible path in this flow gets the app's confirm dialog,
    // naming exactly what disappears.
    ask({
      title: `Replace all ${holdings.length} holdings?`,
      body: `Everything currently in your portfolio is deleted and replaced with the `
        + `${preview.count} holding${preview.count === 1 ? "" : "s"} in this file. `
        + "Arthur can't recover what's removed — it was only ever stored here.",
      confirmLabel: "Replace everything",
      onConfirm: () => commit(true),
    });
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal hold-modal" onClick={(e) => e.stopPropagation()}>
        <div className="hm-head">
          <h3>Import holdings</h3>
          <p>
            A CSV with <code>symbol</code>, <code>quantity</code> and <code>cost_basis</code>{" "}
            columns — including one Arthur exported. Nothing is saved until you choose below.
          </p>
        </div>

        <div className="hm-body">
          {/* Drop target and file button are the same control: dragging a file
              onto a dialog is the first thing people try. */}
          <div
            className={`im-drop${dragging ? " over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault(); setDragging(false);
              take(e.dataTransfer.files?.[0]);
            }}
            onClick={() => inputRef.current?.click()}
          >
            <FileUp size={18} strokeWidth={1.7} />
            <div>
              <strong>{file ? file.name : "Choose a CSV, or drop one here"}</strong>
              {file && <span> · {(file.size / 1024).toFixed(1)} KB</span>}
            </div>
            <input ref={inputRef} type="file" accept=".csv,text/csv" hidden
                   onChange={(e) => take(e.target.files?.[0])} />
          </div>

          {busy && !preview && <div className="im-status"><span className="spinner" /> Reading…</div>}

          {preview && (
            <>
              <div className="im-summary">
                <span className="im-count">{preview.count}</span>
                <span>
                  holding{preview.count === 1 ? "" : "s"} readable
                  {preview.errors?.length
                    ? ` · ${preview.errors.length} row${preview.errors.length === 1 ? "" : "s"} skipped`
                    : ""}
                </span>
              </div>

              {/* The rows themselves, not just a count. "12 holdings found" is
                  a promise; showing the tickers is evidence. */}
              {preview.rows?.length > 0 && (
                <div className="im-rows">
                  {preview.rows.slice(0, 8).map((r, i) => (
                    <div className="im-row" key={i}>
                      <span className="im-sym">{r.symbol}</span>
                      <span className="im-qty">{r.quantity}</span>
                      <span className="im-cost">
                        {r.cost_basis}{r.cost_currency ? ` ${r.cost_currency}` : ""}
                      </span>
                    </div>
                  ))}
                  {preview.rows.length > 8 && (
                    <div className="im-more">+ {preview.rows.length - 8} more</div>
                  )}
                </div>
              )}

              {/* Skipped rows are named with their line number, so the file can
                  be fixed rather than guessed at. */}
              {preview.errors?.length > 0 && (
                <div className="im-errors">
                  <div className="im-errors-head">
                    <AlertCircle size={13} strokeWidth={1.9} /> Skipped
                  </div>
                  {preview.errors.slice(0, 5).map((e, i) => (
                    <div className="im-err" key={i}>
                      <span className="im-line">line {e.line}</span>
                      {e.symbol ? <span className="im-sym">{e.symbol}</span> : null}
                      <span>{e.reason}</span>
                    </div>
                  ))}
                  {preview.errors.length > 5 && (
                    <div className="im-more">+ {preview.errors.length - 5} more</div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="hm-foot">
          <button className="btn primary" disabled={!preview?.count || busy}
                  onClick={() => run(false)}>
            {busy ? "Importing…" : `Add ${preview?.count || ""} to my portfolio`.trim()}
          </button>
          <button className="btn" onClick={onClose}>Cancel</button>
          {/* Only offered when there is actually something to replace. */}
          {preview?.count > 0 && holdings.length > 0 && (
            <>
              <span className="hm-spacer" />
              <button className="btn hm-remove" disabled={busy} onClick={() => run(true)}>
                <Upload size={13} strokeWidth={1.8} /> Replace all
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
