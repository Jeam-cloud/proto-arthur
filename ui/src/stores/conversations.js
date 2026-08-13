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

// How long a freshly created chat stays visibly marked as new. Long enough to
// notice if you were looking at the composer, short enough that it is gone
// before it becomes decoration.
const FRESH_MS = 1400;
let freshTimer = null;

export const useConversations = create((set, get) => ({
  list: [],
  activeId: null,
  loaded: false,
  // The chat created in the last FRESH_MS, or null.
  //
  // WHY THIS EXISTS: creating a chat produced no visible confirmation. Every
  // new conversation is titled "New chat" until the model suggests a title
  // after the first exchange, so making two in a row gave two identical rows;
  // and `active` styling already means "the one you are reading", so it could
  // not also mean "the one that just appeared". Marking it separately is the
  // only way those two states can both be true and still be distinguishable.
  justCreated: null,

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
    set((s) => ({
      list: [{ ...conv, message_count: 0 }, ...s.list],
      activeId: conv.id,
      justCreated: conv.id,
    }));
    // Cleared on a timer rather than on the next interaction: the mark means
    // "this appeared just now", which stops being true whether or not the user
    // does anything next.
    clearTimeout(freshTimer);
    freshTimer = setTimeout(() => set({ justCreated: null }), FRESH_MS);
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

  /**
   * Delete a conversation, with a window to take it back.
   *
   * WHY UNDO RATHER THAN A CONFIRM DIALOG. Deleting a chat is the most
   * frequent destructive action in the app, and a confirm on every one trains
   * people to click through it — at which point the dialog costs a click and
   * protects nothing. An undo offer is cheaper for the common case (it is
   * right) and safer for the rare one (it was a mis-click, and the trash icon
   * sits on every hovered row).
   *
   * THE SERVER CALL IS DEFERRED, not just the row hidden. If the DELETE fired
   * now there would be nothing left to restore — the id is gone and a re-POST
   * would produce a different conversation. So the row leaves immediately (the
   * UI must feel instant) and the delete commits only when the undo offer is
   * genuinely over, which the toast tells us via onExpire.
   *
   * If the app is closed inside that window the delete never happens and the
   * chat is still there next launch. That is the safe direction to fail: a
   * conversation that outlives one delete click is a small surprise, one that
   * vanishes when the user meant to keep it is not recoverable.
   */
  remove(id) {
    const { list, activeId } = get();
    const index = list.findIndex((c) => c.id === id);
    if (index === -1) return;
    const conv = list[index];

    const next = list.filter((c) => c.id !== id);
    set({ list: next, activeId: activeId === id ? (next[0]?.id ?? null) : activeId });

    useToasts.getState().push(`"${conv.title}" deleted.`, "info", {
      action: {
        label: "Undo",
        // Reinserted at its ORIGINAL index, not at the top. The list is sorted
        // by recency and undo is meant to look like nothing happened; putting
        // it back in the wrong place is a second, quieter surprise.
        run: () => set((s) => ({
          list: [...s.list.slice(0, index), conv, ...s.list.slice(index)],
          activeId: activeId === id ? id : s.activeId,
        })),
      },
      onExpire: () => {
        api.del(`/conversations/${id}`).catch((e) => {
          // The delete failed on the server, so the chat still exists. Put it
          // back rather than leaving the sidebar disagreeing with the database.
          useToasts.getState().push(`Could not delete "${conv.title}": ${e.message}`, "error");
          set((s) => ({ list: [...s.list.slice(0, index), conv, ...s.list.slice(index)] }));
        });
      },
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
