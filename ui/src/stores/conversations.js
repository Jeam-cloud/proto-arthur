import { create } from "zustand";
import { api } from "../api/client";
import { useToasts } from "./toasts";

export const useConversations = create((set, get) => ({
  list: [],
  activeId: null,
  loaded: false,

  async load() {
    const list = await api.get("/conversations");
    set({ list, loaded: true });
    if (!get().activeId && list.length) set({ activeId: list[0].id });
  },

  async createNew() {
    const conv = await api.post("/conversations");
    set((s) => ({ list: [{ ...conv, message_count: 0 }, ...s.list], activeId: conv.id }));
    return conv.id;
  },

  select: (id) => set({ activeId: id }),

  setTitle(id, title) {
    set((s) => ({ list: s.list.map((c) => (c.id === id ? { ...c, title } : c)) }));
  },

  async remove(id) {
    try {
      await api.del(`/conversations/${id}`);
    } catch (e) {
      useToasts.getState().push(e.message, "error");
      return;
    }
    set((s) => {
      const list = s.list.filter((c) => c.id !== id);
      return { list, activeId: s.activeId === id ? (list[0]?.id ?? null) : s.activeId };
    });
  },
}));
