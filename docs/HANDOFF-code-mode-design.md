# Handoff — Code mode, the review gate

**For:** Claude design
**From:** implementation pass, 7 August 2026
**Status:** Stage 1 backend complete and tested (341 pytest green, up from 253).
UI is functional but visually unfinished — that's what this handoff is for.
Stages 2–3 (search, shell) are not built; see *Coming next*.
**Revised 7 Aug** after the first mockup — see *Layout* and *Limits*.

---

## What this feature is

Code mode points Arthur at one folder on the user's machine and lets it work
there on its own. rian's brief, verbatim: *"basically i type in there and it
codes by itself, no need for the to-do list stuff."*

So: no task checklist, no plan-approve-execute ceremony. You type, it reads
files, edits them across as many turns as it needs, and stops. Then you review
one diff and decide.

It's a desktop app (Electron + React, plain JS, plain CSS, no component
library). Dark theme only. Design tokens are CSS variables in
`ui/src/styles/global.css` — `--bg`, `--bg2`, `--muted`, `--emph`, `--border`,
`--text`, `--tmut`, `--accent`, `--inv`, `--green`, `--red`, `--yellow`,
`--sans`, `--mono`.

---

## The one idea the whole mode rests on

Research mode only *reads* the world, so a bad run wastes time. Code mode
*writes* the user's files, so a bad run destroys work. Research's trust device
is the citation. **Code mode's trust device is the diff.**

Which is why the gate MOVED rather than disappeared. Previously every
`write_file` hit disk immediately, so every write needed its own approval
dialog — twelve interruptions for one task, each shown without the context of
the other eleven. Now writes stage into an in-memory changeset that never
touches disk, which makes them reversible, which makes them safe to run
unattended. The user approves **once**, at the diff, with everything visible at
the same time.

One decision with full context beats twelve decisions with none. If a design
change would push the user back toward approving things piecemeal, or toward
approving without looking, it's working against the mode.

---

## Layout — direction after the first mockup

The first mockup put the file tree in a **wide left column** headed *"Project"*,
between the conversation list and the chat. rian's read: *"the folder is too
much… it should be on the right side."* Agreed, and the direction is settled —
please work from this rather than the mockup.

**The tree goes on the right, narrow and collapsible.** This is also what the
shipped app already does (`.filetree` is 232px with a `border-left`, rendered
after the message list inside `.chat-body`), so the mockup was a regression, not
a proposal.

Three reasons it matters more than a preference:

1. **Three columns compete before the user has typed anything.** Sidebar, tree,
   chat. The mode's job is one conversation and one diff; the tree is a
   reference aid and should read as one.
2. **It puts the two views of the same change next to each other.** The mockup
   marks staged files green in the tree while the diff sits in the far-right
   panel — the two halves of one fact, separated by the full width of the
   window. On the right they're adjacent, and the green dot becomes a way *into*
   the diff instead of a second thing to track.
3. **Width should be earned.** A wide tree made sense when the user had to point
   the agent at files by hand. Once it navigates on its own (Stage 2 search),
   browsing matters less and *status* matters more — which is a narrow surface.

So: **the tree should become the status surface.** Green dot for a staged edit,
`+/−` on hover or in the row, click to jump to that file's diff in the panel
below. That answers open questions 1 and 2, which are now closed.

Everything on the right side of the mockup — the activity block, the *"Nothing
has reached your folder"* line, the Stop button, the composer hint — reads well
and should carry forward.

---

## What already works (don't redesign the mechanics)

| Capability | Where |
|---|---|
| One folder per conversation, inherited by new chats | `core/api/routes.py` `_conversation_workspace` |
| Path-traversal containment (`../`, absolute paths, `.git`) | `coding/paths.py` |
| Staged writes — nothing reaches disk until Apply | `coding/changeset.py` |
| `edit_file` exact-snippet replace with a uniqueness rule | `tools/coding.py` |
| Reads see pending edits (the overlay) | `coding/changeset.py` `read()` |
| Unified diffs, per-file +/− counts | `coding/changeset.py` |
| Apply all, apply a subset, discard all, discard one | `core/api/routes.py` |
| Conflict detection (file changed on disk since staging) | `coding/changeset.py` `apply()` |
| Sandboxed Python execution, CodeShield scan at approval | `tools/coding.py` `RunPythonTool` |
| File tree, click-to-insert-path into the composer | `components/code/FileTree.jsx` |

---

## The states that need visual design

Currently in `ui/src/components/code/ChangesPanel.jsx` and the
`.changes-*` / `.change` / `.diff-*` rules at the end of `global.css`.

### 1. No folder chosen
Existing `.ws-bar.empty` — green-tinted bar, *"No folder yet…"*, **Choose
folder**. Already designed, unchanged, probably fine.

### 2. Folder set, nothing staged — idle
The review panel renders nothing at all.
**Question for design:** should it? Right now there is no visible promise that
edits will be reviewable, so a first-time user has no way to know the agent
won't just overwrite their project. A persistent one-line reassurance
(*"Arthur's edits are staged for your review"*) might be worth the pixels —
this is the mode's single most important guarantee and it is currently
invisible until after the fact.

### 3. **Agent working — the biggest gap** (the mockup's best answer so far)
Currently: the same `ActivityFeed` chips every mode uses (`read_file · read
src/app.py`), streamed one per tool call.

That was designed for one or two tool calls per turn. An autonomous run makes
fifteen across several minutes, and the feed becomes a wall of near-identical
rows with no sense of progress or shape. There is deliberately **no task list**
— rian ruled it out — so the answer isn't a checklist. But *something* has to
convey "still working, here's roughly where it is."

Worth exploring: collapse repeated reads into one line (*"read 6 files"*),
promote writes/edits over reads since only writes have consequences, or show a
running count of files touched. Compare `ResearchView`'s lane rows, which solve
the same problem for a multi-minute run — though lanes have known-ahead
structure that a coding run doesn't.

**The first mockup already does most of this and it works.** *"Working in
atlas · 5 files touched · 3 edits staged · 2m 14s"* as a header, reads collapsed
into a single *"Read 6 files"* row with the filenames trailing, edits carrying
their own `+18 −6`, the in-flight row spinning with a plain-English note
(*"replacing the conflict rule"*), and — importantly — *"Nothing has reached
your folder. Every edit is staged for the review below."* sitting right next to
**Stop**. That last line is the reassurance state 2 asks for, and putting it
where the user is actually anxious (mid-run, watching files change) is better
than putting it in the idle state. Carry this forward; the open question is only
how it behaves at 40 rows rather than 5 — see *Limits*.

### 4. Changes staged, collapsed
A header strip above the composer: chevron, *"3 files to review"*, `+47` `−12`,
and the muted line *"nothing has been saved yet."*
That last phrase is doing real work — it's the only thing telling the user their
disk is still untouched. Keep the meaning if you change the words.

### 5. Changes staged, expanded — the review itself
Per-file cards: checkbox, kind icon (`FilePlus2` create / `FileMinus2` delete /
`FileDiff` modify), path in mono, `+/−` counts, per-file **Discard**, then the
unified diff. Diffs under 40 lines auto-expand; longer ones stay folded so the
action buttons don't get pushed off screen.

Footer: **Apply all** (primary) · **Discard all** · *"Applying writes these
files to your folder. Discarding leaves it untouched."*

Known weaknesses: the panel is capped at `52vh` and the whole thing lives in the
chat column, so a five-file change is a lot of scrolling in a narrow space. A
side-by-side diff, a full-height drawer, or a per-file collapse-all control are
all unexplored. Also no syntax highlighting — `Markdown.jsx` already has a
highlighter that could be reused.

### 6. Partial selection
Unticked files drop to `opacity: 0.45` and the primary button relabels to
*"Apply 2 of 5"*. Fading rather than hiding is deliberate: you need to see what
you're choosing **not** to take as clearly as what you're taking.

### 7. Applying — in flight
Currently just `disabled` buttons. No spinner, no per-file progress. Applying is
usually instant, but on a network drive it won't be.

### 8. **Conflict — needs the most attention**
When a file changed on disk since Arthur read it (usually the user editing in
their own editor), that file is skipped rather than clobbered, and the others
still apply. Right now this surfaces as a **red toast**:

> Skipped `src/app.py` — changed on disk since Arthur read it. Ask it to
> re-read and try again.

A toast is wrong for this. It's transient, it appears away from the panel, and
the affected file is still sitting in the list looking identical to the ones
that succeeded. The conflict state belongs **on the file card** — and ideally
offers the remedy inline (re-read and retry), the way `.paper-struggling` puts
a model switcher next to the sentence recommending one.

### 9. Just applied — empty again
Panel collapses and disappears; a success toast says *"Applied 3 files."* The
file tree refreshes. Nothing marks which files just changed, so the moment of
"it worked" is very quiet for an operation that just rewrote the user's project.

### 10. Staging unavailable
If the changeset is missing, the tools **refuse to write** rather than falling
back to disk. The model gets told to ask the user to pick a folder. This should
never appear in practice — but if it does, it must not read as a generic error,
because the honest message is *"nothing was written, and nothing was lost."*

---

## Design decisions taken, and why

Reverse any of these if there's a better answer — but these are the reasons.

**The panel sits between the conversation and the composer, not in a sidebar.**
The last thing between "the agent finished" and "you type again" should be the
diff. A sidebar makes reviewing an optional detour, and an optional review is
how unreviewed code gets applied.

**Discard is as easy to reach as Apply.** Same row, same size, no confirmation
step on either. A review where only approval is convenient isn't a review.

**Files are default-selected, and the store records only the boxes the user
unticked.** So a file staged *during* an open review arrives selected rather
than silently opting out of the apply.

**Diff lines are tinted AND keep their leading `+`/`-`.** Roughly 1 in 12 men
can't separate red from green reliably, and this is the screen where confusing
an addition with a deletion costs the most.

**Long diffs stay folded, short ones don't.** A 400-line rewrite that pushes the
buttons off screen gets skimmed, not read.

**Pending changes die on app restart.** Safe direction to fail: the disk is
exactly as the user left it. Design implication — don't imply durability. No
"saved drafts" language, no restore affordance.

**`run_python` kept its approval dialog.** Running code isn't reversible and a
diff can't preview it, so it doesn't qualify for the same treatment as editing.
Two different gates in one mode is a real inconsistency; it may need explaining
in the UI rather than smoothing over.

---

## Open questions

1. ~~Should the file tree mark staged files?~~ **Closed — yes.** See *Layout*.
2. ~~Is click-to-insert-path still the right job for the tree?~~ **Closed — the
   tree becomes a status surface.** See *Layout*.
3. **Where does the count live when the panel is collapsed and the user has
   scrolled away?** No persistent badge exists.
4. **Should applying be undoable?** Everything before Apply is reversible;
   after, nothing is. A post-apply undo (we hold the `before` text already)
   would close that asymmetry — but it's a backend addition, not just design.
5. **How should the tool-use limit surface when it's hit?** See *Limits* — the
   current message is a bare status line, and the failure it describes leaves
   work half-staged. That needs a designed state, not a sentence.

---

## Limits the design has to live with (or push back on)

### The tool-use cap is currently 6

`max_agent_iterations` in `core/config.py` defaults to **6** — six tool calls
per message, total. The comment explains it: *"a hard cap: a confused local
model can't loop forever."* That was proportionate when a turn meant one email
or one web search. It is now the single thing standing between this mode and
the brief it was built for.

Read the first mockup's own activity block against it: read 6 files · edit
`changes.js` · create `ReviewGate.jsx` · read 2 files · edit `global.css`. The
first row alone spends the entire budget. A real run stops mid-task with
*"Stopped: reached the tool-use limit for one message"* — having staged **half**
an edit, which is the worst place to stop, because a partial changeset looks
exactly like a complete one in the review panel.

Cowork-style work is routinely 20–50 tool calls. The cap needs to be much
higher for Code mode. It should **not** simply be raised globally: the reason
behind it is real, and the target model here is small (`llama3.1:8b` in the
mockup), so a loop is a genuine risk in a way it isn't with a frontier model.
A per-mode limit is the likely shape, leaning on the Stop button — which already
exists — as the real backstop.

**What design owns here:** if a run is allowed to make 40 tool calls, the
activity block in state 3 stops being a list and becomes a feed the user watches
for minutes. And when the cap *is* hit, the user needs to understand that the
staged changes are incomplete before they hit Apply. Neither is designed.

### Search doesn't exist yet

Until Stage 2, the agent finds files by walking the tree and guessing names.
Expect activity feeds with more reads and more dead ends than the finished mode
will produce. Don't design the feed around today's ratio of reads to edits.

---

## A bug for someone to decide on

`MODES` in `ModeRail.jsx` still marks Code as `needsDocker: true`, which
**disables the entire mode when Docker isn't running.**

That was correct when every Code tool executed in a container. It isn't
any more: read, list, edit, write, delete and the whole review flow need zero
Docker. Only `run_python` does.

This is exactly the case Research mode already reasoned through — the comment
right above it in the same file explains that Docker only sandboxes the
page-fetching step, so Research runs degraded and says so rather than locking
the user out. Code mode now has the same shape and the opposite behaviour: a
user without Docker can't edit a single file, for no reason.

Suggested fix is Research's: drop `needsDocker`, and disable/explain
`run_python` alone when Docker is down. Flagging rather than changing it,
because "which parts of a mode degrade" is a product call.

---

## Coming next (don't design around their absence)

- **Stage 2 — search.** `search_files` (content/grep) and glob. Without them the
  agent navigates by guessing filenames. Likely produces a new activity-feed
  shape (*"searched 40 files, 3 matches"*) and possibly a results surface.
- **Stage 3 — a shell tool.** Running tests and linters in the sandbox. Brings
  terminal output into the UI, which currently has nowhere to go.

Both were part of the "codes by itself" ask; Stage 1 shipped first because
everything else stages its edits through it.

---

## Security constraints the design must not undermine

**The diff is the whole trust boundary.** Every reassurance the mode offers
reduces to "you saw it before it happened." Anything that makes applying
possible without reading — an Apply button in a toast, a keyboard shortcut on
the collapsed header, an auto-apply preference — removes the property the
feature exists to provide. If auto-apply is ever wanted, it should be an
explicit, per-folder, opt-in decision made somewhere other than the review
panel.

**The tools fail closed.** No changeset means no write, never a fallback to
disk. Don't add a path that "just writes it" when staging is unavailable.
Test: `test_changeset.py::TestTools::test_write_tool_without_changeset_refuses`.

**Containment is enforced at use, not at configuration.** The folder bar
accepts a path without validating it; `safe_path()` re-checks on every single
file operation, and applies re-validate stored paths a second time. Design can
say a path looks missing (`.ws-bar.missing` already does) but must not imply the
folder bar is the security boundary. It isn't.

**Conflicts are reported, never forced.** The user's own edit always wins over
the agent's. Don't add "apply anyway" without making the consequence explicit.

---

## Files to read

```
ui/src/components/code/ChangesPanel.jsx   the review panel — the main surface
ui/src/components/code/WorkspaceBar.jsx   folder bar (already designed)
ui/src/components/code/FileTree.jsx       the tree
ui/src/stores/changes.js                  review state, apply/discard, toasts
ui/src/components/chat/ActivityFeed.jsx   the tool chips — see state 3
ui/src/styles/global.css                  .changes-*, .change, .diff-*, .ws-bar
python/coding/changeset.py                staging, diffs, conflicts (start here
                                          for the reasoning — the docstring is
                                          the design rationale)
python/tools/coding.py                    the tools and their risk levels
```

Reference for visual language: `.paper-struggling` (warning with an inline
remedy) and the Research evidence panel (provenance made visible) are the
closest existing patterns.
