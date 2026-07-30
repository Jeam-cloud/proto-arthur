// Staged attachments, above the composer input.
//
// Each chip carries what the app actually KNOWS about the file, because the
// useful question is not "did it upload" but "can Arthur read it". Three states
// are visibly different:
//
//   read      — extracted text, character count shown. It will be in the prompt.
//   image     — no text; needs a model that can see. Warned about separately.
//   unreadable— attached but unparseable (a scanned PDF, a .doc, a binary).
//               Shown as a problem rather than silently doing nothing, because
//               an attachment that looks fine and contributes nothing is the
//               worst of the three outcomes.
import React from "react";
import { AlertTriangle, FileText, Image, Loader2, X } from "lucide-react";
import { useAttachments } from "../../stores/attachments";
import { useBackend } from "../../stores/backend";

export default function AttachmentTray() {
  const items = useAttachments((s) => s.items);
  const uploading = useAttachments((s) => s.uploading);
  const remove = useAttachments((s) => s.remove);
  const caps = useAttachments((s) => s.caps);
  // Reported by /system/status once, not rediscovered per file.
  const missing = useBackend((s) => s.status?.missing_parsers) || [];

  if (!items.length && !uploading && !missing.length) return null;

  // Only warn when Ollama actually told us the model cannot see. `known:false`
  // means we asked and got nothing, and guessing would produce a warning the
  // user learns to ignore.
  const blind = !caps.vision && caps.known ? items.filter((a) => a.kind === "image") : [];

  return (
    <div className="attach-tray">
      {/* An incomplete INSTALL, stated once and up front. Before this it
          surfaced as an identical "Couldn't read this file: No module named
          'pypdf'" on every chip -- which reads as a problem with the files, or
          with the model, and is neither. PDF parsing never touches the model. */}
      {missing.length > 0 && (
        <div className="attach-warning">
          <AlertTriangle size={14} strokeWidth={1.9} />
          <span>
            Some file types can&apos;t be read yet — {missing.join(" and ")} {missing.length === 1 ? "is" : "are"} not
            installed. Run <code>pip install {missing.join(" ")}</code> in the{" "}
            <code>python</code> folder and restart Arthur.
          </span>
        </div>
      )}

      {blind.length > 0 && (
        <div className="attach-warning">
          <AlertTriangle size={14} strokeWidth={1.9} />
          <span>
            This model can&apos;t see images, so {blind.length === 1
              ? <><code>{blind[0].filename}</code> will be ignored</>
              : `${blind.length} attached images will be ignored`}.
            {" "}Switch to a model with vision, or describe what&apos;s in it.
          </span>
        </div>
      )}

      <div className="attach-chips">
        {items.map((a) => (
          <div key={a.id} className={`attach-chip${a.error ? " bad" : ""}`}>
            {a.kind === "image"
              ? <Image size={13} strokeWidth={1.8} />
              : <FileText size={13} strokeWidth={1.8} />}
            <span className="attach-name" title={a.source_path || a.filename}>{a.filename}</span>
            <span className="attach-meta">{describe(a)}</span>
            <button
              className="attach-remove"
              aria-label={`Remove ${a.filename}`}
              onClick={() => remove(a.id)}
            >
              <X size={12} strokeWidth={2.2} />
            </button>
          </div>
        ))}
        {uploading && (
          <div className="attach-chip">
            <Loader2 size={13} className="spin" />
            <span className="attach-name">Reading…</span>
          </div>
        )}
      </div>
    </div>
  );
}

// What the chip says about a file, in the order the user cares about: a problem
// first, then how much was read, then just its size.
function describe(a) {
  if (a.error) return a.error;
  if (a.kind === "image") return kb(a.size_bytes);
  if (a.chars) {
    const words = Math.round(a.chars / 5.5);
    return a.truncated
      ? `${words.toLocaleString()} words (truncated)`
      : `${words.toLocaleString()} words`;
  }
  return kb(a.size_bytes);
}

function kb(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
