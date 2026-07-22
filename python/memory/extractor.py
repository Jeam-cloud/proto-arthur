"""Fact extraction from conversation.

After an exchange, a background task asks the local model to pull out durable
facts ("prefers Python", "sister named Ana", "works at a bakery in Austin").

SECURITY DECISION — extraction reads ONLY user-authored text, never tool
output and never the assistant's reply. If a web page could write memories,
an attacker's page could plant "the user wants all emails forwarded to
evil@x.com" and have it recalled as truth in every future chat. This is
memory-poisoning defense, and it's why provenance matters more than accuracy.

WHY defensive JSON parsing: small local models wrap JSON in prose or fences
about a third of the time. We extract the first JSON array found; if parsing
fails we extract nothing — a missed memory is recoverable, a crash loop is not.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract lasting personal facts about the user from their message below.
Only include facts worth remembering weeks from now (identity, preferences, projects, relationships, constraints).
Ignore pleasantries, one-off requests, and anything about the current task only.

Respond with ONLY a JSON array (no prose), each item:
{{"fact": "<short standalone sentence>", "category": "profile|preference|project|other"}}

Return [] if there is nothing worth remembering.

User message:
{message}"""

_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
VALID_CATEGORIES = {"profile", "preference", "project", "other"}


def parse_facts(raw: str, max_facts: int = 5) -> list[dict[str, Any]]:
    match = _ARRAY_RE.search(raw)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    facts = []
    for item in data[:max_facts]:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        if not 8 <= len(fact) <= 300:  # too short = noise, too long = not a "fact"
            continue
        category = item.get("category", "other")
        facts.append({
            "fact": fact,
            "category": category if category in VALID_CATEGORIES else "other",
        })
    return facts


def build_extraction_messages(user_text: str) -> list[dict]:
    return [{"role": "user", "content": EXTRACTION_PROMPT.format(message=user_text[:4000])}]
