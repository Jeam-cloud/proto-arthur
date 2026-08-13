// Transient notices. One store, because a toast raised from a store, a
// component or an API catch block must all queue in the same place.
//
// THREE THINGS HERE ARE NOT DECORATION:
//   * an ACTION, so a toast can carry the way back from what it is announcing
//     (delete + Undo). This is what lets destructive actions skip a confirm
//     dialog without becoming unrecoverable -- see conversations.remove().
//   * PAUSE ON HOVER, because a toast you are reaching for should not expire
//     while you reach for it.
//   * MANUAL DISMISS, because the old fixed 5s timer was the only way one ever
//     left the screen: an error you wanted gone sat there, and one you wanted
//     to re-read was already gone.
import { create } from "zustand";

let nextId = 1;

// A toast carrying an action asks something of the reader, so it gets longer.
// Plain ones are read at a glance and 4.8s is enough.
const PLAIN_MS = 4800;
const ACTION_MS = 8000;
// After the pointer leaves, a short grace rather than the full duration again:
// it has already been read by then, and restarting the clock makes a hovered
// toast feel stuck.
const RESUME_MS = 2600;

const timers = new Map();

function clear(id) {
  const t = timers.get(id);
  if (t) { clearTimeout(t); timers.delete(id); }
}

export const useToasts = create((set, get) => ({
  toasts: [],

  /**
   * push("Saved.", "success")
   * push('"Draft" deleted.', "info", { action: {...}, onExpire: commitFn })
   *
   * `onExpire` runs when the toast leaves WITHOUT the action being taken —
   * whether it timed out or the user dismissed it by hand. That is what makes
   * a deferred destructive action safe: the deletion is not committed until
   * the offer to undo it is genuinely over, however long the toast lived. A
   * fixed timer next to the toast's own would drift apart the moment someone
   * hovered, and the two must agree.
   *
   * `ms` stays supported for the handful of callers that pass an explicit
   * duration, so nothing that existed before this changed behaviour.
   */
  push(message, kind = "info", opts = {}) {
    const { action = null, onExpire = null, ms } =
      typeof opts === "number" ? { ms: opts } : opts;
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind, action, onExpire }] }));
    const life = ms ?? (action ? ACTION_MS : PLAIN_MS);
    timers.set(id, setTimeout(() => get().dismiss(id), life));
    return id;
  },

  dismiss(id) {
    clear(id);
    const toast = get().toasts.find((t) => t.id === id);
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    toast?.onExpire?.();
  },

  pause(id) { clear(id); },

  resume(id) {
    clear(id);
    timers.set(id, setTimeout(() => get().dismiss(id), RESUME_MS));
  },

  // Running the action dismisses the toast: the offer has been taken, and
  // leaving it on screen invites a second Undo of an already-undone thing.
  //
  // Note it does NOT go through dismiss() — that would fire onExpire and
  // commit the very deletion this click just cancelled.
  runAction(id) {
    const toast = get().toasts.find((t) => t.id === id);
    clear(id);
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    toast?.action?.run?.();
  },
}));
