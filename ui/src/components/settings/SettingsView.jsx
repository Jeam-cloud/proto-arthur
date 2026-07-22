import React, { useState } from "react";
import { ArrowLeft } from "lucide-react";
import GeneralTab from "./GeneralTab";
import ModelsTab from "./ModelsTab";
import PersonasTab from "./PersonasTab";
import MemoryTab from "./MemoryTab";
import SecurityTab from "./SecurityTab";
import IntegrationsTab from "./IntegrationsTab";

const TABS = [
  ["general", "General"],
  ["models", "Models"],
  ["personas", "Personas"],
  ["memory", "Memory"],
  ["security", "Security"],
  ["integrations", "Integrations"],
];

export default function SettingsView({ onClose }) {
  const [tab, setTab] = useState("general");
  return (
    <div className="settings">
      <div className="settings-nav">
        <button className="nav-btn" onClick={onClose}><ArrowLeft size={14} /> Back</button>
        <div style={{ height: 8 }} />
        {TABS.map(([id, label]) => (
          <button key={id} className={`nav-btn ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="settings-body">
        {tab === "general" && <GeneralTab />}
        {tab === "models" && <ModelsTab />}
        {tab === "personas" && <PersonasTab />}
        {tab === "memory" && <MemoryTab />}
        {tab === "security" && <SecurityTab />}
        {tab === "integrations" && <IntegrationsTab />}
      </div>
    </div>
  );
}
