// External connections. Keys are write-only by design: they go INTO the OS
// credential vault and the API will only ever say "configured", there is no
// endpoint that returns a stored key.
import React, { useState } from "react";
import { api } from "../../api/client";
import { useBackend } from "../../stores/backend";
import { useConfirm } from "../../stores/confirm";
import { useSettings } from "../../stores/settings";
import { useToasts } from "../../stores/toasts";

export default function IntegrationsTab() {
  const { status, refreshStatus } = useBackend();
  const secrets = status?.secrets || {};

  // (Microsoft 365 OAuth removed along with MS Graph — it needed an Azure app
  // registration that was never completed, so the button could not succeed.
  // `busy`, `pushToast` and the `api` import went with it: they existed only
  // to drive that button.)

  return (
    <>
      <h2>Integrations</h2>
      <div className="section-sub">
        Each connection is scoped to the minimum it needs. Keys live in your OS's secure
        credential vault, never in files, and never inside tool sandboxes.
      </div>

      <EmailCard configured={!!secrets.email_password} onSaved={refreshStatus} />

      <KeyCard
        title="Tavily, web research"
        hint="Free tier at tavily.com. Powers Research mode's web search."
        name="tavily"
        configured={!!secrets.tavily}
        onSaved={refreshStatus}
      />

      <h2 style={{ fontSize: 14, margin: "18px 0 4px" }}>Optional cloud models (BYOK)</h2>
      <div className="section-sub" style={{ marginBottom: 10 }}>
        Route a single request to a hosted model when you want more quality than the local
        model gives. Strictly opt-in per message, chat-only (cloud models never get tools),
        and Arthur shows a badge whenever a reply came from the cloud.
      </div>

      <KeyCard title="OpenAI API key" name="byok_openai" configured={!!secrets.byok_openai} onSaved={refreshStatus} />
      <KeyCard title="Anthropic API key" name="byok_anthropic" configured={!!secrets.byok_anthropic} onSaved={refreshStatus} />
    </>
  );
}

// Email via app password: the 2-minute path (how Odysseus/OpenClaw do email).
// Gmail: myaccount.google.com -> Security -> 2-Step Verification -> App passwords.
// Hosts auto-fill from the address domain (gmail/yahoo/icloud presets in the
// backend); anything exotic can be typed manually.
function EmailCard({ configured, onSaved }) {
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const pushToast = useToasts((s) => s.push);
  const savedAddress = useSettings((s) => (s.values && s.values.email_address) || "");
  const reloadSettings = useSettings((s) => s.load);
  const ask = useConfirm((s) => s.ask);

  const disconnect = () => {
    ask({
      title: "Disconnect email?",
      body: `The app password for ${savedAddress} is deleted from your system's credential `
        + "vault. Email mode stops working until you connect an account again, and you will "
        + "need to paste a new app password — the old one cannot be read back.",
      confirmLabel: "Disconnect",
      onConfirm: async () => {
        try {
          await api.del("/integrations/email");
          await reloadSettings();
          pushToast("Email disconnected. The password was removed from your system's vault.", "success");
          onSaved();
        } catch (e) {
          pushToast(e.message, "error");
        }
      },
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/settings", { email_address: address.trim() });
      await api.put("/secrets", { name: "email_password", value: password });
      // Verify by actually logging in, a wrong password should fail HERE,
      // with a plain explanation, not later mid-conversation.
      const check = await api.post("/integrations/email/test");
      if (check.ok) {
        setPassword(""); setAddress("");
        pushToast("Verified, email is ready. Try: “email someone@example.com that …”", "success");
      } else {
        pushToast(check.error, "error");
      }
      await reloadSettings();
      onSaved();
    } catch (e) {
      pushToast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <div className="card-row">
        <div className="grow">
          <div className="card-title">Email, any provider (app password)</div>
          <div className="card-sub">
            Fastest setup: your address + an app password. Gmail/Yahoo/iCloud: create one
            in your account's security settings (needs 2-step verification on).
            Sending always shows you the draft first.
          </div>
        </div>
        {configured && savedAddress && <span className="pill ok">{savedAddress}</span>}
      </div>
      {configured && savedAddress ? (
        <div className="field-row" style={{ marginTop: 10 }}>
          <span className="grow" style={{ fontSize: 12.5, color: "var(--mid)" }}>
            Connected and verified. Disconnecting removes the password from your system's vault.
          </span>
          <button className="btn danger" onClick={disconnect}>Disconnect</button>
        </div>
      ) : (
        <div className="field-row" style={{ marginTop: 10 }}>
          <input
            type="text" className="grow" placeholder="you@gmail.com"
            value={address} onChange={(e) => setAddress(e.target.value)}
            style={{ background: "var(--surface3)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 11px", fontSize: 13, outline: "none" }}
          />
          <input
            type="password" className="grow" placeholder="app password"
            value={password} onChange={(e) => setPassword(e.target.value)}
            style={{ background: "var(--surface3)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 11px", fontSize: 13, outline: "none" }}
          />
          <button className="btn" disabled={saving || !address.includes("@") || password.length < 8} onClick={save}>
            {saving ? "Verifying…" : "Connect"}
          </button>
        </div>
      )}
    </div>
  );
}

function KeyCard({ title, hint, name, configured, onSaved }) {
  const [value, setValue] = useState("");
  const pushToast = useToasts((s) => s.push);
  const ask = useConfirm((s) => s.ask);

  const save = async () => {
    try {
      await api.put("/secrets", { name, value });
      setValue("");
      pushToast("Key stored in your OS vault.", "success");
      onSaved();
    } catch (e) { pushToast(e.message, "error"); }
  };

  const remove = () => {
    ask({
      title: `Remove the ${title.replace(/ API key$/, "")} key?`,
      // Specifically flagged as unreadable: a key store that only ever writes
      // is the right design, but it means "remove" is not reversible by
      // looking the value up again, and the user is the only one who can know
      // whether they still have it.
      body: "It is deleted from your system's credential vault. Arthur cannot read keys "
        + "back, so you will need the original again to reconnect.",
      confirmLabel: "Remove key",
      onConfirm: async () => {
        try {
          await api.del(`/secrets/${name}`);
          onSaved();
        } catch (e) { pushToast(e.message, "error"); }
      },
    });
  };

  return (
    <div className="card">
      <div className="card-row">
        <div className="grow">
          <div className="card-title">{title}</div>
          {hint && <div className="card-sub">{hint}</div>}
        </div>
        {configured && <span className="pill ok">configured</span>}
      </div>
      <div className="field-row" style={{ marginTop: 10 }}>
        <input
          type="password" className="grow" placeholder={configured ? "Replace key…" : "Paste key…"}
          value={value} onChange={(e) => setValue(e.target.value)}
          style={{ background: "var(--surface3)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 11px", fontSize: 13, outline: "none" }}
        />
        <button className="btn" disabled={value.length < 8} onClick={save}>Save</button>
        {configured && <button className="btn danger" onClick={remove}>Remove</button>}
      </div>
    </div>
  );
}
