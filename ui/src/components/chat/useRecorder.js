// Push-to-talk recording hook (tracker p3t14/15).
//
// MediaRecorder produces webm/opus natively; the backend's faster-whisper
// decodes it directly. Permission handling is explicit because "mic denied"
// is a first-class product state (tracker p4t2), not an exception to bury:
// the composer shows why the mic button is disabled and how to fix it.
import { useRef, useState } from "react";
import { api } from "../../api/client";

export function useRecorder({ onText, onError }) {
  const [state, setState] = useState("idle"); // idle | recording | transcribing | denied
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);

  async function start() {
    if (state === "recording") return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setState("denied");
      onError(e.name === "NotAllowedError"
        ? "Microphone access was denied. Allow it in your system's privacy settings, then try again."
        : "No microphone found.");
      return;
    }
    chunksRef.current = [];
    const rec = new MediaRecorder(stream, { mimeType: "audio/webm" });
    rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); // release the mic indicator
      setState("transcribing");
      try {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const form = new FormData();
        form.append("audio", blob, "speech.webm");
        const { text } = await api.postForm("/voice/transcribe", form);
        if (text) onText(text);
        else onError("Didn't catch that — try speaking a bit longer.");
      } catch (e) {
        onError(e.message);
      } finally {
        setState("idle");
      }
    };
    recorderRef.current = rec;
    rec.start();
    setState("recording");
  }

  function stop() {
    if (recorderRef.current && recorderRef.current.state === "recording") {
      recorderRef.current.stop();
    }
  }

  return { state, start, stop };
}
