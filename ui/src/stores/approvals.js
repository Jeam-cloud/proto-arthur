// Pending human confirmations. The chat stream pushes approval_required
// events here; the ApprovalModal renders the queue head; a decision POSTs
// back and the backend's awaiting Future resumes the agent loop.
import { create } from "zustand";
import { api } from "../api/client";

export const useApprovals = create((set, get) => ({
  queue: [],

  push(approval) {
    set((s) => ({ queue: [...s.queue, approval] }));
  },

  dismiss(id) {
    set((s) => ({ queue: s.queue.filter((a) => a.id !== id) }));
  },

  async decide(id, approved) {
    get().dismiss(id); // optimistic: the dialog closes immediately
    try {
      await api.post(`/approvals/${id}`, { approved });
    } catch {
      // stream may already have timed out to deny — that's the safe direction
    }
  },
}));
