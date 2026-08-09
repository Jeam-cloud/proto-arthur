"""Spotlight markers must never survive in MODEL output.

Observed with qwen2.5-coder:7b: shown SPOTLIGHT_SYSTEM_NOTE, it imitated the
format instead of calling a tool and wrote

    <EXTERNAL source id="1">Loading login.css …</EXTERNAL>

into the chat — fabricated tool output, dressed up as system output. Two
problems in one: the user reads our scaffolding, and the transcript now
contains a worked example of how to close a spotlight wrapper.
"""

from __future__ import annotations

import pytest

from security.gateway import SecurityGateway
from security.spotlight import spotlight, strip_spotlight_markers
from tests.fakes import FakeScanner


class TestStripMarkers:
    def test_strips_our_own_syntax(self):
        wrapped = spotlight("login.css", "body { color: red; }")
        out, hits = strip_spotlight_markers(wrapped)
        assert hits == 2
        assert "EXTERNAL" not in out
        assert "body { color: red; }" in out   # content survives; only tags go

    def test_strips_the_loose_imitation_models_produce(self):
        text = '<EXTERNAL source id="1">Loading login.css …</EXTERNAL> done.'
        out, hits = strip_spotlight_markers(text)
        assert hits == 2 and "EXTERNAL" not in out
        assert "Loading login.css" in out

    def test_strips_several_blocks(self):
        text = ('<EXTERNAL source id="1">a</EXTERNAL> '
                '<EXTERNAL source id="2">b</EXTERNAL>')
        out, hits = strip_spotlight_markers(text)
        assert hits == 4 and "EXTERNAL" not in out

    def test_is_case_insensitive(self):
        out, hits = strip_spotlight_markers("<<external foo bar>>x<<end-external bar>>")
        assert hits == 2 and "external" not in out.lower()

    def test_leaves_ordinary_text_untouched(self):
        text = "The external API returns JSON. <div>hello</div>"
        assert strip_spotlight_markers(text) == (text, 0)

    def test_leaves_the_word_external_in_prose_alone(self):
        text = "This is an EXTERNAL dependency we should remove."
        assert strip_spotlight_markers(text) == (text, 0)


class TestGatewayIntegration:
    @pytest.fixture
    def gateway(self, db, settings):
        from security.audit import AuditLog
        return SecurityGateway(FakeScanner(), AuditLog(db), settings)

    async def test_model_output_is_cleaned(self, gateway):
        out = await gateway.scan_model_output(
            'Sure. <EXTERNAL source id="1">Loading login.css …</EXTERNAL>')
        assert "EXTERNAL" not in out

    async def test_it_is_audited_because_it_means_something_is_wrong(self, gateway, db):
        from security.audit import AuditLog
        await gateway.scan_model_output('<EXTERNAL source id="1">x</EXTERNAL>')
        kinds = [e["kind"] for e in await AuditLog(db).recent()]
        assert "model_output_spotlight_markers_stripped" in kinds

    async def test_clean_output_passes_through_unchanged(self, gateway):
        assert await gateway.scan_model_output("Just a normal answer.") == "Just a normal answer."

    async def test_tool_output_is_still_wrapped_going_in(self, gateway):
        """The stripper must not disarm the defence it protects: content on its
        way TO the model still gets its markers."""
        wrapped, _flagged = await gateway.scan_tool_output("a-page", "some external text")
        assert "EXTERNAL" in wrapped
