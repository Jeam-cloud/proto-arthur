"""Research mode: investigations, not messages.

A chat turn is one model call. An INVESTIGATION is a long-lived object that
plans, searches several providers, reads pages, notices its own thin spots,
searches again, and only then writes a report. That does not fit inside
chat_service (which is per-message by design), so it lives here.
"""
