"""Hardware detection -> model recommendation.

WHY config-driven tiers: the "best small model" changes every few months
(mid-2026: qwen3.5:4b for 8GB machines, llama3.3:8b as all-rounder). Tiers
live in one table so an app update can revise recommendations without
touching logic. GPU VRAM matters more than system RAM for speed, so we check
for an NVIDIA GPU via nvidia-smi (no extra dependency; pynvml would be one).
"""

from __future__ import annotations

import platform
import shutil
import subprocess

import psutil

# (min_ram_gb, recommended chat model, note)
# Every tag here is verified to exist in the Ollama library AND to carry the
# "tools" capability (required for the agent loop). Two rules learned the hard
# way: a size must actually be published (Llama 3.3 ships 70B ONLY — there is
# no llama3.3:8b), and don't invent version numbers (there is no qwen3.5).
# The 8GB tier uses llama3.1:8b on purpose: it's a rock-solid tool-caller with
# no "thinking" mode, so on a CPU-only machine it answers fast instead of
# burning tokens reasoning out loud. qwen3 (which has thinking mode) is used
# at the higher tiers where a GPU is likelier to absorb that cost.
MODEL_TIERS = [
    (32, "qwen3:32b", "Large model — best quality your machine can run"),
    (16, "qwen3:14b", "Strong reasoning and tool calling"),
    (8, "llama3.1:8b", "Best all-round balance for 8–16GB machines"),
    (0, "qwen3:4b", "Compact, fast, supports tool calling — ideal under 8GB"),
]

EMBED_MODEL = "nomic-embed-text"  # always required for memory/RAG


def _gpu_info() -> dict | None:
    """Best-effort NVIDIA check. AMD/Intel iGPU users just get RAM-based tiers."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        name, vram_mb = out.stdout.strip().split("\n")[0].split(", ")
        return {"name": name, "vram_gb": round(int(vram_mb) / 1024, 1)}
    except Exception:
        return None


def detect() -> dict:
    ram_gb = round(psutil.virtual_memory().total / 1024**3, 1)
    gpu = _gpu_info()
    # Effective budget: VRAM if there's a discrete GPU (model must fit in it to
    # be fast), otherwise system RAM with headroom for the OS.
    budget = gpu["vram_gb"] if gpu else max(ram_gb - 4, 2)

    chat_model, note = MODEL_TIERS[-1][1], MODEL_TIERS[-1][2]
    for min_gb, model, tier_note in MODEL_TIERS:
        if budget >= min_gb:
            chat_model, note = model, tier_note
            break

    return {
        "os": platform.system(),
        "cpu_count": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
        "ram_gb": ram_gb,
        "gpu": gpu,
        "recommendation": {
            "chat_model": chat_model,
            "embed_model": EMBED_MODEL,
            "note": note,
        },
    }
