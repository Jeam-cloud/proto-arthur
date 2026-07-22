import { create } from "zustand";
import { api } from "../api/client";
import { useToasts } from "./toasts";

export const useSettings = create((set, get) => ({
  values: null, // {default_model, workspace_root, allow_unsandboxed_network_tools, memory_enabled, font_scale}

  async load() {
    const values = await api.get("/settings");
    set({ values });
    document.documentElement.style.setProperty("--font-scale", String(values.font_scale || 1));
  },

  async update(patch) {
    const prev = get().values;
    set({ values: { ...prev, ...patch } }); // optimistic
    try {
      await api.patch("/settings", patch);
      if (patch.font_scale) {
        document.documentElement.style.setProperty("--font-scale", String(patch.font_scale));
      }
    } catch (e) {
      set({ values: prev }); // roll back on failure
      useToasts.getState().push(e.message, "error");
    }
  },
}));
