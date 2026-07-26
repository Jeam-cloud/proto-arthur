// Plan review: the sub-questions, before a single request is made.
//
// This screen is the steering wheel. A small local model plans badly over long
// horizons, and the honest fix is not a bigger prompt, it is showing the plan to
// the person who actually knows what they meant and letting them fix it in ten
// seconds. It reads as a courtesy; it is really the thing that makes small
// models viable for multi-step work.
import React from "react";
import { GripVertical, X, Plus } from "lucide-react";
import { useResearch } from "../../stores/research";

export default function ResearchPlan() {
  const { question, subs, editSub, delSub, addSub, backHome, run } = useResearch();

  return (
    <div className="research-scroll">
      <div className="research-col wide">
        <div className="micro-label">Proposed approach</div>
        <div className="research-question-echo">{question}</div>
        <p className="research-lede">
          Arthur will work through these sub-questions in parallel. Edit, reorder or remove any of
          them before the run starts.
        </p>

        {subs.map((q, i) => (
          <div key={q.id} className="research-sub">
            <GripVertical size={15} strokeWidth={1.8} className="research-sub-grip" />
            <span className="research-sub-n">{i + 1}</span>
            <input
              type="text"
              value={q.text}
              onChange={(e) => editSub(q.id, e.target.value)}
              placeholder="Describe what to find out"
            />
            <button className="research-sub-del" onClick={() => delSub(q.id)} title="Remove">
              <X size={14} strokeWidth={1.9} />
            </button>
          </div>
        ))}

        <button className="research-add-sub" onClick={addSub}>
          <Plus size={14} strokeWidth={2} /> Add sub-question
        </button>

        <div className="research-actions end">
          <span className="research-budget">
            {subs.length} sub-questions · ~{subs.length * 2} sources · about{" "}
            {Math.max(1, Math.round(subs.length * 0.6))} min
          </span>
          <button className="btn" onClick={backHome}>Back</button>
          <button className="btn primary" disabled={!subs.length} onClick={run}>Run investigation</button>
        </div>
      </div>
    </div>
  );
}
