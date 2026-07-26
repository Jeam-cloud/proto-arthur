// Pinned conversations + folders -- purely a sidebar organizing layer.
//
// WHY this is localStorage, not a backend table: the mockup's sidebar shows
// pinned chats and folders, but python/core/conversations.py has no folder or
// pin columns, and adding a migration is out of scope for a UI pass. This
// store persists the same shape (pinned id list, folder id->name list,
// conv id->folder id map) to localStorage so it survives restarts. If rian
// wants folders to sync across machines later, promoting this into a real
// backend table means only rewriting this file, not any component that uses it.
import { create } from "zustand";

const KEY = "arthur.organize.v1";

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { pinned: [], folders: [], convFolder: {}, openFolders: {} };
    const parsed = JSON.parse(raw);
    return {
      pinned: parsed.pinned || [],
      folders: parsed.folders || [],
      convFolder: parsed.convFolder || {},
      openFolders: parsed.openFolders || {},
    };
  } catch {
    return { pinned: [], folders: [], convFolder: {}, openFolders: {} };
  }
}

function persist(state) {
  try {
    localStorage.setItem(KEY, JSON.stringify({
      pinned: state.pinned, folders: state.folders,
      convFolder: state.convFolder, openFolders: state.openFolders,
    }));
  } catch {
    // localStorage unavailable (private mode, quota) -- organizing state just
    // won't survive a restart; nothing else depends on it.
  }
}

export const useOrganize = create((set, get) => ({
  ...load(),

  togglePin(convId) {
    set((s) => {
      const pinned = s.pinned.includes(convId)
        ? s.pinned.filter((id) => id !== convId)
        : [...s.pinned, convId];
      const next = { ...s, pinned };
      persist(next);
      return { pinned };
    });
  },

  addFolder(name) {
    const id = `f-${Date.now()}`;
    set((s) => {
      const folders = [...s.folders, { id, name }];
      const openFolders = { ...s.openFolders, [id]: true };
      persist({ ...s, folders, openFolders });
      return { folders, openFolders };
    });
    return id;
  },

  toggleFolder(id) {
    set((s) => {
      const openFolders = { ...s.openFolders, [id]: !s.openFolders[id] };
      persist({ ...s, openFolders });
      return { openFolders };
    });
  },

  renameFolder(id, name) {
    const trimmed = name.trim();
    if (!trimmed) return;
    set((s) => {
      const folders = s.folders.map((f) => (f.id === id ? { ...f, name: trimmed } : f));
      persist({ ...s, folders });
      return { folders };
    });
  },

  // Deleting a folder just un-assigns its chats back to Recents -- it never
  // deletes conversations, only the grouping.
  deleteFolder(id) {
    set((s) => {
      const folders = s.folders.filter((f) => f.id !== id);
      const convFolder = Object.fromEntries(
        Object.entries(s.convFolder).filter(([, fid]) => fid !== id)
      );
      const { [id]: _gone, ...openFolders } = s.openFolders;
      persist({ ...s, folders, convFolder, openFolders });
      return { folders, convFolder, openFolders };
    });
  },

  setConvFolder(convId, folderId) {
    set((s) => {
      const convFolder = { ...s.convFolder, [convId]: folderId };
      persist({ ...s, convFolder });
      return { convFolder };
    });
  },
}));
