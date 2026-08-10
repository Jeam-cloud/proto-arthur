// Pending file edits staged by Code mode, and the two ways they end.
import { api } from "./client";

// `diffs=false` for the header badge, which only needs the counts. Shipping a
// twelve-file diff to render the text "3 files" is a lot of bytes for nothing.
export function getChanges(conversationId, { diffs = true } = {}) {
  return api.get(`/conversations/${conversationId}/changes?diffs=${diffs}`);
}

// paths === null means "all of them", which is the one-click case.
export function applyChanges(conversationId, paths = null) {
  return api.post(`/conversations/${conversationId}/changes/apply`, { paths });
}

export function discardChanges(conversationId, paths = null) {
  return api.post(`/conversations/${conversationId}/changes/discard`, { paths });
}

// What can still be put back. With edits landing automatically, this is the
// safety model rather than a convenience — see python/coding/undo.py.
export function getUndos(conversationId) {
  return api.get(`/conversations/${conversationId}/undo`);
}

// id === null means the most recent apply in this chat, which is the button on
// the receipt and the only case that matters in practice.
export function undoApply(conversationId, id = null) {
  return api.post(`/conversations/${conversationId}/undo`, { id });
}
