// System status store: is Ollama up, is Docker up, which models exist.
// Polled every 12s — this is what drives banners and degraded-feature states
// (tracker p4t2: "Ollama down, no internet, Docker off").
import { create } from "zustand";
import { api } from "../api/client";

export const useBackend = create((set, get) => ({
  phase: "starting", // starting | ready | failed
  status: null,      // /system/status payload
  pollTimer: null,

  setPhase: (phase) => set({ phase }),

  async refreshStatus() {
    try {
      const status = await api.get("/system/status");
      set({ status, phase: "ready" });
    } catch (e) {
      if (e.code === "backend_unreachable") set({ phase: "failed" });
    }
  },

  startPolling() {
    if (get().pollTimer) return;
    get().refreshStatus();
    const timer = setInterval(() => get().refreshStatus(), 12_000);
    set({ pollTimer: timer });
  },
}));
