// One confirmation dialog for the whole app.
//
// WHY A STORE AND NOT A COMPONENT PROP. Destructive actions are raised from
// everywhere — a sidebar context menu, a settings tab, a store's own method.
// Threading `onConfirm` down to each of those means every intermediate
// component learns about confirmation, and in practice it meant the ones far
// from a modal simply skipped it: eight destructive actions shipped with no
// confirmation at all, while Research had a hand-rolled modal and two places
// fell back to window.confirm().
//
// WHY NOT window.confirm(). It is a native OS dialog: wrong font, wrong
// chrome, ignores the theme entirely, and in a frameless dark Electron window
// it reads as though a different application interrupted you.
//
// NOT for the frequent case. Deleting a chat uses an undo toast instead — see
// conversations.remove() for why a confirm on a high-frequency action stops
// being read. This is for things with no way back.
import { create } from "zustand";

export const useConfirm = create((set, get) => ({
  pending: null, // { title, body, confirmLabel, danger, onConfirm }

  /**
   * ask({
   *   title: "Clear the security log?",
   *   body: "Every recorded event is erased...",
   *   confirmLabel: "Clear the log",
   *   onConfirm: () => ...,
   * })
   *
   * `danger` defaults TRUE: everything routed through here is destructive
   * unless it says otherwise, and defaulting the other way would quietly give
   * a delete button the neutral styling.
   */
  ask({ title, body, confirmLabel = "Confirm", danger = true, onConfirm }) {
    set({ pending: { title, body, confirmLabel, danger, onConfirm } });
  },

  cancel() { set({ pending: null }); },

  run() {
    const p = get().pending;
    // Cleared BEFORE running, so an onConfirm that itself opens a dialog (or
    // throws) cannot leave the old one stuck on screen.
    set({ pending: null });
    p?.onConfirm?.();
  },
}));
