// The folder a conversation is allowed to touch, and what is inside it.
import { api } from "./client";

export function getWorkspace(conversationId) {
  return api.get(`/conversations/${conversationId}/workspace`);
}

export function setWorkspace(conversationId, root) {
  // `root: null` clears the binding, which returns the conversation to
  // inheriting the last-used folder rather than denying it access.
  return api.put(`/conversations/${conversationId}/workspace`, { root });
}

export function getTree(conversationId) {
  const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  return api.get(`/workspace/tree${q}`);
}
