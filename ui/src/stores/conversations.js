import { create } from "zustand";
import { api } from "../api/client";
// CIRCULAR BY DESIGN, and safe: chat.js imports this module too. Neither
// touches the other at module-evaluation time — both only call
// `.getState()` inside function bodies — so whichever module the bundler
// evaluates first finds a fully-formed export by the time anything runs.
// Keep it that way: a top-level `useChat.getState()` in either file would
// turn this into a real initialisation-order bug.
import { useChat } from "./chat";
import { useToasts } from "./toasts";

export const useConversations = create((set, get) => ({
  list: [],
  activeId: null,
  loaded: false,

  async load() {
    const list = await api.get("/conversations");
    set({ list, loaded: true });
    // Each conversation's pinned model rides along on the list, so the chat
    // store can be seeded in the same round trip rather than asking per chat.
    useChat.getState().hydrateModelOverrides(list);
    if (!get().activeId && list.length) set({ activeId: list[0].id });
  },

  // Mode and folder are decided HERE, at creation, and never change after.
  // That is what makes "this is a Code chat on this project" a durable fact
  // rather than a reading of whatever the rail currently points at.
  async createNew({ mode = "general", workspaceRoot = null } = {}) {
    const conv = await api.post("/conversations", { mode, workspace_root: workspaceRoot });
    set((s) => ({ list: [{ ...conv, message_count: 0 }, ...s.list], activeId: conv.id }));
    return conv.id;
  },

  select: (id) => set({ activeId: id }),

  setTitle(id, title) {
    set((s) => ({ list: s.list.map((c) => (c.id === id ? { ...c, title } : c)) }));
  },

  // Rename talks to the server FIRST (unlike setTitle, which is a local patch
  // the streaming title-suggestion event calls) so a failed rename doesn't
  // leave the sidebar showing a title the backend never saved.
  async rename(id, title) {
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      await api.patch(`/conversations/${id}`, { title: trimmed });
      get().setTitle(id, trimmed);
    } catch (e) {
      useToasts.getState().push(e.message, "error");
    }
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

  // Archiving just drops the conversation from the active list (the backend
  // filters WHERE archived=0) -- it isn't deleted, so there's no data loss,
  // but there's no "show archived" view yet, so treat this as a soft hide.
  async archive(id, archived = true) {
    try {
      await api.post(`/conversations/${id}/archive`, { archived });
    } catch (e) {
      useToasts.getState().push(e.message, "error");
      return;
    }
    set((s) => {
      const list = s.list.filter((c) => c.id !== id);
      return { list, activeId: s.activeId === id ? (list[0]?.id ?? null) : s.activeId };
    });
  },

  async clone(id) {
    try {
      const conv = await api.post(`/conversations/${id}/clone`);
      set((s) => ({ list: [conv, ...s.list], activeId: conv.id }));
      return conv.id;
    } catch (e) {
      useToasts.getState().push(e.message, "error");
      return null;
    }
  },
}));
