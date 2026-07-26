import React, { useEffect, useState } from "react";
import { ArrowLeft, Sliders, Cpu, ChefHat, User, Brain, Shield, Plug } from "lucide-react";
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
  ["models", "Cookbook", ChefHat],
  ["personas", "Personas", User],
  ["memory", "Memory", Brain],
  ["security", "Security", Shield],
  ["integrations", "Integrations", Plug],
];

// initialTab lets the command palette jump straight into a tab ("Settings,
// Memory" from the search results should land on Memory, not General).
export default function SettingsView({ onClose, initialTab }) {
  const [tab, setTab] = useState(initialTab || "general");

  useEffect(() => {
    if (initialTab) setTab(initialTab);
  }, [initialTab]);

  return (
    <div className="settings">
      <div className="settings-nav">
        <button className="nav-btn" onClick={onClose}><ArrowLeft size={14} /> Back</button>
        <div style={{ height: 8 }} />
        {TABS.map(([id, label, Icon]) => (
          <button key={id} className={`nav-btn ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            <Icon size={15} strokeWidth={1.7} /> {label}
          </button>
        ))}
      </div>
      <div className="settings-body">
        <div className="settings-body-inner">
          {tab === "general" && <GeneralTab />}
          {tab === "system" && <SystemTab />}
          {tab === "models" && <ModelsTab />}
          {tab === "personas" && <PersonasTab />}
          {tab === "memory" && <MemoryTab />}
          {tab === "security" && <SecurityTab />}
          {tab === "integrations" && <IntegrationsTab />}
        </div>
      </div>
    </div>
  );
}
