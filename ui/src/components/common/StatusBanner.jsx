// One banner, worst problem first. Ollama down blocks everything -> error
// style with a retry; Docker off only degrades sandboxed tools -> dismissible
// warning. The point (tracker p4t2): the user should never wonder why a
// feature silently isn't working.
import React, { useState } from "react";
import { useBackend } from "../../stores/backend";

export default function StatusBanner() {
  const { status, refreshStatus } = useBackend();
  const [dockerDismissed, setDockerDismissed] = useState(false);

  if (!status) return null;

  if (!status.ollama_up) {
    return (
      <div className="status-banner error">
        <span>
          Ollama isn't running, chat is paused. Start Ollama (or install it from ollama.com), then retry.
        </span>
        <button onClick={refreshStatus}>Retry</button>
      </div>
    );
  }

  if (!status.docker_up && !dockerDismissed) {
    return (
      <div className="status-banner">
        <span>
          Docker is off, research, finance and code-execution tools are disabled until it starts. Chat still works.
        </span>
        <button onClick={() => setDockerDismissed(true)}>Dismiss</button>
      </div>
    );
  }

  return null;
}
