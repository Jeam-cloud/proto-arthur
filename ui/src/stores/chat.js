// Chat store — the heart of the UI. One state slice per conversation:
//   messages   : persisted turns (loaded from the API)
//   draft      : the assistant message currently streaming in
//   activity   : tool events for the current stream (research, clicks, ...)
//   error      : terminal stream error, rendered inline with a retry
//
// WHY the streaming draft is separate from messages: tokens arrive dozens of
// times per second. Appending to a small `draft` object and merging it into
// `messages` once at DONE keeps re-renders cheap and the persisted list
// clean (its ids come from the server).
import { create } from "zustand";
import { api } from "../api/client";
import { streamSSE } from "../api/sse";
import { useApprovals } from "./approvals";
import { useChanges } from "./changes";
import { useConversations } from "./conversations";

// ONE stable, frozen reference for the "no data yet" slice.
//
// WHY it must be a shared constant, not a factory: components read state via
// `useChat((s) => s.slice(cid))`, and Zustand's useSyncExternalStore re-renders
// whenever the selector RETURNS A NEW REFERENCE. A factory (`() => ({...})`)
// hands back a fresh object every render, so an empty conversation looked
// "changed" on every pass -> infinite re-render ("getSnapshot should be cached"
// / "Maximum update depth exceeded"). A single frozen object is referentially
// stable, so the empty case renders once and stops.
//
// It's safe to share because it is NEVER mutated: _patch and send() always
// spread it into a NEW object and replace arrays with new arrays (never .push).
// Object.freeze makes an accidental mutation throw in dev instead of corrupting
// every empty slice silently.
const EMPTY_SLICE = Object.freeze({
  messages: [], draft: null, activity: [], memoryUsed: [],
  // A pending ask_user question, or null. Not a message: it is a live control,
  // and answering it replaces it with the user's own message.
  ask: null,
  // Charts produced by tools this turn, in arrival order. Held on the slice
  // like `activity` rather than pushed into `messages`: they belong to the
  // turn that fetched them, and they are rebuilt from the tool result each
  // time rather than persisted as model text.
  charts: [],
  // `stopped` is deliberately separate from `error`: a cancelled turn is a
  // normal ending, and putting it in `error` is what made Stop look like a
  // crash. Cleared on the next send, like `error`.
  streaming: false, error: null, stopped: false, loaded: false, startedAt: null,
});

export const useChat = create((set, get) => ({
  byConv: {},
  aborters: {},
  // Per-conversation model override from the composer picker. "" = auto
  // (backend resolves: mode's assigned model, else the global default).
  //
  // This map is a CACHE of `conversations.model`, not the source of truth. It
  // used to be the only copy, living purely in memory, so every relaunch reset
  // every chat to Auto while the chip carried on looking authoritative —
  // twenty turns of qwen2.5-coder followed by a silent switch back to the
  // default. hydrateModelOverrides() fills it from the server on load.
  modelOverride: {},

  // Seed from the conversation list at boot. Only rows that actually carry a
  // model are copied: a NULL means "never chose one", which must stay absent
  // here so the chip renders Auto rather than an empty explicit selection.
  hydrateModelOverrides(conversations) {
    const seeded = {};
    for (const c of conversations || []) {
      if (c && typeof c.model === "string") seeded[c.id] = c.model;
    }
    set((s) => ({ modelOverride: { ...seeded, ...s.modelOverride } }));
  },

  setModelOverride(cid, model) {
    // Optimistic: the picker closes instantly and the next send already uses
    // the new model. The write is a durability detail, so a failed PUT must
    // not rewind a choice the user can see they made — it is logged and the
    // in-memory value stands for this session.
    set((s) => ({ modelOverride: { ...s.modelOverride, [cid]: model } }));
    api.put(`/conversations/${cid}/model`, { model }).catch((e) => {
      console.warn("could not persist model choice for", cid, e);
    });
  },

  slice(cid) {
    return get().byConv[cid] || EMPTY_SLICE;
  },

  _patch(cid, patch) {
    set((s) => ({
      byConv: { ...s.byConv, [cid]: { ...(s.byConv[cid] || EMPTY_SLICE), ...patch } },
    }));
  },

  async loadMessages(cid) {
    if (get().slice(cid).loaded) return;
    const rows = await api.get(`/conversations/${cid}/messages`);
    const messages = rows
      // `receipt` rows are Code mode's "this actually landed" notes. Rendered
      // in the transcript, never sent to the model — see the apply route.
      .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "receipt")
      .map((m) => ({ id: m.id, role: m.role, content: m.content, provider: m.provider }));
    get()._patch(cid, { messages, loaded: true });
  },

  async send(cid, text, { mode, model, provider, attachments } = {}) {
    const g = get();
    const s = g.slice(cid);
    if (s.streaming) return;

    // The optimistic user message carries its attachments.
    //
    // Without this, a sent message shows its files only after the conversation
    // is refetched -- so during the send that matters they are invisible, and a
    // message carrying ONLY an image renders as an empty bubble. That made the
    // whole feature undebuggable: there was no way to see which message a
    // dropped file had actually gone with.
    const userMsg = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      attachments: attachments || [],
    };
    g._patch(cid, {
      messages: [...s.messages, userMsg],
      draft: { role: "assistant", content: "", provider: provider || "local" },
      // Wall-clock start, for the run block's elapsed counter. Stored rather
      // than derived: a turn that takes four minutes needs a clock, and the
      // only honest starting point is when the request actually went out.
      startedAt: Date.now(),
      // `ask` cleared here rather than when a choice is clicked, so it also
      // clears when the user ignores the question and types something else —
      // a stale question offering choices about a finished topic is worse than
      // no question at all.
      activity: [], memoryUsed: [], ask: null, charts: [], streaming: true,
      error: null, stopped: false,
    });

    // A new turn is not cut short until it says so; leaving the banner up would
    // accuse this turn of something the last one did.
    useChanges.getState().clearCapped();

    const aborter = new AbortController();
    set((st) => ({ aborters: { ...st.aborters, [cid]: aborter } }));

    try {
      const stream = streamSSE("/chat/stream", {
        conversation_id: cid, message: text,
        mode: mode || "general", model: model || "", provider: provider || "local",
      }, { signal: aborter.signal });

      for await (const { event, data } of stream) {
        const cur = get().slice(cid);
        switch (event) {
          case "token":
            get()._patch(cid, { draft: { ...cur.draft, content: cur.draft.content + data.content } });
            break;
          // The model typed a tool call as prose and the backend recognised
          // it after the tokens were already on screen. Swap the draft for the
          // cleaned text — a raw JSON blob is not something the user should
          // have to read past.
          case "draft_replace":
            get()._patch(cid, { draft: { ...cur.draft, content: data.content } });
            break;
          case "tool_start":
            get()._patch(cid, { activity: [...cur.activity, { key: `${data.name}-${cur.activity.length}`, name: data.name, summary: data.summary, running: true }] });
            break;
          case "tool_result":
            get()._patch(cid, {
              activity: cur.activity.map((a, i) =>
                i === cur.activity.length - 1 && a.name === data.name
                  ? { ...a, running: false, ok: data.ok, flagged: data.flagged,
                      summary: data.summary, detail: data.detail }
                  : a),
            });
            break;
          case "approval_required":
            useApprovals.getState().push(data);
            break;
          case "approval_resolved":
            useApprovals.getState().dismiss(data.id);
            break;
          // The turn staged file edits. The event carries only totals, so the
          // panel fetches the diffs itself — see api/changes.js for why they
          // are not pushed down the stream.
          case "changes_updated":
            useChanges.getState().load(cid);
            break;
          // The turn WROTE the files. The normal ending for a Code turn now:
          // the panel becomes a receipt with an Undo button rather than a
          // decision waiting to be made. The receipt message is appended here
          // so it lands in the transcript in the same tick as the answer.
          case "changes_applied":
            useChanges.getState().applied(data);
            if (data.receipt) get().appendMessage(cid, data.receipt);
            break;
          // The turn ran out of tool calls rather than finishing, so whatever
          // is staged is PARTIAL. The panel says so above the files; applying
          // half a change is the mistake that screen exists to prevent.
          case "tool_limit":
            if (data.mode === "code") useChanges.getState().markCapped();
            break;
          // The model asked a question and the turn ENDED. Held on the slice
          // rather than pushed into `messages`: it is a live control the user
          // acts on, and once they answer it is replaced by their own message.
          // Clearing it on the next send is what stops a stale question sitting
          // under the transcript offering choices about a finished topic.
          case "ask_user":
            get()._patch(cid, { ask: data });
            break;
          case "chart":
            get()._patch(cid, { charts: [...cur.charts, data] });
            break;
          case "memory_used":
            get()._patch(cid, { memoryUsed: data.items });
            break;
          case "title":
            useConversations.getState().setTitle(data.conversation_id, data.title);
            break;
          case "status":
            get()._patch(cid, { activity: [...cur.activity, { key: `s-${cur.activity.length}`, name: "status", summary: data.text, running: false, ok: true }] });
            break;
          case "error":
            get()._patch(cid, { error: data });
            break;
          case "done": {
            const done = get().slice(cid);
            get()._patch(cid, {
              messages: [...done.messages, { id: data.message_id, role: "assistant", content: done.draft.content, provider: done.draft.provider, activity: done.activity, charts: done.charts }],
              draft: null, activity: [], charts: [],
            });
            break;
          }
          default:
            break;
        }
      }
    } catch (e) {
      // STOPPING IS NOT FAILING.
      //
      // Clicking Stop aborts the fetch, which rejects the stream with whatever
      // the platform calls it — "BodyStreamBuffer was aborted" in Chromium,
      // "The user aborted a request" elsewhere. That was landing in the same
      // red "Something went wrong" box as a real crash, so the app reported an
      // error every single time it did exactly what it was told.
      //
      // Detected by the aborter we own rather than by matching the message
      // text: the wording is a browser implementation detail and differs
      // between engines and versions, but `aborter.signal.aborted` is a fact.
      if (aborter.signal.aborted || e.name === "AbortError") {
        get()._patch(cid, { error: null, stopped: true });
      } else {
        get()._patch(cid, { error: { code: e.code || "stream_failed", message: e.message } });
      }
    } finally {
      const cur = get().slice(cid);
      // stream ended without DONE (abort/crash): keep partial text, mark noted
      if (cur.draft && cur.draft.content) {
        get()._patch(cid, {
          messages: [...cur.messages, { id: `partial-${Date.now()}`, role: "assistant", content: cur.draft.content, partial: true }],
        });
      }
      get()._patch(cid, { draft: null, streaming: false });
      set((st) => {
        const { [cid]: _gone, ...rest } = st.aborters;
        return { aborters: rest };
      });
    }
  },

  // Append a message the SERVER created outside the stream (currently only the
  // apply receipt). Kept here rather than in the changes store so every write
  // to a conversation's message list goes through one place.
  appendMessage(cid, message) {
    const cur = get().slice(cid);
    get()._patch(cid, { messages: [...cur.messages, message] });
  },

  stop(cid) {
    get().aborters[cid]?.abort();
  },
}));
