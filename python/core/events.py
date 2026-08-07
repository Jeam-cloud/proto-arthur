"""SSE event names — one shared vocabulary between backend and UI.

The stream is a typed protocol, not a raw token feed: the UI renders tool
activity, approval prompts and memory chips from these events. Keeping the
names in one module (mirrored in ui/src/api/sse.js) prevents the classic
"backend renamed an event, UI silently ignores it" bug.
"""

TOKEN = "token"                    # {content}
STATUS = "status"                  # {text}            transient progress line
TOOL_START = "tool_start"          # {name, summary}
TOOL_RESULT = "tool_result"        # {name, ok, summary, flagged}
APPROVAL_REQUIRED = "approval_required"  # {id, tool, summary, args_preview}
APPROVAL_RESOLVED = "approval_resolved"  # {id, approved}
MEMORY_USED = "memory_used"        # {items: [{id, text}]}
TITLE = "title"                    # {conversation_id, title}
ERROR = "error"                    # {code, message}
DONE = "done"                      # {message_id, conversation_id}

# ---- code mode ----
# The turn staged file edits. Carries only TOTALS ({files, additions,
# deletions}) — the diffs themselves are fetched over HTTP, not pushed down the
# stream, because a multi-file diff is far too big to belong in an SSE frame
# and the user may never open the panel.
CHANGES_UPDATED = "changes_updated"  # {files, additions, deletions}
# The turn stopped because it ran out of tool calls, not because it finished.
# Its own event rather than a `status` line the UI would have to string-match:
# in Code mode this means the changeset on screen is PARTIAL, and a review panel
# that cannot tell the difference will happily apply half a change.
TOOL_LIMIT = "tool_limit"          # {mode}

# ---- research mode ----
# An investigation is not a token stream, so it needs its own vocabulary. Each
# event is a whole object the UI can render on its own: lanes redraw one row,
# sources append one card, blocks append one paragraph. WHY whole objects and
# not deltas: a research run lasts minutes and the user may open the window
# mid-run -- idempotent "here is the current state of lane 3" survives that,
# a delta stream does not.
RESEARCH_LANE = "research_lane"      # {id, text, state, read, of, srcs, pass}
RESEARCH_SOURCE = "research_source"  # full source card (see research/engine.py)
RESEARCH_GAP = "research_gap"        # {ids: [lane_id], note}  second pass starting
# The paper is written section by section, so sections stream in as they are
# finished (RESEARCH_SECTION) and the completed paper arrives last with its
# title and abstract (RESEARCH_PAPER). Sections carry `order`, so the UI can
# place them correctly without relying on arrival order.
RESEARCH_SECTION = "research_section"  # {id, kind, heading, order, paragraphs[]}
RESEARCH_PAPER = "research_paper"      # {title, abstract, question, sections[]}
# The writer is failing repeatedly and the user needs to change something.
# Carries WHICH thing: a truncated context is a setting, an overwhelmed model
# is a download. Sent once per run, after the paper, so it never interrupts
# writing that might still recover.
RESEARCH_MODEL_STRUGGLING = "research_model_struggling"  # {failed, total, context_full, model}
