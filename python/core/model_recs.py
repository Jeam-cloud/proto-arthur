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

from core.model_kind import is_cloud_model

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


# Cookbook catalog (July 2026): a broader, searchable list for the Cookbook
# tab's "search any model" table, separate from MODE_RECS above (which is
# only the top 2-3 picks per mode, shown in the composer's compact menu).
#
# WHY this stays a small, hand-verified list instead of live-scraping
# ollama.com/search: Ollama has no official model-search API. A previous
# session hallucinated a tag ('llama3.3:8b') that doesn't exist and it broke
# onboarding — see the module docstring above. Scraping ollama.com's HTML
# would trade that risk for a DIFFERENT one (silently breaking whenever their
# markup changes, with no way to catch it before it ships), which is worse for
# a desktop app with no CI running against the live site. Every (model,
# params_b, size_gb) triple below was read directly off that model's real
# ollama.com/library page, same rule as MODEL_SIZES_GB. Grow this list the
# same way: check the real tags page, don't estimate.
#
# Per-entry fields, and where each one comes from:
#   model/family/params_b/size_gb/ctx  read off the real ollama.com/library page
#   org                                the lab that trained it (label only)
#   type                               one of general/code/reasoning/embed --
#                                      drives the hub's "All types" filter
#   tags/desc                          our own editorial copy for search+display
#
# NOT stored here, deliberately: tokens/sec. The mockup's hub table has a Speed
# column, but Arthur never runs a model to time it, and t/s depends on the
# user's exact GPU, quant and context length. A hardcoded number would be a
# fabricated benchmark, so the hub shows Ctx in that column instead -- a real
# fact we can actually cite.
CATALOG: list[dict] = [
    {"model": "llama3.1:8b", "family": "llama3.1", "org": "Meta", "type": "general", "params_b": 8, "size_gb": 4.9, "ctx": "128K",
     "tags": ["general", "tools"], "desc": "Meta's general-purpose model — fast tool calling, 128K context"},
    {"model": "llama3.1:70b", "family": "llama3.1", "org": "Meta", "type": "general", "params_b": 70, "size_gb": 43, "ctx": "128K",
     "tags": ["general", "tools"], "desc": "Much stronger reasoning; needs a serious GPU or a lot of RAM"},
    {"model": "qwen3:0.6b", "family": "qwen3", "org": "Qwen", "type": "general", "params_b": 0.6, "size_gb": 0.5, "ctx": "40K",
     "tags": ["general", "tools", "thinking"], "desc": "Tiny and fast — good for quick, simple replies"},
    {"model": "qwen3:1.7b", "family": "qwen3", "org": "Qwen", "type": "general", "params_b": 1.7, "size_gb": 1.4, "ctx": "40K",
     "tags": ["general", "tools", "thinking"], "desc": "A step up from 0.6b, still very light"},
    {"model": "qwen3:4b", "family": "qwen3", "org": "Qwen", "type": "general", "params_b": 4, "size_gb": 2.5, "ctx": "40K",
     "tags": ["general", "tools", "thinking"], "desc": "Compact, fast, supports tool calling — ideal under 8GB"},
    {"model": "qwen3:8b", "family": "qwen3", "org": "Qwen", "type": "reasoning", "params_b": 8, "size_gb": 5.2, "ctx": "40K",
     "tags": ["general", "research", "finance", "tools", "thinking"], "desc": "Stronger reasoning than llama3.1:8b, a bit slower"},
    {"model": "qwen3:14b", "family": "qwen3", "org": "Qwen", "type": "reasoning", "params_b": 14, "size_gb": 9.3, "ctx": "40K",
     "tags": ["research", "finance", "code", "tools", "thinking"], "desc": "Best local reasoning that still fits a 12-16GB GPU"},
    {"model": "qwen3:30b", "family": "qwen3", "org": "Qwen", "type": "reasoning", "params_b": 30, "size_gb": 19, "ctx": "256K", "moe": True,
     "tags": ["research", "code", "tools", "thinking"], "desc": "Mixture-of-experts — punches above its download size"},
    {"model": "qwen3:32b", "family": "qwen3", "org": "Qwen", "type": "reasoning", "params_b": 32, "size_gb": 20, "ctx": "40K",
     "tags": ["research", "code", "tools", "thinking"], "desc": "Large dense model — needs a high-VRAM GPU to be fast"},
    {"model": "qwen2.5-coder:0.5b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 0.5, "size_gb": 0.4, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Tiny code-completion model, near-instant on any machine"},
    {"model": "qwen2.5-coder:1.5b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 1.5, "size_gb": 1.0, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Small, fast code helper"},
    {"model": "qwen2.5-coder:3b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 3, "size_gb": 1.9, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Good balance for quick edits and explanations"},
    {"model": "qwen2.5-coder:7b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 7, "size_gb": 4.7, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Purpose-built for code — solid daily-driver size"},
    {"model": "qwen2.5-coder:14b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 14, "size_gb": 9.0, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Stronger code generation and repair, needs more headroom"},
    {"model": "qwen2.5-coder:32b", "family": "qwen2.5-coder", "org": "Qwen", "type": "code", "params_b": 32, "size_gb": 20, "ctx": "32K",
     "tags": ["code", "tools"], "desc": "Competitive with GPT-4o on code benchmarks; needs a big GPU"},
    {"model": "gemma2:2b", "family": "gemma2", "org": "Google", "type": "general", "params_b": 2, "size_gb": 1.6, "ctx": "8K",
     "tags": ["general"], "desc": "Google's efficient small model — no tool calling"},
    {"model": "gemma2:9b", "family": "gemma2", "org": "Google", "type": "general", "params_b": 9, "size_gb": 5.4, "ctx": "8K",
     "tags": ["general"], "desc": "Punches above its size on quality benchmarks"},
    {"model": "gemma2:27b", "family": "gemma2", "org": "Google", "type": "general", "params_b": 27, "size_gb": 16, "ctx": "8K",
     "tags": ["general"], "desc": "Rivals models twice its size; no tool calling"},
    {"model": "nomic-embed-text", "family": "nomic-embed-text", "org": "Nomic", "type": "embed", "params_b": 0.137, "size_gb": 0.3, "ctx": "8K",
     "tags": ["embed", "memory"], "desc": "Embedding model — this is what powers Arthur's memory search"},
]


def _base(name: str) -> str:
    """'qwen3:14b-q4_K_M' and 'qwen3:14b' are the same model to a user."""
    return name.split("-")[0]


def catalog_search(
    installed_models: list[str],
    budget_gb: float,
    query: str = "",
    type_filter: str = "all",
) -> list[dict]:
    """Model hub's search+score table. A pure function of (installed, budget,
    query, type) -> ranked rows, easy to unit test without touching Ollama or
    HTTP.

    WHY the score formula is what it is: it isn't a benchmark (Arthur never
    runs the model to measure it) — it's "how comfortably does this fit your
    declared budget", expressed as one number so results can be sorted like
    the reference screenshot. >=1.15x the download size is reserved the same
    way core/model_recs.recommendations() does, for KV-cache and context.
    Fits with headroom -> 60-100. Over budget -> drops fast, floors at 5
    (never 0, since it would still run, just slowly).

    `fit` buckets that same score into the OPTIMAL/SUITABLE/MARGINAL/UNSUITABLE
    labels the hub table shows. Computed here rather than in the UI so the label
    and the number can never drift apart.

    WHY these words: they describe the hardware relationship rather than
    grading the model. "POOR" read as a judgement on model quality, which is
    wrong -- a 70B model is not poor, it simply exceeds this machine's memory."""
    installed = {_base(m) for m in installed_models}
    q = query.strip().lower()
    tf = (type_filter or "all").strip().lower()
    rows = []
    for entry in CATALOG:
        if tf != "all" and entry["type"] != tf:
            continue
        haystack = (
            f"{entry['model']} {entry['family']} {entry['org']} "
            f"{entry['desc']} {' '.join(entry['tags'])}"
        ).lower()
        if q and q not in haystack:
            continue
        cloud = is_cloud_model(entry["model"])
        if cloud:
            # A cloud model consumes NONE of this machine's memory, so scoring
            # it against the local budget is meaningless -- and actively
            # harmful. `size_gb` for a cloud tag is 0, which the formula below
            # would turn into a perfect 100/OPTIMAL: the most enticing row in
            # the table would be the one thing in it that is not local at all.
            #
            # Given its own label instead. CLOUD is not a point on the
            # hardware-fit scale, it is a different axis entirely.
            score, fit, ratio = 0, "CLOUD", 0.0
        else:
            need = entry["size_gb"] * 1.15
            ratio = need / budget_gb if budget_gb > 0 else 99
            if ratio <= 1:
                score = round(60 + (1 - ratio) * 40)
            else:
                score = max(5, round(60 - (ratio - 1) * 80))
            if score >= 88:
                fit = "OPTIMAL"
            elif score >= 70:
                fit = "SUITABLE"
            elif score >= 45:
                fit = "MARGINAL"
            else:
                fit = "UNSUITABLE"
        rows.append({
            **entry,
            "moe": entry.get("moe", False),
            "cloud": cloud,
            "installed": _base(entry["model"]) in installed,
            "fits": cloud or ratio <= 1,
            # "runs" = where the weights actually land. Over budget means part
            # of the model spills out of VRAM into system RAM.
            "runs": "remote" if cloud else ("gpu" if ratio <= 1 else "cpu+gpu"),
            "score": score,
            "fit": fit,
        })
    # Cloud rows sink to the bottom regardless of score. They are not competing
    # on hardware fit, and putting them at the top of a local-first app's model
    # table would be the wrong default even though they would "run" fastest.
    rows.sort(key=lambda r: (not r["cloud"], r["score"]), reverse=True)
    return rows


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
