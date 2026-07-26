// Onboarding wizard (tracker p4t1): welcome -> Ollama -> model -> Docker -> done.
//
// Design rules: each step DETECTS rather than assumes (re-check buttons call
// /system/status), the model step recommends from real hardware via
// /system/hardware, the Docker step is skippable (chat works without it,
// only sandboxed tools need it), and nothing here dead-ends: every failure
// state shows what to do next.
import React, { useEffect, useState } from "react";
import { Check, Download, ExternalLink, Loader2 } from "lucide-react";
import { api, apiUrl, authHeaders } from "../api/client";
import { useBackend } from "../stores/backend";
import { useSettings } from "../stores/settings";
import { LogoMark } from "./ModeRail";

const STEPS = ["welcome", "ollama", "model", "docker", "done"];

// The mockup labels each step "Step N of 4" above the heading instead of
// numbering the heading itself ("1 · Ollama"). Small thing, but it means the
// heading is the same size and shape on every step, so the card doesn't
// visually jump as you click through.
function StepLabel({ n, note }) {
  return (
    <div style={{ fontSize: 11.5, color: "var(--tmut)", fontWeight: 500, marginBottom: 12 }}>
      Step {n} of 4{note ? ` · ${note}` : ""}
    </div>
  );
}

export default function Onboarding() {
  const [step, setStep] = useState("welcome");
  const { status, refreshStatus } = useBackend();
  const idx = STEPS.indexOf(step);

  return (
    <div className="onboarding">
      <div className="onboarding-card">
        <div className="step-dots">
          {STEPS.map((s, i) => (
            <div key={s} className={`step-dot ${i < idx ? "done" : i === idx ? "current" : ""}`} />
          ))}
        </div>
        {step === "welcome" && <Welcome onNext={() => setStep("ollama")} />}
        {step === "ollama" && <OllamaStep status={status} refresh={refreshStatus} onNext={() => setStep("model")} />}
        {step === "model" && <ModelStep status={status} refresh={refreshStatus} onNext={() => setStep("docker")} />}
        {step === "docker" && <DockerStep status={status} refresh={refreshStatus} onNext={() => setStep("done")} />}
        {step === "done" && <Done />}
      </div>
    </div>
  );
}

function Welcome({ onNext }) {
  return (
    <>
      <h1><span className="logo"><LogoMark size={21} /></span> Welcome to Arthur</h1>
      <div style={{ fontSize: 12.5, color: "var(--tmut)", margin: "-6px 0 18px 48px" }}>
        Local-first · No account · No subscription
      </div>
      <p>
        Arthur runs entirely on this computer, no account, no subscription, and nothing
        leaves your machine unless you switch on a feature that needs the internet.
        Let's check the two things Arthur builds on.
      </p>
      <div className="modal-actions">
        <button className="btn primary" onClick={onNext}>Get started</button>
      </div>
    </>
  );
}

function OllamaStep({ status, refresh, onNext }) {
  const [checking, setChecking] = useState(false);
  const up = status?.ollama_up;

  const recheck = async () => {
    setChecking(true);
    await refresh();
    setChecking(false);
  };

  return (
    <>
      <StepLabel n={1} />
      <h1>Ollama {up && <Check color="var(--green)" size={18} />}</h1>
      <p>
        Ollama runs the AI models locally. {up
          ? "Found it, you're set."
          : "Arthur can't find it. Install it from ollama.com (one click, free), then come back."}
      </p>
      <div className="modal-actions">
        {!up && (
          <>
            <button className="btn" onClick={() => window.arthur?.openExternal("https://ollama.com/download")}>
              <ExternalLink size={13} /> Get Ollama
            </button>
            <button className="btn" onClick={recheck} disabled={checking}>
              {checking ? <Loader2 size={13} className="spin" /> : "Check again"}
            </button>
          </>
        )}
        <button className="btn primary" onClick={onNext} disabled={!up}>Continue</button>
      </div>
    </>
  );
}

function ModelStep({ status, refresh, onNext }) {
  const [hw, setHw] = useState(null);
  const [pulling, setPulling] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const update = useSettings((s) => s.update);

  useEffect(() => { api.get("/system/hardware").then(setHw).catch(() => {}); }, []);

  const rec = hw?.recommendation;
  const models = status?.models || [];
  const chatInstalled = rec && models.some((m) => m.name.startsWith(rec.chat_model.split(":")[0]));
  const embedInstalled = models.some((m) => m.name.startsWith("nomic-embed-text"));

  async function pull(model) {
    // fetch-SSE progress: same framing the chat stream uses
    setPulling(true); setError(null);
    try {
      const res = await fetch(apiUrl("/models/pull"), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ model }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        for (const line of buf.split("\n")) {
          if (line.startsWith("data:")) {
            try {
              const d = JSON.parse(line.slice(5));
              if (d.total) setProgress({ done: d.completed, total: d.total, status: d.status });
              if (d.code) setError(d.message);
            } catch { /* partial line */ }
          }
        }
        buf = buf.slice(buf.lastIndexOf("\n") + 1);
      }
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setPulling(false); setProgress(null);
    }
  }

  async function installBoth() {
    if (!embedInstalled) await pull("nomic-embed-text");
    if (!chatInstalled) await pull(rec.chat_model);
    await update({ default_model: rec.chat_model });
  }

  const pct = progress && progress.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <>
      <StepLabel n={2} />
      <h1>Pick your model {chatInstalled && embedInstalled && <Check color="var(--green)" size={18} />}</h1>
      {hw ? (
        <p>
          This machine: {hw.ram_gb}GB RAM{hw.gpu ? `, ${hw.gpu.name} (${hw.gpu.vram_gb}GB)` : ", no NVIDIA GPU"}.
          Recommended: <strong>{rec.chat_model}</strong>, {rec.note}.
          Plus <strong>nomic-embed-text</strong> for memory.
        </p>
      ) : <p>Reading your hardware…</p>}

      {pulling && (
        <>
          <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
          <p style={{ fontSize: 12 }}>{progress?.status || "starting download"} {pct > 0 && `· ${pct}%`}</p>
        </>
      )}
      {error && <p style={{ color: "var(--red)", fontSize: 12.5 }}>{error}</p>}

      <div className="modal-actions">
        {!(chatInstalled && embedInstalled) && (
          <button className="btn" onClick={installBoth} disabled={pulling || !rec}>
            <Download size={13} /> {pulling ? "Downloading…" : "Download models"}
          </button>
        )}
        <button className="btn primary" onClick={onNext} disabled={!(chatInstalled && embedInstalled)}>
          Continue
        </button>
      </div>
    </>
  );
}

function DockerStep({ status, refresh, onNext }) {
  const up = status?.docker_up;
  return (
    <>
      <StepLabel n={3} note="optional" />
      <h1>Docker {up && <Check color="var(--green)" size={18} />}</h1>
      <p>
        Docker sandboxes Arthur's risky tools (web research, code execution, finance)
        so they run isolated from your real system. {up
          ? "It's running. All tools available."
          : "Without it those tools stay disabled; chat, memory and computer control still work. You can install Docker Desktop anytime."}
      </p>
      <div className="modal-actions">
        {!up && (
          <>
            <button className="btn" onClick={() => window.arthur?.openExternal("https://www.docker.com/products/docker-desktop/")}>
              <ExternalLink size={13} /> Get Docker Desktop
            </button>
            <button className="btn" onClick={refresh}>Check again</button>
          </>
        )}
        <button className="btn primary" onClick={onNext}>{up ? "Continue" : "Skip for now"}</button>
      </div>
    </>
  );
}

function Done() {
  const refresh = useBackend((s) => s.refreshStatus);
  const finish = async () => {
    await api.post("/system/onboarded");
    await refresh(); // status.onboarded flips -> App renders the main layout
  };
  return (
    <>
      <StepLabel n={4} />
      <h1>You're set <Check color="var(--green)" size={18} /></h1>
      <p>
        Tip: press <strong>Ctrl+Shift+A</strong> anywhere to summon the quick widget.
        Anything Arthur remembers about you lives in Settings, Memory tab: visible, editable, deletable.
      </p>
      <div className="modal-actions">
        <button className="btn primary" onClick={finish}>Open Arthur</button>
      </div>
    </>
  );
}
