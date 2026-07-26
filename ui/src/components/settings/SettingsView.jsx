import React, { useEffect, useState } from "react";
import { ArrowLeft, Sliders, Cpu, Library, Box, User, Brain, Shield, Plug } from "lucide-react";
import GeneralTab from "./GeneralTab";
import SystemTab from "./SystemTab";
import ModelsTab from "./ModelsTab";
import PersonasTab from "./PersonasTab";
import MemoryTab from "./MemoryTab";
import SecurityTab from "./SecurityTab";
import IntegrationsTab from "./IntegrationsTab";

const TABS = [
  ["general", "General", Sliders],
  ["system", "This computer", Cpu],
  ["models", "Models", Box],
  ["personas", "Personas", User],
  ["memory", "Memory", Brain],
  ["security", "Security", Shield],
  ["integrations", "Integrations", Plug],
];

// initialTab lets the command palette jump straight into a tab ("Settings,
// Memory" from the search results should land on Memory, not General).
//
// "Model hub" sits in this nav list but is NOT a tab: clicking it leaves
// Settings entirely for the top-level hub page. It's listed here anyway
// because that's where people look for it, and a nav entry that navigates
// away is less surprising than one that's missing.
export default function SettingsView({ onClose, initialTab, onOpenHub }) {
  const [tab, setTab] = useState(initialTab || "general");

  useEffect(() => {
    if (initialTab) setTab(initialTab);
  }, [initialTab]);

  return (
    <div className="settings">
      <div className="settings-nav">
        <button className="nav-btn" onClick={onClose}><ArrowLeft size={14} /> Back</button>
        <div style={{ height: 8 }} />
        {TABS.slice(0, 2).map(([id, label, Icon]) => (
          <button key={id} className={`nav-btn ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            <Icon size={15} strokeWidth={1.7} /> {label}
          </button>
        ))}
        <button className="nav-btn" onClick={onOpenHub}>
          <Library size={15} strokeWidth={1.7} /> Model hub
        </button>
        {TABS.slice(2).map(([id, label, Icon]) => (
          <button key={id} className={`nav-btn ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            <Icon size={15} strokeWidth={1.7} /> {label}
          </button>
        ))}
      </div>
      <div className="settings-body">
        <div className="settings-body-inner">
          {tab === "general" && <GeneralTab />}
          {tab === "system" && <SystemTab />}
          {tab === "models" && <ModelsTab onOpenHub={onOpenHub} />}
          {tab === "personas" && <PersonasTab />}
          {tab === "memory" && <MemoryTab />}
          {tab === "security" && <SecurityTab />}
          {tab === "integrations" && <IntegrationsTab />}
        </div>
      </div>
    </div>
  );
}
