// Mirrors core/model_kind.py. Change one, change the other.
//
// WHY it is duplicated rather than fetched: this decides whether the UI prints
// "Nothing leaves this computer", and that label has to be right on the first
// render, before any request resolves. A privacy claim that appears and then
// corrects itself has already been read.

// Ollama's naming convention for remotely-hosted models: a bare `:cloud` tag
// (`kimi-k3:cloud`) or a size-qualified suffix (`gemma4:31b-cloud`).
const CLOUD_RE = /(?::cloud$|-cloud(?::|$))/i;

export function isCloudModel(name) {
  return CLOUD_RE.test(String(name || "").trim());
}

/** Where the work happens, for any UI that promises locality. */
export function localityNote(name) {
  return isCloudModel(name)
    ? "This model runs on Ollama's servers, so your question and any attached files leave this computer."
    : "Nothing leaves this computer.";
}
