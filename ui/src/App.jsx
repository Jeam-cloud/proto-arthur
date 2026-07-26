// App shell: boot -> onboarding -> main layout. Also owns global keyboard
// shortcuts (tracker p4t4) and the always-mounted overlays (approval modal,
// toasts): approvals must interrupt whatever screen the user is on.
import React, { useEffect, useState } from "react";
import { initApi } from "./api/client";
import { useBackend } from "./stores/backend";
import { useConversations } from "./stores/conversations";
import { useSettings } from "./stores/settings";
import ModeRail from "./components/ModeRail";
import Sidebar from "./components/Sidebar";
import CommandPalette from "./components/CommandPalette";
import ChatView from "./components/chat/ChatView";
import SettingsView from "./components/settings/SettingsView";
import Onboarding from "./components/Onboarding";
import ApprovalModal from "./components/ApprovalModal";
import Toasts from "./components/common/Toasts";
import StatusBanner from "./components/common/StatusBanner";
import BootScreen from "./components/common/BootScreen";
import ErrorBoundary from "./components/common/ErrorBoundary";

export default function App() {
  const [view, setView] = useState("chat"); // chat | settings
  const [mode, setMode] = useState("general"); // lifted: rail, sidebar footer, chat header and composer all read it
  const [settingsTab, setSettingsTab] = useState("general");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState(null);
  const { phase, status, startPolling } = useBackend();

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
      if (e.key === "Escape") {
        if (paletteOpen) setPaletteOpen(false);
        else setView("chat");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen]);

  if (bootError || phase === "failed") {
    return (
      <div className="boot">
        <div className="logo">A</div>
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
        setMode={setMode}
        settingsActive={view === "settings"}
        onOpenSettings={() => { setSettingsTab("general"); setView("settings"); }}
      />
      <Sidebar view={view} mode={mode} setView={setView} onOpenPalette={() => setPaletteOpen(true)} />
      <div className="main-pane">
        <StatusBanner />
        <ErrorBoundary>
          {view === "chat"
            ? <ChatView mode={mode} setMode={setMode} />
            : <SettingsView initialTab={settingsTab} onClose={() => setView("chat")} />}
        </ErrorBoundary>
      </div>
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
        />
      )}
    </div>
  );
}
