// The review queue: what the agent staged, and whether the user takes it.
//
// WHY a store and not component state: three consumers need the same data --
// the review panel, the count badge on the composer bar, and the chat stream
// (which pokes it after every turn). Component state would mean each fetches
// its own copy and they drift.
//
// Keyed by conversation because the changeset is: two chats editing two
// projects must never share a review queue.
import { create } from "zustand";
import { applyChanges, discardChanges, getChanges } from "../api/changes";
import { useToasts } from "./toasts";
import { useWorkspace } from "./workspace";

const EMPTY = { changes: [], files: 0, additions: 0, deletions: 0 };

export const useChanges = create((set, get) => ({
  conversationId: null,
  ...EMPTY,
  open: false,          // is the review panel showing
  busy: false,          // an apply/discard is in flight
  selected: {},         // path -> bool. Absent = selected; see selectedPaths()
  expanded: {},         // path -> bool, which diffs are unfolded

  async load(conversationId) {
    if (!conversationId) {
      set({ conversationId: null, ...EMPTY, open: false, selected: {}, expanded: {} });
      return;
    }
    const switching = get().conversationId !== conversationId;
    if (switching) set({ conversationId, ...EMPTY, selected: {}, expanded: {}, open: false });
    try {
      const res = await getChanges(conversationId);
      // Guard against a slow response for a chat the user already left.
      if (get().conversationId !== conversationId) return;
      set({
        changes: res.changes || [],
        files: res.files, additions: res.additions, deletions: res.deletions,
      });
    } catch {
      // Pending changes we cannot list are not worth a toast on every chat
      // switch. The panel simply shows nothing and the files stay on disk
      // untouched, which is the safe direction to fail.
      if (get().conversationId === conversationId) set({ ...EMPTY });
    }
  },

  toggleOpen: () => set((s) => ({ open: !s.open })),
  toggleExpanded: (path) =>
    set((s) => ({ expanded: { ...s.expanded, [path]: !s.expanded[path] } })),

  // DEFAULT-SELECTED, and stored as exceptions.
  //
  // The common case is "apply everything", so an unvisited checkbox must mean
  // yes. Storing only the boxes the user actively unticked keeps that true
  // even when the agent stages a new file mid-review — a fresh path has no
  // entry, so it arrives selected instead of silently opting out of the apply.
  toggleSelected: (path) =>
    set((s) => ({ selected: { ...s.selected, [path]: s.selected[path] === false } })),

  selectedPaths: () => {
    const { changes, selected } = get();
    return changes.filter((c) => selected[c.path] !== false).map((c) => c.path);
  },

  async apply(conversationId, paths) {
    const targets = paths || get().selectedPaths();
    if (!targets.length || get().busy) return;
    set({ busy: true });
    try {
      const res = await applyChanges(conversationId, targets);
      const n = res.applied.length;
      if (n) {
        useToasts.getState().push(
          `Applied ${n} ${n === 1 ? "file" : "files"}.`, "success");
      }
      // Conflicts are the interesting failure: the user edited the file in
      // their own editor while the agent was working. Named explicitly,
      // because "some changes were skipped" is useless — they need to know
      // which file to look at.
      if (res.conflicts.length) {
        useToasts.getState().push(
          `Skipped ${res.conflicts.join(", ")} — changed on disk since Arthur read it. ` +
          "Ask it to re-read and try again.", "error");
      }
      for (const f of res.failed) {
        useToasts.getState().push(`Could not write ${f.path}: ${f.error}`, "error");
      }
      await get().load(conversationId);
      // The tree may have gained or lost files.
      await useWorkspace.getState().refreshTree();
      if (!get().changes.length) set({ open: false });
    } catch (e) {
      useToasts.getState().push(e.message || "Could not apply the changes.", "error");
    } finally {
      set({ busy: false });
    }
  },

  async discard(conversationId, paths) {
    if (get().busy) return;
    set({ busy: true });
    try {
      await discardChanges(conversationId, paths || null);
      await get().load(conversationId);
      if (!get().changes.length) set({ open: false });
    } catch (e) {
      useToasts.getState().push(e.message || "Could not discard the changes.", "error");
    } finally {
      set({ busy: false });
    }
  },
}));
