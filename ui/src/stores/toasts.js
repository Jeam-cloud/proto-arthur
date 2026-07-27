import { create } from "zustand";

let nextId = 1;

export const useToasts = create((set) => ({
  toasts: [],
  // `ms` is optional and defaults to the old fixed 5s, so every existing
  // caller is unchanged. It exists because a toast that explains a fallback
  // ("the backend was unreachable, here is what I did instead") is two
  // sentences of instruction, and five seconds is not long enough to read
  // instructions.
  push(message, kind = "info", ms = 5000) {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), ms);
  },
}));
