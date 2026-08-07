# Handoff — Code mode, the right panel

**For:** Claude design
**From:** implementation pass, 7 August 2026
**Companion to:** `HANDOFF-code-mode-design.md` (read that first for the mode's
trust model). This doc covers **only** the right-hand file panel, which the
second mockup got structurally wrong.

**Reference model:** VS Code's Explorer + Source Control. Borrow its decoration
system; do not borrow its density or its chrome. Reasons for both below.

---

## What's wrong with the current mockup

Not a polish problem — the structure is off in three specific ways.

### 1. The same five files are on screen twice

The centre column header says *"5 files to review · +141 −62"*. The right panel
header says *"Files 5"* and then lists those same five files with the same
`+64 / +18 / +12 / +47` counts. Two lists, one fact, competing for the same
glance. Neither is obviously the authoritative one, so the eye keeps checking
both.

The centre column owns the *diffs*. The right panel should own **where things
are** — not restate what the diff panel already says.

### 2. `STAGED` above `UNTOUCHED` breaks the tree

This is the real error. Splitting the folder into a staged list and an untouched
list means **a file physically leaves the tree the moment Arthur edits it**.
`ReviewGate.jsx` is gone from `ui/src/components/code/`, relocated to a flat
list at the top with its path shrunk to grey subtext.

The consequence: the tree stops being the project. It becomes "the project,
minus the interesting parts", and the user loses the one thing a tree is for —
seeing a change *in context*, next to the files it sits beside and might affect.

VS Code never does this. A modified file stays exactly where it lives and gets
**decorated in place**. The flat list of changes is a *separate view* you switch
to, not a section stapled on top of the Explorer.

### 3. The `UNTOUCHED` tree isn't a tree

It's indentation and nothing else: no folder icons, no chevrons, no indent
guides, no file-type icons, no way to collapse `python/` and stop looking at it.
`atlas → python → coding → changeset.py` is four levels of pure whitespace. At
ten files it's illegible, and a real project has hundreds.

Also, the word *"UNTOUCHED"* frames the entire codebase by what Arthur hasn't
done to it, which is a strange thing to make the primary label.

---

## What it should be instead

**One tree. The whole project. Decorated in place.** That's the entire idea.

```
Files                                    5 ⌄
─────────────────────────────────────────────
⌄ 📁 atlas
  ⌄ 📁 python
    ⌄ 📁 coding
        📄 changeset.py
        📄 paths.py
  ⌄ 📁 ui / src
    ⌄ 📁 components/code
        📄 ChangesPanel.jsx           +47  M
        📄 FileTree.jsx
        📄 LegacyApprovalBar.jsx           D
        📄 ReviewGate.jsx             +64  A
        📄 WorkspaceBar.jsx
    ⌄ 📁 stores
        📄 changes.js                 +18  M
    ⌄ 📁 styles
        📄 global.css                 +12  M
      📄 package.json
```

Same rows, same ordering, same nesting whether Arthur has touched a file or not.
The only thing that changes is the decoration on the row.

### Decorations (this is the VS Code part worth copying)

| State | Filename colour | Badge | Notes |
|---|---|---|---|
| Created | `--green` | `A` | |
| Modified | `--yellow` | `M` | |
| Deleted | `--red` | `D` | strike-through the name |
| Untouched | `--text` | — | |
| Inside a folder with changes | folder name tinted `--yellow` | — | so collapsed folders still signal |

That last row matters more than it looks. VS Code propagates decoration colour
up to parent folders precisely so a collapsed tree still tells you where the
activity is. Without it, collapsing `ui/` hides the fact that four of the five
changes are in there.

**Badge letters, not just colour.** Same reasoning as the diff lines in the
review panel: roughly 1 in 12 men can't separate red from green reliably. Green
"created" and red "deleted" are the two states where confusing them costs most.

**`+47` in mono, dim, right-aligned before the badge.** Line counts are a
scanning aid; they should line up in a column and recede. The centre panel owns
the precise numbers.

### Row spec

- Height ~24px, one line, no wrapping. Ellipsis in the **middle** of long
  filenames, not the end — `ChangesPanel...jsx` beats `ChangesPa…`, because the
  extension is what identifies the file type at a glance.
- Chevron for folders, file-type icon for files (the existing `FileTree.jsx`
  already picks icons; extend rather than replace).
- Indent guides — faint 1px verticals at `--border`. Four levels of plain
  whitespace is where the mockup's tree fell apart.
- Hover: `--emph` background, full path in a tooltip.
- Selected: `--muted` background plus a 2px `--accent` left edge.

### Header

`Files` on the left, a count badge on the right showing **changed** files (`5`),
and a collapse-all control. The chevron currently in the mockup's header should
collapse the panel; use a separate icon for collapse-all-folders, as VS Code
does — they're different actions and one control can't be both.

---

## Interaction

**Click a changed file → scroll the centre panel to that file's diff and expand
it.** The mockup's own line *"Click one to jump to its diff"* is exactly right;
it just shouldn't need saying once the tree is the only list.

**Click an untouched file → nothing destructive.** Today it inserts the path
into the composer (`requestInsert` in `stores/workspace.js`). That was built
when the user had to point the agent at files by hand; now that Arthur searches
on its own it's less useful, but it's harmless and occasionally handy. Keep it
as a secondary action, not the primary one.

**Checkbox for include/exclude in the apply.** Currently only in the centre
panel. It could live here too — but a checkbox on every row in a tree of 200
untouched files is noise. Suggestion: show it on changed rows only, on hover or
when the panel is in "review" state. Open question.

**Discard on hover**, right-aligned, on changed rows only. Matches VS Code's
inline SCM actions and saves a trip to the centre panel for the common
"actually, not that file" case.

---

## What NOT to borrow from VS Code

This is a chat app that edits code, not an IDE. The failure mode is importing
IDE density into a window that's mostly a conversation.

- **No Source Control view, no view switcher, no activity bar.** There's already
  a mode rail on the far left; a second icon column would be one too many.
- **No staging area.** VS Code has *Changes* and *Staged Changes* because git
  has an index. Arthur has one changeset and one Apply. Don't reintroduce a
  two-step model the backend doesn't have — the word "staged" in the code means
  "pending review", not "git-added", and the UI shouldn't blur that.
- **No 300px default width.** VS Code's sidebar can be wide because the editor
  is the whole app. Here the conversation is. Keep the shipped **232px**,
  resizable, collapsible — and make sure collapsing it is easy, because for long
  stretches of a chat the user doesn't need it at all.
- **No breadcrumbs, no minimap, no tabs.**
- **No git status.** Arthur's changeset is not git. Showing both would imply a
  relationship that doesn't exist. (If git integration ever lands, that's a
  genuine second decoration channel and needs its own thinking.)

---

## Empty and edge states

1. **No folder chosen** — panel hidden entirely. The `.ws-bar.empty` prompt in
   the centre is the only call to action; two competing ones is worse than one.
2. **Folder chosen, nothing changed** — plain tree, no badges, count badge
   hidden. This is the majority state and it should look calm.
3. **Agent working** — decorations appear as edits stage. Worth considering a
   brief highlight on a row that just changed, so the tree shows progress
   without the user watching the activity feed. Don't animate more than once per
   file; forty pulses in a long run is a strobe.
4. **Tree truncated** — `GET /workspace/tree` is bounded (2000 nodes, depth 6)
   and returns `truncated: true`. Currently surfaced as small print. In a
   VS-Code-shaped tree it should be a row at the bottom of the affected folder:
   *"…more files not shown"*.
5. **Folder missing** — `.ws-bar.missing` already covers it; panel should empty
   rather than show a stale tree.
6. **After Apply** — decorations clear. Consider holding a faint "just applied"
   tint for a few seconds; right now the tree simply goes quiet, which is a very
   soft ending for an operation that just rewrote several files.

---

## Open questions

1. **Do untouched files earn their place at all?** The counter-argument to
   everything above: once Arthur navigates by search, the user may only ever
   care about the changed five. If so, the panel becomes a flat changed-file
   list and the tree goes away — the opposite conclusion, but at least a
   coherent one. The mockup's error was doing *both at once*. Pick one.
2. **Checkboxes in the tree, or only in the centre panel?** See above.
3. **Does the panel need a filter/search box?** VS Code has one. With
   `search_files` on the backend, the user might reasonably expect the UI to
   search too — but that's a new backend route, not just design.
4. **Where does the centre panel's file list go if the tree takes over
   navigation?** Possibly nowhere: it could become pure diffs with no per-file
   header duplication.

---

## Files to read

```
ui/src/components/code/FileTree.jsx    the tree — icons and expand state exist
ui/src/stores/workspace.js             tree data, expanded map, pendingInsert
ui/src/components/code/ChangesPanel.jsx  the centre panel it must not duplicate
ui/src/stores/changes.js               changed-file list, kinds, +/− counts
ui/src/styles/global.css               .filetree-*, .changes-*, .diff-*
core/api/routes.py  /workspace/tree    bounds, truncation, skip list
```

The data for every decoration above already exists client-side:
`useChanges().changes` gives `{path, kind, additions, deletions}` for each
staged file, and `useWorkspace().tree` gives the tree. This is a rendering
change, not a backend one.
