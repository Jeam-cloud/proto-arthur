import { create } from "zustand";

let nextId = 1;

export const useToasts = create((set) => ({
  toasts: [],
  push(message, kind = "info") {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 5000);
  },
}));
