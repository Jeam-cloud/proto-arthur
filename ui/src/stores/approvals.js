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

  async decide(id, approved, args = null) {
    get().dismiss(id); // optimistic: the dialog closes immediately
    try {
      // `args` carries an edited draft (e.g. a reworded email) back through
      // the same Pydantic gate the model's own call went through — see
      // agent/loop.py _execute_one. Omit it entirely when nothing was edited
      // so the backend runs the tool with its original, already-validated args.
      await api.post(`/approvals/${id}`, args ? { approved, args } : { approved });
    } catch {
      // stream may already have timed out to deny — that's the safe direction
    }
  },
}));
