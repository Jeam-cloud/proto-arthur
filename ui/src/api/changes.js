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
