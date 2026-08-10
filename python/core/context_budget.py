"""How much of the context window each part of a turn is allowed to take.

WHY THIS IS ITS OWN MODULE
--------------------------
Two numbers used to be hardcoded independently: `num_ctx` (8192, in
ollama_client) and the conversation history budget (24_000 characters, in
conversations.py). Nothing tied them together, so raising one silently did
nothing and raising the other silently overflowed. They are the same decision
seen from two ends — how big is the window, and how much of it may history eat
— so they are derived in one place, and it is pure so it can be tested without
a model.

WHY num_ctx IS NOT AUTO-SCALED TO THE MODEL'S WINDOW
----------------------------------------------------
Tempting, and deliberately not done. Ollama allocates the KV cache up front and
it scales linearly with num_ctx: at 8K an 8B model costs about a gigabyte extra,
at 32K it does not fit the hardware this app targets. Auto-scaling would also
change the option between calls, and Ollama reloads a resident model whenever
its options change — seconds of stall on every alternation. So the window stays
one deliberate number the user can raise if their machine can take it, and what
adapts is how the space inside it is SPENT.
"""

from __future__ import annotations

# Characters per token. Not a real tokenizer — carrying one per local model is a
# dependency for an estimate, and 4 is the standard planning number for English
# and for code. Wrong in the safe direction for code, which tokenizes denser.
CHARS_PER_TOKEN = 4

# Share of the window history may occupy. The rest holds the system prompt,
# the tool schemas, this turn's message, tool results (which in Code mode can be
# whole files), and the reply itself. History is the part that can always be
# trimmed without breaking the turn, so it is the part that gets capped.
HISTORY_SHARE = 0.35

# Never below this, or long conversations lose their thread entirely; a couple
# of exchanges is the floor for the model to know what "it" refers to.
MIN_HISTORY_CHARS = 4_000


def history_char_budget(num_ctx: int, *, share: float = HISTORY_SHARE) -> int:
    """Characters of past conversation to replay for a window of `num_ctx`.

    Returned in characters rather than tokens because that is what the caller
    can measure for free — see CHARS_PER_TOKEN for why the estimate is enough.
    """
    window = max(0, int(num_ctx or 0))
    return max(MIN_HISTORY_CHARS, int(window * share * CHARS_PER_TOKEN))
