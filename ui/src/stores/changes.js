// The review queue: what the agent staged, and whether the user takes it.
//
// WHY a store and not component state: three consumers need the same data --
// the review panel, the decorated file tree, and the chat stream (which pokes
// it after every turn). Component state would mean each fetches its own copy
// and they drift.
//
// Keyed by conversation because the changeset is: two chats editing two
// projects must never share a review queue.
import { create } from "zustand";
import { applyChanges, discardChanges, getChanges } from "../api/changes";
import { useToasts } from "./toasts";
import { useWorkspace } from "./workspace";

const EMPTY = { changes: [], files: 0, additions: 0, deletions: 0 };

// Card DOM nodes, so the tree can scroll a file's diff into view. Kept OUTSIDE
// the store: they are not state, nothing renders from them, and putting DOM
// refs in a reactive store means a re-render on every mount.
const cards = {};

export const useChanges = create((set, get) => ({
  conversationId: null,
  ...EMPTY,
  expanded: true,       // the review is the point; it opens itself
  busy: false,          // an apply/discard is in flight
  capped: false,        // last turn hit the tool-use limit -> changeset is PARTIAL
  flash: null,          // path briefly highlighted after a jump from the tree
  selected: {},         // path -> false for files the user unticked
  diffOpen: {},         // path -> bool, overrides the auto fold for long diffs

  async load(conversationId) {
    if (!conversationId) {
      set({ conversationId: null, ...EMPTY, selected: {}, diffOpen: {}, capped: false });
      return;
    }
    const switching = get().conversationId !== conversationId;
    if (switching) {
      set({ conversationId, ...EMPTY, selected: {}, diffOpen: {}, capped: false, flash: null });
    }
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
      // switch. The panel shows nothing and the files stay on disk untouched,
      // which is the safe direction to fail.
      if (get().conversationId === conversationId) set({ ...EMPTY });
    }
  },

  // Called when a new message is sent: a fresh turn is not cut short until it
  // says so, and leaving the banner up would accuse it of something it hasn't
  // done yet.
  clearCapped: () => set({ capped: false }),
  markCapped: () => set({ capped: true, expanded: true }),

  toggleOpen: () => set((s) => ({ expanded: !s.expanded })),
  toggleDiff: (path) =>
    set((s) => ({ diffOpen: { ...s.diffOpen, [path]: !(s.diffOpen[path] ?? false) } })),

  // DEFAULT-SELECTED, and stored as exceptions.
  //
  // The common case is "apply everything", so an unvisited checkbox must mean
  // yes. Storing only the boxes the user actively unticked keeps that true even
  // when the agent stages a new file mid-review — a fresh path has no entry, so
  // it arrives selected instead of silently opting out of the apply.
  toggleSelected: (path) =>
    set((s) => ({ selected: { ...s.selected, [path]: s.selected[path] === false } })),

  selectedPaths: () => {
    const { changes, selected } = get();
    // Conflicted files are never included: they cannot apply, and offering them
    // in the count would overstate what the button is about to do.
    return changes.filter((c) => !c.conflict && selected[c.path] !== false).map((c) => c.path);
  },

  registerCard: (path, el) => { if (el) cards[path] = el; else delete cards[path]; },

  // Tree -> diff. The tree is the map; this is what makes it a map OF something
  // rather than a second list beside it.
  jumpTo: (path) => {
    cards[path]?.scrollIntoView({ behavior: "smooth", block: "center" });
    set({ expanded: true, flash: path });
    setTimeout(() => { if (get().flash === path) set({ flash: null }); }, 1200);
  },

  async apply(conversationId, paths) {
    const targets = paths || get().selectedPaths();
    if (!targets.length || get().busy) return;
    set({ busy: true });
    try {
      const res = await applyChanges(conversationId, targets);
      // The receipt goes in the transcript rather than a toast: this is the one
      // moment Code mode changed the user's disk, and a message that scrolls
      // away is a poor record of it. See the apply route.
      if (res.receipt) {
        const { useChat } = await import("./chat");
        useChat.getState().appendMessage(conversationId, res.receipt);
      }
      // Conflicts are NOT toasted any more — they now render on the file's own
      // card, where the file still is. See ChangesPanel.
      for (const f of res.failed) {
        useToasts.getState().push(`Could not write ${f.path}: ${f.error}`, "error");
      }
      await get().load(conversationId);
      await useWorkspace.getState().refreshTree();   // files may have appeared or gone
      if (!get().changes.length) set({ capped: false });
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
      if (!get().changes.length) set({ capped: false });
    } catch (e) {
      useToasts.getState().push(e.message || "Could not discard the changes.", "error");
    } finally {
      set({ busy: false });
    }
  },

  // The two remedies. Both are ordinary messages rather than special API calls:
  // the fix for "this edit is stale" and "you stopped early" is more agent work,
  // and routing them through the same path the user's own words take means they
  // land in the transcript as a visible part of the story.
  reread: (conversationId, path) =>
    get()._say(conversationId,
      `The file \`${path}\` changed on disk after you read it. Re-read it and redo your edit against the current version.`),

  continueRun: (conversationId) =>
    get()._say(conversationId, "Carry on from where you stopped."),

  async _say(conversationId, text) {
    // Imported lazily: stores/chat.js already imports this module, and a static
    // import back would be a cycle.
    const { useChat } = await import("./chat");
    useChat.getState().send(conversationId, text, { mode: "code" });
  },
}));
