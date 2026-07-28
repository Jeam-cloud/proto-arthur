// Which folder the current conversation can touch, and its file tree.
//
// WHY a store rather than component state: three separate places need this --
// the folder bar, the file tree panel, and the composer (so clicking a file
// can insert its path). Keeping it in one place also means the tree refreshes
// once when the folder changes rather than each consumer fetching its own.
//
// Keyed by conversation id because the binding IS per conversation now (see
// migration 2 and _conversation_workspace in core/api/routes.py). Switching
// chats must not show you the previous chat's folder.
import { create } from "zustand";
import { getTree, getWorkspace, setWorkspace } from "../api/workspace";
import { useToasts } from "./toasts";

export const useWorkspace = create((set, get) => ({
  conversationId: null,
  root: null,
  bound: false,      // false = inheriting the last-used folder, not yet chosen here
  exists: true,      // a remembered path can point at an unplugged drive
  tree: [],
  truncated: false,
  loading: false,
  expanded: {},      // path -> bool, folders the user has opened

  async load(conversationId) {
    if (!conversationId) {
      set({ conversationId: null, root: null, bound: false, tree: [] });
      return;
    }
    set({ conversationId, loading: true });
    try {
      const ws = await getWorkspace(conversationId);
      // Guard against a slower response for a conversation the user has
      // already navigated away from -- otherwise switching chats quickly can
      // leave the previous chat's folder on screen.
      if (get().conversationId !== conversationId) return;
      set({ root: ws.root, bound: ws.bound, exists: ws.exists });
      if (ws.root && ws.exists) await get().refreshTree();
      else set({ tree: [], truncated: false });
    } catch {
      // A folder we cannot read about is not worth a toast on every chat
      // switch; the bar shows "no folder" and the user can pick one.
      set({ root: null, bound: false, tree: [] });
    } finally {
      set({ loading: false });
    }
  },

  async refreshTree() {
    const { conversationId } = get();
    if (!conversationId) return;
    try {
      const res = await getTree(conversationId);
      if (get().conversationId !== conversationId) return;
      set({ tree: res.tree || [], truncated: !!res.truncated, exists: !res.missing });
    } catch {
      set({ tree: [] });
    }
  },

  // Opens the OS folder picker and binds the result to this conversation.
  async choose() {
    const { conversationId } = get();
    if (!conversationId) return;
    if (!window.arthur?.pickFolder) {
      useToasts.getState().push("Choosing a folder needs the desktop app.", "error");
      return;
    }
    const folder = await window.arthur.pickFolder();
    if (!folder) return; // cancelled
    try {
      await setWorkspace(conversationId, folder);
      set({ root: folder, bound: true, exists: true });
      await get().refreshTree();
      useToasts.getState().push("Folder set for this chat.", "success");
    } catch (e) {
      useToasts.getState().push(e.message || "Could not set that folder.", "error");
    }
  },

  toggleDir: (path) =>
    set((s) => ({ expanded: { ...s.expanded, [path]: !s.expanded[path] } })),

  // Text the file tree wants appended to the composer's draft.
  //
  // WHY a token rather than just the string: the composer consumes this in an
  // effect and then clears it, and clicking the SAME file twice would produce
  // an identical value that React sees as unchanged, so the second click would
  // do nothing. The counter makes every request distinct.
  //
  // WHY route it through the store at all: the draft is local state inside
  // Composer, and the tree is a sibling three levels away. A shared channel
  // beats lifting the whole draft up and re-rendering the message list on
  // every keystroke.
  pendingInsert: null,   // {text, token} | null
  requestInsert: (text) =>
    set((s) => ({ pendingInsert: { text, token: (s.pendingInsert?.token || 0) + 1 } })),
  clearInsert: () => set({ pendingInsert: null }),
}));
