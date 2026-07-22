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
