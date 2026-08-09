// App shell: boot -> onboarding -> main layout. Also owns global keyboard
// shortcuts (tracker p4t4) and the always-mounted overlays (approval modal,
// toasts): approvals must interrupt whatever screen the user is on.
import React, { useEffect, useState } from "react";
import { initApi } from "./api/client";
import { useBackend } from "./stores/backend";
import { useConversations } from "./stores/conversations";
import { useSettings } from "./stores/settings";
import { useResearch } from "./stores/research";
import ModeRail, { LogoMark } from "./components/ModeRail";
import Sidebar from "./components/Sidebar";
import ModelHub from "./components/ModelHub";
import CommandPalette from "./components/CommandPalette";
import ChatView from "./components/chat/ChatView";
import ResearchView from "./components/research/ResearchView";
import SettingsView from "./components/settings/SettingsView";
import Onboarding from "./components/Onboarding";
import ApprovalModal from "./components/ApprovalModal";
import Toasts from "./components/common/Toasts";
import StatusBanner from "./components/common/StatusBanner";
import BootScreen from "./components/common/BootScreen";
import ErrorBoundary from "./components/common/ErrorBoundary";

export default function App() {
  const [view, setView] = useState("chat"); // chat | settings
  const [settingsTab, setSettingsTab] = useState("general");
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The Model hub is an overlay, not a view: it floats over whatever screen
  // you're already on so closing it puts you back exactly where you were,
  // scroll position and all.
  const [hubOpen, setHubOpen] = useState(false);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState(null);
  const { phase, status, startPolling } = useBackend();

  // MODE BELONGS TO THE CONVERSATION, not to the app.
  //
  // It used to be useState here, which meant a chat had no mode of its own --
  // it was whatever the rail happened to point at while you were looking at
  // it, and a reload turned every conversation back into General. Reading it
  // off the active conversation makes "this is a Code chat" a durable fact,
  // and makes the folder bound beside it meaningful.
  const activeId = useConversations((s) => s.activeId);
  const conversations = useConversations((s) => s.list);
  const mode = conversations.find((c) => c.id === activeId)?.mode || "general";
  const researchStage = useResearch((s) => s.stage);
  const researchWide =
    view === "chat" && mode === "research" && (researchStage === "run" || researchStage === "report");

  useEffect(() => {
    (async () => {
      try {
        await initApi();
        startPolling();
        await Promise.all([
          useConversations.getState().load(),
          useSettings.getState().load(),
        ]);
        setBooted(true);
      } catch (e) {
        setBootError(e.message);
      }
    })();
  }, [startPolling]);

  // global shortcuts: Ctrl+N new chat, Ctrl+K command palette, Ctrl+, settings, Esc closes whatever's open
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "n") { e.preventDefault(); useConversations.getState().createNew(); setView("chat"); }
      if (mod && e.key === "k") { e.preventDefault(); setPaletteOpen((o) => !o); }
      if (mod && e.key === ",") { e.preventDefault(); setView("settings"); }
      // Esc unwinds one layer at a time, innermost first. The hub handles its
      // own Esc (it needs to refuse while a download is running), so it's
      // checked here only to stop this handler from also kicking you out of
      // Settings underneath it.
      if (e.key === "Escape") {
        if (hubOpen) return;
        if (paletteOpen) setPaletteOpen(false);
        else setView("chat");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, hubOpen]);

  if (bootError || phase === "failed") {
    return (
      <div className="boot">
        <div className="logo"><LogoMark size={26} /></div>
        <h3 style={{ color: "var(--red)" }}>Arthur couldn't start</h3>
        <p style={{ maxWidth: 420, textAlign: "center", fontSize: 13 }}>
          {bootError || "The local backend stopped responding. Try restarting the app; if it keeps happening, check the logs folder in Settings."}
        </p>
      </div>
    );
  }

  if (!booted || !status) return <BootScreen />;
  if (!status.onboarded) return <Onboarding />;

  return (
    <div className="app">
      <ModeRail
        mode={mode}
        // Clicking a mode STARTS A CHAT in it rather than re-flagging the one
        // you are reading. Re-flagging is what made this confusing: a
        // conversation full of staged edits could silently become a General
        // chat with no file tools, and nothing on screen said why.
        onStart={(m) => useConversations.getState().createNew({ mode: m })}
        settingsActive={view === "settings"}
        onOpenSettings={() => { setSettingsTab("general"); setView("settings"); }}
        hubActive={hubOpen}
        onOpenHub={() => setHubOpen(true)}
      />
      {/* A running investigation takes the full width: the run screen already
          carries its own run list on the left, and two stacked lists would be
          two competing answers to "where am I". */}
      {!researchWide && (
        <Sidebar view={view} mode={mode} setView={setView} onOpenPalette={() => setPaletteOpen(true)} />
      )}
      <div className="main-pane">
        <StatusBanner />
        <ErrorBoundary>
          {view === "settings"
            ? <SettingsView initialTab={settingsTab} onClose={() => setView("chat")} onOpenHub={() => setHubOpen(true)} />
            : mode === "research"
              // Research mode is a different screen, not a differently-configured
              // chat: an investigation has stages, lanes and a document, none of
              // which fit in a message list.
              ? <ResearchView onOpenIntegrations={() => { setSettingsTab("integrations"); setView("settings"); }} />
              : <ChatView mode={mode} />}
        </ErrorBoundary>
      </div>
      {hubOpen && <ModelHub onClose={() => setHubOpen(false)} />}
      <ApprovalModal />
      <Toasts />
      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onOpenConversation={(id) => {
            useConversations.getState().select(id);
            setView("chat");
            setPaletteOpen(false);
          }}
          onOpenSettingsTab={(tabId) => {
            setSettingsTab(tabId);
            setView("settings");
            setPaletteOpen(false);
          }}
          onOpenHub={() => { setHubOpen(true); setPaletteOpen(false); }}
          // The palette's mode entries start a chat too, same as the rail —
          // "Code mode" as a command means "work on code", and the only way to
          // do that now is in a chat that IS a Code chat.
          onSetMode={(m) => {
            useConversations.getState().createNew({ mode: m });
            setView("chat");
            setPaletteOpen(false);
          }}
          onNewChat={() => { useConversations.getState().createNew(); setView("chat"); setPaletteOpen(false); }}
        />
      )}
    </div>
  );
}
