"""Per-mode model recommendations, ranked against THIS machine.

Feeds the composer's model menu: switch to Finance mode and the menu shows
which models are best FOR FINANCE and whether each one actually fits this
PC — installed ones are selectable, missing-but-fitting ones get a Download
action, too-big ones are shown dimmed so users learn what an upgrade buys.

RULES THAT KEEP THIS TABLE HONEST (a wrong tag dead-ends onboarding — it
happened with a hallucinated 'llama3.3:8b'):
  * Every tag must exist in the Ollama library AND support tool calling.
    Verified July 2026: qwen3 {4b,8b,14b,32b}, llama3.1:8b.
  * Sizes are the real download sizes from ollama.com/library tags pages.
  * WHY llama3.1:8b leads tool-heavy modes (email/computer/general): it has
    no "thinking" mode, so it acts instead of monologuing — on CPU that's the
    difference between snappy and sluggish. qwen3 leads reasoning-heavy modes
    (research/finance/code) where thinking earns its latency.

Sync note: MODEL_TIERS in hardware.py is the ONBOARDING default (one model
for a fresh install); this table is the per-mode refinement. Both must obey
the verified-tags rule.
"""

from __future__ import annotations

# real download sizes (GB) from ollama.com/library — also the ~RAM needed
MODEL_SIZES_GB: dict[str, float] = {
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3:14b": 9.3,
    "qwen3:32b": 20.0,
    "llama3.1:8b": 4.9,
}

# mode -> ordered [(model, note)] — best first; the UI keeps this order
MODE_RECS: dict[str, list[tuple[str, str]]] = {
    "general": [
        ("llama3.1:8b", "Fast, reliable all-rounder — no thinking delay"),
        ("qwen3:8b", "Stronger reasoning, slightly slower"),
        ("qwen3:4b", "Lightest — best under 8GB"),
    ],
    "research": [
        ("qwen3:14b", "Best synthesis of sources on consumer hardware"),
        ("qwen3:8b", "Good balance for reading + summarizing"),
        ("llama3.1:8b", "Solid fallback, faster on CPU"),
    ],
    "finance": [
        ("qwen3:14b", "Strongest numeric reasoning of the local options"),
        ("qwen3:8b", "Good analysis at half the memory"),
        ("llama3.1:8b", "Reliable tool calls for quotes/history"),
    ],
    "code": [
        ("qwen3:14b", "Best local code generation (Qwen leads HumanEval)"),
        ("qwen3:8b", "Good code quality, mid-size"),
        ("qwen3:4b", "Quick edits and explanations on small machines"),
    ],
    "email": [
        ("llama3.1:8b", "Dependable tool calling, natural drafting tone"),
        ("qwen3:8b", "More careful drafts, a bit slower"),
    ],
    "computer": [
        ("llama3.1:8b", "Most reliable at picking the right action"),
        ("qwen3:8b", "Better at multi-step plans, slower per step"),
    ],
    "design": [
        ("qwen3:8b", "Best structured SVG output"),
        ("llama3.1:8b", "Faster, simpler graphics"),
    ],
}


def _base(name: str) -> str:
    """'qwen3:14b-q4_K_M' and 'qwen3:14b' are the same model to a user."""
    return name.split("-")[0]


def recommendations(installed_models: list[str], budget_gb: float) -> dict:
    """-> {mode: [{model, note, size_gb, fits, installed}]}
    Pure function of (installed, budget) — trivially testable, no I/O."""
    installed = {_base(m) for m in installed_models}
    out: dict[str, list[dict]] = {}
    for mode, recs in MODE_RECS.items():
        rows = []
        for model, note in recs:
            size = MODEL_SIZES_GB.get(model, 5.0)
            rows.append({
                "model": model,
                "note": note,
                "size_gb": size,
                # ~1.15x headroom: KV-cache and context live alongside weights
                "fits": budget_gb >= size * 1.15,
                "installed": _base(model) in installed,
            })
        out[mode] = rows
    return out
