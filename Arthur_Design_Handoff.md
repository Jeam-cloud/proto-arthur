# Arthur — Design & UX Handoff

**Scanned:** Aug 12, 2026 · 66 UI files, 1,967 lines of CSS, 7 settings tabs, 21 components.

This is a review of what needs styling and interaction work. It is not a redesign — the visual system is coherent and the token layer is well built. What's missing is mostly **confirmation, feedback, and consistency**: the app is careful about safety in the backend and careless about telling you so in the front.

Everything below is grouped by severity, with the file and line to touch.

---

## P0 — Destructive actions with no confirmation

The app currently has **three different standards** for "are you sure?", and the most dangerous actions use the weakest one.

| Action | File | Current behaviour |
|---|---|---|
| Delete conversation (trash icon) | `Sidebar.jsx:135` | **Deletes instantly** |
| Delete conversation (right-click menu) | `Sidebar.jsx:92` | **Deletes instantly** |
| Delete folder | `Sidebar.jsx:96` | **Deletes instantly** |
| Delete memory | `MemoryTab.jsx:103` | **Deletes instantly** |
| Delete persona | `PersonasTab.jsx:120` | **Deletes instantly** |
| Clear security event log | `SecurityTab.jsx:121` | **Deletes instantly** |
| Remove API key | `IntegrationsTab.jsx:171` | **Deletes instantly** |
| Disconnect email | `IntegrationsTab.jsx:114` | **Deletes instantly** |
| Delete investigation | `ResearchHome.jsx:234` | ✅ In-app modal |
| Delete model | `ModelHub.jsx:151`, `ModelsTab.jsx:88` | ⚠️ `window.confirm()` |

**The fix, in order:**

1. **Extract the modal that already exists.** `ResearchHome.jsx:234-253` is the right pattern — backdrop, `.modal narrow`, title as a question, consequence sentence, Cancel + `btn danger`. Lift it into `components/common/ConfirmDialog.jsx` and take a `{ title, body, confirmLabel, danger }` prop set.

2. **Replace both `window.confirm()` calls.** A native OS dialog in a frameless dark Electron app is jarring — different font, different chrome, ignores the theme entirely.

3. **Wire the eight unprotected actions to it.** Copy should name the thing and its consequence, matching the existing voice: *"Delete "Refactor login flow"? The conversation and its messages are removed permanently."*

**One exception worth making:** deleting a chat is the highest-frequency destructive action here. Consider **undo-toast instead of a confirm** for that one — delete immediately, show "Chat deleted · Undo" for 8s. It's faster for the common case and safer for the accidental one. The backend already soft-deletes via `archive`, so this is mostly frontend. If that's too much for now, a confirm is fine — but the trash icon sitting permanently visible on every hovered row (`Sidebar.jsx:132`) is a mis-click waiting to happen either way.

---

## P0 — New chat gives no visual confirmation

`Sidebar.jsx:156` — `createNew()` inserts the conversation at the top of Recents and sets it active. Three things go wrong:

1. Every new chat is titled **"New chat"**, so creating two in a row produces two identical rows. The title is only replaced later, when the streaming `title` event fires after the first exchange.
2. The active row styling (`.conv-item.active`) is the *same* treatment used for "the chat you're reading", so nothing distinguishes *just created* from *currently open*.
3. There's no motion. The row appears with no transition, so if the sidebar is scrolled you may not notice it at all.

**Fix:**

- Add a brief highlight on insert — `.conv-item.just-created` with a 1.2s fade from `--accent-soft`. There's already a `flash` pattern in `ChangesPanel`/`stores/changes.js` doing exactly this for files; reuse the idea.
- Scroll the new row into view.
- Consider labelling untitled chats **"New chat · just now"** or showing a muted "Untitled" placeholder in italic, so two pending chats are distinguishable before their titles arrive.
- The empty state already handles this well (`EmptyChat`) — the gap is only in the sidebar.

---

## P1 — Feedback gaps

**Toasts can't be dismissed.** `Toasts.jsx` renders a plain div, `stores/toasts.js` removes it on a 5s timer. No close button, no hover-to-pause, no icon distinguishing success from error beyond colour. An error toast you want to re-read is gone; one you've read blocks the corner for 5s.
→ Add a close affordance, pause-on-hover, and a leading icon per `kind`.

**No `aria-live` anywhere in the app** (0 occurrences). Toasts, streaming replies, and status banners are all invisible to a screen reader. Toasts are the cheap win — one `role="status" aria-live="polite"` on the container.

**Saving state is inconsistent.** `PersonasTab.save()` shows a toast; `GeneralTab` settings toggles show nothing at all — you flip a switch and hope. `useSettings.update()` is optimistic with rollback on failure, so a failed save silently reverts the control. That's the worst case: it looks like it worked, then doesn't.
→ Either a subtle "Saved" tick next to changed fields, or at minimum a toast on failure.

**Buttons have no loading state** except `EmailCard` ("Verifying…") and `ModelMenu` (pull %). `PersonasTab` save/activate/delete, `SecurityTab` clear, `MemoryTab` add/delete all fire with no busy indication and no double-submit guard.

---

## P1 — Unsaved-work loss in Personas

`PersonasTab.jsx:21` — `select()` overwrites `draft` unconditionally. Edit a system prompt, click another persona chip, and the edits are gone with no warning. Same on tab-switch away from Personas.

This is the only screen in the app with a real edit-then-save form, and it's the only one that can lose work. Needs either a dirty check with a confirm, or autosave.

---

## P1 — Focus states are effectively missing

`:focus-visible` appears **0 times** in 1,967 lines of CSS. There are 9 `:focus` rules, mostly on inputs.

Every button, nav item, mode-rail icon, conversation row, and context-menu entry is keyboard-reachable and gives **no visible indication** of focus. The app is keyboard-navigable in principle and unusable that way in practice.

→ One global rule gets most of it:
```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 6px;
}
```
Then audit the handful of places where the outline clips (rail buttons, pill buttons).

Related: `tabIndex` and `role=` appear 0 times. Several clickable `<div>`s are not buttons — `Sidebar.jsx:102` (conversation row), `Sidebar.jsx:182` (folder row), `ResearchHome.jsx:214` (recent row). These can't be reached or activated by keyboard at all.

---

## P2 — Settings audit

**Working well:** `SystemTab` is the strongest screen in the app — honest about what it can't measure, tiers explained, good hierarchy. `SecurityTab`'s scanner segmented control with the warning on "off" is good design.

**Issues found:**

1. **Stale comment contradicts shipped behaviour.** `SecurityTab.jsx:97-100` says *"Off is the default because the protection did not disappear when the gate did"* — but `code_review_before_apply` now defaults to **True** (`test_auto_apply.py::test_the_default_is_on`). The user-facing copy is fine; the code comment is wrong and will mislead the next person.

2. **No search.** Seven tabs, ~30 settings, no filter. `Ctrl+K` opens the command palette but it doesn't index settings.

3. **`GeneralTab` is thin** — 4 fields, one of which ("Global hotkey") is static text, not a setting. The hotkey is not rebindable, which is the first thing people will try to change. Either make it editable or move it to a "Shortcuts" section listing all of them (`Ctrl+N`, `Ctrl+K`, `Ctrl+,`, `Esc`, `Ctrl+Shift+A`) — currently these are documented only in `App.jsx`'s keydown handler and a composer hint.

4. **No theme control.** Tokens are fully variable-driven (`:root` in `global.css`) and there is only a dark theme. Given the token structure, a light theme is genuinely cheap. At minimum, "Appearance" belongs in General.

5. **Text size slider has no preview** — you drag and the whole app reflows live, which is fine, but there's no reset-to-100% and no sample text.

6. **`IntegrationsTab` has four near-identical `KeyCard`s with inline styles** (9 inline style objects in the file). Input styling is duplicated verbatim in `EmailCard` and `KeyCard`. Should be a `.field-input` class.

7. **Model hub is a nav item that navigates away** from Settings entirely (`SettingsView.jsx:45`). The comment acknowledges this is odd. It reads as a tab and behaves as a link — give it an external-link affordance (`↗`) so the jump isn't a surprise.

---

## P2 — Dead mode in the rail

`ModeRail.jsx:59` lists **Design** mode. `TaskMode.DESIGN` exists in `python/tools/base.py:43` — but `TaskMode.DESIGN` appears **nowhere else in the backend**. No tool grants it.

Clicking Design starts a chat with zero tools that looks identical to General but isn't. Either build it, hide it, or mark it "coming soon" and disable it the way Email/Finance are disabled when unavailable (`ModeRail.jsx:71`).

---

## P2 — Styling consistency

**130 inline style objects** across the components. Worst offenders:

| File | Count |
|---|---|
| `ModelHub.jsx` | 29 |
| `ModelsTab.jsx` | 17 |
| `SystemTab.jsx` | 15 |
| `IntegrationsTab.jsx` | 9 |
| `SecurityTab.jsx` | 8 |

`SystemTab`'s hardware cards and tier list are entirely inline — that's a reusable stat-card and a reusable data-row that exist as one-offs. Same for `ModelHub`.

**Back-compat tokens still in use** (`global.css:51-60` says new code shouldn't use them): `--mid` ×3, `--surface3` ×4, `--surface2` ×1. Eight references — small enough to finish the migration and delete the aliases.

**No `@media` queries at all.** `minWidth: 900` in `electron/main.js:47` is the only thing preventing a broken layout. The sidebar is user-resizable to 420px against a 900px floor — at that combination the chat column is under 400px with the mode rail. Either raise the floor or add a breakpoint that auto-collapses the sidebar.

**No `prefers-reduced-motion`.** There's a `pulse` animation on the recording mic, `scale-in` on panels, smooth scrolling, and the new spinner — all unconditional.

---

## P3 — Smaller polish items

- **`ChangesPanel` "Discard all"** (`ChangesPanel.jsx:180`) throws away every staged edit with no confirm. Add it to the P0 list if review-before-apply is on by default, which it now is.
- **`Sidebar` collapsed state doesn't persist** — reopens expanded every launch. Same class of bug as the model-override one just fixed; `width` doesn't persist either.
- **Sidebar has no scroll shadow** — long conversation lists cut off with no visual indication there's more.
- **`ContextMenu` has no keyboard support** — arrow keys don't move between items, Enter doesn't activate. Escape works.
- **Empty states are uneven.** `MemoryTab` has a nice one; `Sidebar` has bare text ("No conversations yet."); folders have inline-styled text ("Empty, drag a chat here").
- **`ErrorBoundary`** shows a raw `error.message` in mono to the user. Fine for dev, needs a friendlier wrapper + a "copy details" button for reporting.
- **`title=` used 68 times as the only tooltip mechanism.** Native tooltips have ~1s delay, can't be styled, and don't work on the disabled mode-rail buttons — which is exactly where the explanation ("Needs Docker running") matters most. A real tooltip component would fix a genuine usability hole, not just a cosmetic one.
- **No skeleton/loading states** — `MemoryTab` shows "Loading…", `SystemTab` shows a sentence, most others show nothing while fetching.

---

## Suggested order

**Week 1 — trust and safety of touch**
1. `ConfirmDialog` component + wire the 8 unprotected deletes
2. Replace the 2 `window.confirm()` calls
3. New-chat highlight + scroll-into-view
4. Global `:focus-visible`

**Week 2 — feedback**
5. Toast dismiss + `aria-live` + icons
6. Save confirmation / failure toasts in settings
7. Button loading states
8. Personas unsaved-changes guard

**Week 3 — consistency**
9. Extract stat-card / data-row / field-input from the inline styles
10. Finish the `--mid`/`--surface*` token migration
11. Settings: shortcuts section, search, Model-hub link affordance
12. Decide on Design mode
13. `prefers-reduced-motion` + a small-window breakpoint

---

## What's already good — don't touch

- The token layer (`global.css:12-61`) is well-reasoned and documented, including the counter-intuitive `--bg` lighter than `--bg2`.
- `ChangesPanel`'s two-faced receipt/review design, and the reasoning comments explaining why the conflict notice lives on the card rather than in a toast.
- `AskUser`'s "or just type your own answer below" escape hatch.
- `SystemTab`'s refusal to invent a VRAM-usage number the backend can't measure.
- `ModeRail`'s launcher-not-switch model, and the comments explaining why Research and Code deliberately don't require Docker.
- `ErrorBoundary` existing at all, and being a class component with a comment saying why.

The codebase's comment discipline is unusually strong — most decisions carry their reasoning. Keep that up in whatever gets built from this list.
