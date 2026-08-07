// Files staged in the composer, plus what the selected model can do with them.
//
// TWO UPLOAD PATHS, and the difference is not cosmetic:
//
//   * BY PATH — a file dragged from the file manager. Electron can tell us
//     where it lives, so the backend opens it directly. This is the only path
//     that can attach a FOLDER, and it avoids pulling a 20MB PDF through the
//     renderer's memory and back out over HTTP.
//   * BY BYTES — a pasted screenshot, a file dragged out of a browser, or the
//     plain-browser dev build where no bridge exists. There is no path to read,
//     so the bytes go up as multipart.
//
// The store picks per file, not per drop: one gesture can contain both.
import { create } from "zustand";
import { api } from "../api/client";
import { useToasts } from "./toasts";

export const useAttachments = create((set, get) => ({
  conversationId: null,
  items: [],
  uploading: false,
  // {vision, known} for the current model. `known: false` means Ollama did not
  // answer -- treated as capable, never warned about. A warning for a
  // limitation that may not exist teaches people to dismiss warnings.
  caps: { vision: true, known: false },
  // True while a file is being dragged anywhere over the chat. Lives in the
  // store because the element that DETECTS the drag (the whole conversation)
  // is not the element that shows the highlight (the composer).
  dragging: false,
  setDragging: (dragging) => set((s) => (s.dragging === dragging ? {} : { dragging })),

  async load(conversationId) {
    set({ conversationId, items: [] });
    if (!conversationId) return;
    try {
      set({ items: await api.get(`/conversations/${conversationId}/attachments`) });
    } catch {
      /* nothing staged, or backend not up yet -- an empty tray is correct */
    }
  },

  // Asked per model rather than cached forever: the user can switch models
  // between messages and the warning has to follow.
  async loadCaps(model) {
    if (!model) {
      set({ caps: { vision: true, known: false } });
      return;
    }
    try {
      const res = await api.get(`/models/${encodeURIComponent(model)}/capabilities`);
      set({ caps: { vision: res.vision, known: res.known } });
    } catch {
      set({ caps: { vision: true, known: false } });
    }
  },

  /** Accepts a DataTransfer's files (drop or paste) and routes each one. */
  async addFiles(fileList) {
    const { conversationId } = get();
    if (!conversationId || !fileList?.length) return;

    const paths = [];
    const blobs = [];
    for (const file of fileList) {
      const p = window.arthur?.pathForFile ? window.arthur.pathForFile(file) : "";
      if (p) paths.push(p);
      else blobs.push(file);
    }

    set({ uploading: true });
    try {
      const added = [];
      const errors = [];
      let truncatedFolders = [];

      if (paths.length) {
        const res = await api.post(`/conversations/${conversationId}/attachments/paths`, { paths });
        added.push(...(res.attachments || []));
        errors.push(...(res.errors || []));
        truncatedFolders = res.truncated_folders || [];
      }
      if (blobs.length) {
        const form = new FormData();
        for (const b of blobs) form.append("files", b, b.name || "pasted");
        const res = await api.postForm(`/conversations/${conversationId}/attachments`, form);
        added.push(...(res.attachments || []));
        errors.push(...(res.errors || []));
      }

      if (added.length) set((s) => ({ items: [...s.items, ...added] }));

      // Partial success is reported as partial. A drop of six files where one
      // is too large should attach five and say which one didn't, rather than
      // failing the whole gesture or silently dropping it.
      for (const e of errors) {
        useToasts.getState().push(`${e.filename}: ${e.error}`, "error", 7000);
      }
      for (const name of truncatedFolders) {
        useToasts.getState().push(
          `${name} has more files than Arthur will attach at once — the first 50 were added.`,
          "info", 7000,
        );
      }
    } catch (e) {
      useToasts.getState().push(e.message || "Couldn't attach those files.", "error");
    } finally {
      set({ uploading: false });
    }
  },

  async addFromPicker() {
    const { conversationId } = get();
    if (!conversationId) return;
    if (!window.arthur?.pickFiles) {
      useToasts.getState().push("Choosing files needs the desktop app.", "error");
      return;
    }
    const paths = await window.arthur.pickFiles();
    if (!paths?.length) return;
    set({ uploading: true });
    try {
      const res = await api.post(`/conversations/${conversationId}/attachments/paths`, { paths });
      set((s) => ({ items: [...s.items, ...(res.attachments || [])] }));
      for (const e of res.errors || []) {
        useToasts.getState().push(`${e.filename}: ${e.error}`, "error", 7000);
      }
    } catch (e) {
      useToasts.getState().push(e.message || "Couldn't attach those files.", "error");
    } finally {
      set({ uploading: false });
    }
  },

  async remove(id) {
    // Removed from the tray immediately, then from disk. The delete is not
    // worth a spinner and a failure would only leave an orphaned file, never a
    // chip the user thinks they got rid of.
    set((s) => ({ items: s.items.filter((a) => a.id !== id) }));
    try {
      await api.del(`/attachments/${id}`);
    } catch {
      /* orphaned file on disk is better than a chip that won't go away */
    }
  },

  // Cleared after a send: the backend has bound these to the message, so they
  // are part of the transcript now rather than staged in the composer.
  clear: () => set({ items: [] }),

  /** Images staged against a model that cannot see them. Drives the warning. */
  blindImages() {
    const s = get();
    if (s.caps.vision || !s.caps.known) return [];
    return s.items.filter((a) => a.kind === "image");
  },
}));
