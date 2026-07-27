"""Tests for the deterministic half of Research mode.

The model-driven steps are covered only through a fake LLM: what is worth
locking down here is the arithmetic the UI's honesty claims rest on -- the
confidence rule, reprint detection, and citation recovery. If any of those
silently changed, the app would keep looking trustworthy while being wrong,
which is the failure mode this whole mode exists to avoid.
"""

from __future__ import annotations

import pytest

from research.engine import ResearchEngine
from research.providers import SearchHit, _strip_tags, _undo_inverted_index, root_domain


class TestRootDomain:
    def test_strips_subdomains(self):
        assert root_domain("https://news.bbc.co.uk/story/1") == "bbc.co.uk"
        assert root_domain("https://www.reuters.com/x") == "reuters.com"

    def test_two_label_hosts_survive(self):
        assert root_domain("https://arxiv.org/abs/2401.1") == "arxiv.org"

    def test_garbage_does_not_raise(self):
        assert root_domain("not a url") == ""


class TestOpenAlexAbstract:
    def test_inverted_index_is_rebuilt_in_order(self):
        # OpenAlex ships {word: [positions]} rather than prose; getting the
        # order wrong would feed the model a shuffled abstract.
        inv = {"Open": [0], "weights": [1], "are": [2], "not": [3], "open": [4], "source": [5]}
        assert _undo_inverted_index(inv) == "Open weights are not open source"

    def test_missing_abstract_is_empty_not_none(self):
        assert _undo_inverted_index(None) == ""


class TestCrossrefAbstract:
    def test_jats_tags_are_stripped(self):
        raw = "<jats:p>Enterprises reported <jats:italic>6.2</jats:italic> weeks.</jats:p>"
        assert _strip_tags(raw) == "Enterprises reported 6.2 weeks."


class FakeLLM:
    """Returns whatever the test queued, ignoring the schema. Schema
    enforcement is Ollama's job; what we test here is our handling of the
    result, including the case where the model returns nothing at all."""

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat_json(self, model, messages, schema, temperature=0.0):
        self.calls.append({"model": model, "schema": schema})
        return self._replies.pop(0) if self._replies else None


def make_engine(replies: list) -> ResearchEngine:
    return ResearchEngine(FakeLLM(replies), vault=None, sandbox=None, embedder=None, gateway=None)


def source(n: int, domain: str, *, venue: str = "") -> dict:
    return {"n": n, "id": f"e{n}", "title": f"Source {n}", "domain": domain,
            "venue": venue, "passage": "x", "used": False}


class TestPlan:
    async def test_uses_the_models_sub_questions(self):
        eng = make_engine([{"sub_questions": ["one", "two", "three", "four"]}])
        assert await eng.plan("q", "standard", "m") == ["one", "two", "three", "four"]

    async def test_depth_caps_the_count(self):
        eng = make_engine([{"sub_questions": ["a", "b", "c", "d", "e", "f"]}])
        assert len(await eng.plan("q", "quick", "m")) == 3

    async def test_model_failure_falls_back_to_the_raw_question(self):
        # A dead plan step must not dead-end the user: one lane on the original
        # question is still a usable investigation.
        eng = make_engine([None])
        assert await eng.plan("  why is the sky blue  ", "standard", "m") == ["why is the sky blue"]


def section(paragraphs: list[dict], heading: str = "A Section") -> dict:
    return {"heading": heading, "paragraphs": paragraphs}


class TestSectionConfidence:
    """The confidence rule is ours, not the model's: two independent
    publishers -> supported, one -> thin, none -> unverified. It is computed
    per PARAGRAPH now that the output is a paper rather than a block list, but
    the arithmetic is unchanged and still never asks the model how sure it is."""

    async def test_two_independent_publishers_is_supported(self):
        eng = make_engine([section([{"text": "Both agree.", "citations": [1, 2]}])])
        by_n = {1: source(1, "a.com"), 2: source(2, "b.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[0]["conf"] == "ok"

    async def test_two_sources_from_one_publisher_is_still_thin(self):
        eng = make_engine([section([{"text": "Same outlet twice.", "citations": [1, 2]}])])
        by_n = {1: source(1, "a.com"), 2: source(2, "a.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[0]["conf"] == "thin"

    async def test_no_citations_is_unverified(self):
        eng = make_engine([section([{"text": "Asserted from nowhere.", "citations": []}])])
        by_n = {1: source(1, "a.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[0]["conf"] == "unverified"

    async def test_inline_markers_count_even_when_undeclared(self):
        # Small models routinely write "[2]" in the prose and forget to list it.
        # Both halves are unioned so the pill renders AND the confidence is right.
        eng = make_engine([section([{"text": "Claim [1] and claim [2].", "citations": []}])])
        by_n = {1: source(1, "a.com"), 2: source(2, "b.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[0]["citations"] == [1, 2]
        assert out[0]["conf"] == "ok"

    async def test_citations_to_sources_that_do_not_exist_are_dropped(self):
        eng = make_engine([section([{"text": "Hallucinated ref [9].", "citations": [9]}])])
        by_n = {1: source(1, "a.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[0]["citations"] == []
        assert out[0]["conf"] == "unverified"

    async def test_cited_sources_are_marked_used(self):
        by_n = {1: source(1, "a.com"), 2: source(2, "b.com")}
        eng = make_engine([section([{"text": "x [1]", "citations": [1]}])])
        await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert by_n[1]["used"] is True
        assert by_n[2]["used"] is False

    async def test_a_failed_section_still_yields_a_cited_paragraph(self):
        # A dead model call must not leave a hole in the paper: fall back to
        # the strongest passage, attributed, rather than an empty section.
        eng = make_engine([None])
        by_n = {1: source(1, "a.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out and out[0]["citations"] == [1]


class TestPaperAssembly:
    async def test_sections_follow_the_approved_plan_in_order(self):
        # The plan the user reviewed IS the outline: intro, one section per
        # sub-question, discussion, conclusion.
        replies = [section([{"text": "intro", "citations": []}])]           # introduction
        replies += [section([{"text": f"body {i}", "citations": [1]}]) for i in range(2)]
        replies += [section([{"text": "disc", "citations": []}])]           # discussion
        replies += [section([{"text": "concl", "citations": []}])]          # conclusion
        replies += [{"title": "A Title", "abstract": "An abstract."}]
        eng = make_engine(replies)
        emit = CollectingEmit()
        srcs = [source(1, "a.com")]
        srcs[0]["sub"] = 0

        await eng._write_paper("q", srcs, ["first theme", "second theme"], "m", emit)

        kinds = [s["kind"] for s in emit.of("research_section")]
        assert kinds == ["intro", "theme", "theme", "discussion", "conclusion"]

    async def test_title_and_abstract_arrive_last(self):
        replies = [section([{"text": "x", "citations": []}]) for _ in range(4)]
        replies += [{"title": "T", "abstract": "A"}]
        eng = make_engine(replies)
        emit = CollectingEmit()
        await eng._write_paper("q", [source(1, "a.com")], ["one theme"], "m", emit)

        names = [e for e, _ in emit.events]
        assert names[-1] == "research_paper"
        assert emit.of("research_paper")[0]["title"] == "T"

    async def test_a_dead_model_still_produces_a_titled_paper(self):
        eng = make_engine([])  # every call returns None
        emit = CollectingEmit()
        await eng._write_paper("why is the sky blue", [source(1, "a.com")], ["one"], "m", emit)
        assert emit.of("research_paper")[0]["title"]


class TestConflicts:
    async def test_pairs_outside_the_source_set_are_ignored(self):
        eng = make_engine([{"conflicts": [
            {"a": 1, "b": 2, "note": "real"},
            {"a": 1, "b": 99, "note": "invented"},
            {"a": 3, "b": 3, "note": "self"},
        ]}])
        out = await eng._find_conflicts([source(1, "a.com"), source(2, "b.com")], "m")
        assert out == [(1, 2, "real")]

    async def test_model_failure_means_no_conflicts_not_a_crash(self):
        eng = make_engine([None])
        assert await eng._find_conflicts([source(1, "a.com")], "m") == []


class TestRequery:
    async def test_falls_back_to_the_original_when_the_model_is_silent(self):
        eng = make_engine([None])
        assert await eng._requery("topic", "original sub", "m") == "original sub"

    async def test_uses_the_rewritten_query(self):
        eng = make_engine([{"query": "narrower wording"}])
        assert await eng._requery("topic", "original", "m") == "narrower wording"


class CollectingEmit:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    def of(self, name: str) -> list[dict]:
        return [d for e, d in self.events if e == name]


class TestSynthesizeOnly:
    """This is the "Write the paper now" path (stores/research.js), used when a
    run gets stopped after search finished but before the paper was written. It
    must reuse the exact same conflict+writing logic as a full run() -- no
    separate, potentially-diverging code path for the same job."""

    async def test_writes_a_paper_from_sources_with_no_search(self):
        eng = make_engine([
            {"conflicts": []},
            *[section([{"text": "x [1]", "citations": [1]}]) for _ in range(4)],
            {"title": "T", "abstract": "A"},
        ])
        emit = CollectingEmit()
        await eng.synthesize_only("q", [source(1, "a.com")], "m", emit, subs=["one theme"])
        assert emit.of("research_section")
        assert emit.of("research_paper")
        assert emit.of("done")

    async def test_empty_sources_errors_instead_of_calling_the_model(self):
        eng = make_engine([])
        emit = CollectingEmit()
        await eng.synthesize_only("q", [], "m", emit)
        assert emit.of("error")[0]["code"] == "zero_results"
        assert not emit.of("research_section")

    async def test_outline_is_recovered_when_the_sub_questions_are_gone(self):
        # After a stop the approved wording is lost, but every source records
        # which lane found it, so the SHAPE of the investigation survives.
        eng = make_engine([
            {"conflicts": []},
            *[section([{"text": "x", "citations": []}]) for _ in range(5)],
            {"title": "T", "abstract": "A"},
        ])
        emit = CollectingEmit()
        a, b = source(1, "a.com"), source(2, "b.com")
        a["sub"], b["sub"] = 0, 1
        await eng.synthesize_only("q", [a, b], "m", emit)  # no subs passed
        kinds = [s["kind"] for s in emit.of("research_section")]
        assert kinds.count("theme") == 2

    async def test_contradictions_still_get_detected(self):
        eng = make_engine([
            {"conflicts": [{"a": 1, "b": 2, "note": "disagree"}]},
            *[section([{"text": "x [1][2]", "citations": [1, 2]}]) for _ in range(4)],
            {"title": "T", "abstract": "A"},
        ])
        emit = CollectingEmit()
        srcs = [source(1, "a.com"), source(2, "b.com")]
        await eng.synthesize_only("q", srcs, "m", emit, subs=["one"])
        updated = {d["id"]: d for d in emit.of("research_source")}
        assert updated["e1"]["contradicts"] == "e2"
        assert updated["e2"]["contradicts"] == "e1"


class TestSearchHitDefaults:
    def test_web_hits_carry_no_paper_metadata(self):
        h = SearchHit(url="https://x.com/a", title="A")
        assert h.kind == "web" and h.doi == "" and h.cites == 0

    def test_pdf_fields_default_to_false_and_zero(self):
        # These back the "Np read" badge -- must never default to something
        # that LOOKS like a real page count when nothing was actually read.
        h = SearchHit(url="https://x.com/a.pdf", title="A")
        assert h.is_pdf is False
        assert h.pages == 0
