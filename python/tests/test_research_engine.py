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
from research.providers import (
    RELEVANCE_FLOOR, SearchHit, _strip_tags, _undo_inverted_index, key_terms,
    keyword_query, relevance, root_domain,
)


class TestQueryShaping:
    """The bug: sub-questions went to keyword indexes as whole sentences, so
    the indexes matched the filler words and ranked by citation count. An ADHD
    question came back with a global asthma strategy."""

    def test_filler_is_stripped_but_subject_survives(self):
        q = "Differences in attention span between ADHD and neurotypical individuals"
        terms = key_terms(q)
        assert "attention" in terms
        assert "span" in terms
        assert "neurotypical" in terms
        # The words that poisoned the match.
        assert "differences" not in terms
        assert "individuals" not in terms
        assert "between" not in terms

    def test_acronyms_survive_the_length_floor(self):
        # Dropping ADHD for being four letters would discard the single most
        # discriminating token in the question.
        assert "adhd" in key_terms("What is ADHD")
        assert "iq" in key_terms("IQ and PTSD in adults")
        assert "ptsd" in key_terms("IQ and PTSD in adults")

    def test_terms_keep_order_and_deduplicate(self):
        assert key_terms("asthma asthma inhaler") == ["asthma", "inhaler"]

    def test_a_question_of_pure_filler_falls_back_to_the_original(self):
        # An empty query makes providers return their most-cited works, which
        # is the exact failure being fixed.
        assert keyword_query("What are the main differences") != ""

    def test_query_is_capped(self):
        long_q = " ".join(f"term{i}" for i in range(30))
        assert len(keyword_query(long_q).split()) <= 8


class TestRelevance:
    def _hit(self, title, snippet=""):
        return SearchHit(url="https://x.test/1", title=title, snippet=snippet)

    def test_the_asthma_case_scores_below_the_floor(self):
        terms = key_terms("attention span in ADHD and neurotypical individuals")
        off = self._hit(
            "Global strategy for asthma management and prevention",
            "Asthma is a serious health problem throughout the world.",
        )
        assert relevance(off, terms) < RELEVANCE_FLOOR

    def test_an_on_topic_paper_scores_well(self):
        terms = key_terms("attention span in ADHD and neurotypical individuals")
        on = self._hit(
            "Attention span in adults with ADHD",
            "We compare sustained attention in ADHD and neurotypical adults.",
        )
        assert relevance(on, terms) > RELEVANCE_FLOOR

    def test_title_matches_outweigh_body_matches(self):
        terms = key_terms("adhd attention")
        in_title = self._hit("ADHD and attention", "")
        in_body = self._hit("A study", "adhd attention are discussed here")
        assert relevance(in_title, terms) > relevance(in_body, terms)

    def test_prefix_stemming_matches_word_forms(self):
        terms = key_terms("attention deficits")
        assert relevance(self._hit("Attentional control"), terms) > 0

    def test_no_terms_means_everything_passes(self):
        # A question we could not extract terms from must not filter the world.
        assert relevance(self._hit("Anything"), []) == 1.0


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


class TestCleanText:
    """Abstracts arrive carrying the debris of every system they passed
    through. It reached the paper -- "&#13;\\nThey play a vital role" was
    rendered literally on screen -- and, worse, reached the MODEL as evidence,
    where a model shown that will happily reproduce it."""

    def test_undecoded_entities_are_decoded(self):
        from research.providers import clean_text
        out = clean_text("The oceans cover two-thirds.&#13;\\nThey play a vital role.")
        assert "&#13;" not in out
        assert "\\n" not in out
        assert out == "The oceans cover two-thirds. They play a vital role."

    def test_named_entities_too(self):
        from research.providers import clean_text
        assert clean_text("Smith &amp; Jones &mdash; a review") == "Smith & Jones — a review"

    def test_non_breaking_spaces_become_real_spaces(self):
        from research.providers import clean_text
        assert clean_text("5\xa0mg per\xa0day") == "5 mg per day"

    def test_whitespace_is_collapsed_to_one_block(self):
        from research.providers import clean_text
        assert clean_text("line one\n\n   line two\t\tend") == "line one line two end"

    def test_encoded_markup_does_not_become_live_markup(self):
        # Decoding AFTER a tag strip would turn this into a real tag that
        # nothing then removes. Decoding here must not resurrect one either.
        from research.providers import clean_text
        assert clean_text("&lt;script&gt;alert(1)&lt;/script&gt;") == "<script>alert(1)</script>"

    def test_empty_input_is_safe(self):
        from research.providers import clean_text
        assert clean_text("") == ""
        assert clean_text(None) == ""


class FakeLLM:
    """Returns whatever the test queued, ignoring the schema. Schema
    enforcement is Ollama's job; what we test here is our handling of the
    result, including the case where the model returns nothing at all."""

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat_json(self, model, messages, schema, temperature=0.0):
        # `messages` is recorded too: the length-budget tests assert on what
        # the prompt actually said, which is the only way to tell a target that
        # reached the model from one that was quietly dropped on the way.
        self.calls.append({"model": model, "schema": schema, "messages": messages})
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

    async def test_a_failed_section_says_so_instead_of_quoting_a_source(self):
        # SUPERSEDES "a failed section still yields a cited paragraph".
        #
        # The old contract was: fall back to the strongest passage, attributed.
        # In practice that pasted a source's abstract into the paper as body
        # prose in the paper's own voice -- an ADHD review opened with the
        # verbatim executive summary of a global asthma strategy. An attributed
        # verbatim extract presented as written prose is still plagiarism, and
        # it also disguised a total model failure as a working section.
        #
        # The new contract: leave a visible, honest hole.
        eng = make_engine([None])
        by_n = {1: source(1, "a.com")}
        by_n[1]["passage"] = "Asthma is a serious health problem throughout the world."
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out and out[0]["kind"] == "notice"
        assert "Asthma" not in out[0]["text"]
        assert out[0]["citations"] == []


class TestPublisherIndependence:
    """Regression: every OpenAlex hit used to carry domain='openalex.org', so
    forty papers from forty different journals collapsed into ONE evidence
    card reading '40 sources - 1 independent', and any paragraph citing two of
    them scored 'thin' because the publisher set had size 1. A search index is
    not a publisher."""

    def test_openalex_papers_do_not_share_a_publisher(self):
        from research.providers import _venue_or_host

        a = _venue_or_host("The Lancet", "https://thelancet.com/x.pdf")
        b = _venue_or_host("BMJ", "https://bmj.com/y.pdf")
        assert a != b

    def test_a_missing_venue_falls_back_to_the_host_not_the_index(self):
        from research.providers import _venue_or_host

        assert _venue_or_host("", "https://europepmc.org/article/MED/1") == "europepmc.org"

    def test_placeholder_venues_are_not_treated_as_real_publishers(self):
        from research.providers import _venue_or_host

        # "Preprint"/"Journal" are filler values several APIs return; treating
        # them as publisher names would merge unrelated papers again.
        assert _venue_or_host("Preprint", "https://arxiv.org/abs/1") == "arxiv.org"

    async def test_two_papers_in_different_journals_are_two_publishers(self):
        eng = make_engine([section([{"text": "Both agree [1][2].", "citations": [1, 2]}])])
        by_n = {1: source(1, "thelancet.com"), 2: source(2, "bmj.com")}
        by_n[1]["publisher"], by_n[2]["publisher"] = "The Lancet", "BMJ"
        out, _ = await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        assert out[0]["conf"] == "ok"

    async def test_two_papers_in_the_same_journal_are_one_publisher(self):
        eng = make_engine([section([{"text": "Same journal [1][2].", "citations": [1, 2]}])])
        by_n = {1: source(1, "thelancet.com"), 2: source(2, "thelancet.com")}
        by_n[1]["publisher"] = by_n[2]["publisher"] = "The Lancet"
        out, _ = await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        assert out[0]["conf"] == "thin"


class TestSmallModelHandling:
    def test_size_is_read_off_the_model_name(self):
        from research.engine import model_is_small

        assert model_is_small("llama3.2:3b") is True
        assert model_is_small("qwen2.5:14b") is False
        assert model_is_small("phi3.5:3.8b") is True

    def test_an_unlabelled_model_is_assumed_capable(self):
        # Degrading a good model on a bad guess is worse than letting a small
        # one try and fall back.
        from research.engine import model_is_small

        assert model_is_small("mistral-nemo") is False

    async def test_structurally_valid_but_empty_output_still_yields_a_section(self):
        """THE empty-Introduction regression. A small model returning the right
        JSON shape with empty strings inside satisfies the grammar and says
        nothing -- the fallback has to judge what survived validation, not what
        arrived."""
        eng = make_engine([section([{"text": "   ", "citations": []}])])
        by_n = {1: source(1, "a.com")}
        by_n[1]["passage"] = "A real extracted passage about the topic."
        out, _ = await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        # Still no hole in the paper -- but the filler is now Arthur saying it
        # failed, not the source's own passage wearing the paper's voice.
        assert out and out[0]["text"]
        assert out[0]["kind"] == "notice"
        assert "A real extracted passage" not in out[0]["text"]
        assert out[0]["conf"] == "unverified"

    async def test_small_models_get_the_flat_schema_and_still_get_citations(self):
        # The simple path returns one string; citations come from inline [n].
        eng = make_engine([{"heading": "H", "body": "First para [1].\n\nSecond para [2]."}])
        by_n = {1: source(1, "a.com"), 2: source(2, "b.com")}
        by_n[1]["publisher"], by_n[2]["publisher"] = "a.com", "b.com"
        out, _ = await eng._write_section("llama3.2:3b", "H", "b", list(by_n.values()), by_n)
        assert len(out) == 2
        assert out[0]["citations"] == [1]
        assert out[1]["citations"] == [2]


class TestTableValidation:
    """A comparison table is the most authoritative-looking thing a paper can
    contain, so it gets the strictest validation in the engine. Anything
    structurally unsound is dropped WHOLE rather than rendered with a hole --
    half a table invites trust in the half that survived."""

    def _by_n(self):
        return {1: source(1, "a.com"), 2: source(2, "b.com")}

    def test_a_sound_table_is_kept(self):
        from research.engine import _validate_table

        out = _validate_table({
            "caption": "Licence terms compared",
            "columns": ["Model", "Licence"],
            "rows": [["Qwen", "Apache 2.0"], ["Phi", "MIT"]],
            "row_sources": [1, 2],
        }, self._by_n())
        assert out["columns"] == ["Model", "Licence"]
        assert out["citations"] == [1, 2]

    def test_rows_citing_a_nonexistent_source_are_dropped(self):
        from research.engine import _validate_table

        out = _validate_table({
            "caption": "c", "columns": ["A", "B"],
            "rows": [["x", "y"], ["p", "q"], ["m", "n"]],
            "row_sources": [1, 99, 2],  # 99 does not exist
        }, self._by_n())
        assert len(out["rows"]) == 2
        assert out["row_sources"] == [1, 2]

    def test_ragged_rows_are_dropped_not_padded(self):
        from research.engine import _validate_table

        out = _validate_table({
            "caption": "c", "columns": ["A", "B", "C"],
            "rows": [["x", "y", "z"], ["short", "row"], ["p", "q", "r"]],
            "row_sources": [1, 1, 2],
        }, self._by_n())
        assert len(out["rows"]) == 2

    def test_a_table_reduced_below_two_rows_is_discarded_entirely(self):
        from research.engine import _validate_table

        out = _validate_table({
            "caption": "c", "columns": ["A", "B"],
            "rows": [["x", "y"], ["p", "q"]],
            "row_sources": [99, 98],  # neither exists
        }, self._by_n())
        assert out is None

    def test_a_single_column_is_not_a_table(self):
        from research.engine import _validate_table

        assert _validate_table({
            "caption": "c", "columns": ["Only"],
            "rows": [["a"], ["b"]], "row_sources": [1, 2],
        }, self._by_n()) is None

    def test_no_table_at_all_is_fine(self):
        from research.engine import _validate_table

        assert _validate_table(None, self._by_n()) is None

    def test_table_rows_mark_their_sources_used(self):
        from research.engine import _validate_table

        by_n = self._by_n()
        _validate_table({
            "caption": "c", "columns": ["A", "B"],
            "rows": [["x", "y"], ["p", "q"]], "row_sources": [1, 2],
        }, by_n)
        assert by_n[1]["used"] is True and by_n[2]["used"] is True

    async def test_a_valid_table_is_appended_to_the_section(self):
        eng = make_engine([{
            "heading": "Licences",
            "paragraphs": [{"text": "Prose [1].", "citations": [1]}],
            "table": {
                "caption": "Compared", "columns": ["Model", "Licence"],
                "rows": [["Qwen", "Apache"], ["Phi", "MIT"]],
                "row_sources": [1, 2],
            },
        }])
        by_n = {1: source(1, "a.com"), 2: source(2, "b.com")}
        out, _ = await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        assert out[-1]["kind"] == "table"
        assert out[-1]["caption"] == "Compared"


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
        # The REAL title is the last event. (Index 0 is the provisional one --
        # see the next test.)
        assert names[-1] == "research_paper"
        assert emit.of("research_paper")[-1]["title"] == "T"
        assert emit.of("research_paper")[-1]["abstract"] == "A"

    async def test_a_provisional_title_is_emitted_before_any_section(self):
        # A write that gets cut short -- stopped, timed out, disconnected --
        # never reaches the title step, which used to leave the reader looking
        # at "Untitled paper". A placeholder up front means the document always
        # has a name, and it carries NO sections so it cannot clobber the ones
        # streaming in behind it (see the research_paper handler in
        # stores/research.js).
        eng = make_engine([])
        emit = CollectingEmit()
        await eng._write_paper("do vaccines work", [source(1, "a.com")], ["one"], "m", emit)

        names = [e for e, _ in emit.events]
        assert names[0] == "research_paper"
        first = emit.of("research_paper")[0]
        assert first["title"] == "Do vaccines work"
        assert first["sections"] == []

    async def test_a_dead_model_still_produces_a_titled_paper(self):
        eng = make_engine([])  # every call returns None
        emit = CollectingEmit()
        await eng._write_paper("why is the sky blue", [source(1, "a.com")], ["one"], "m", emit)
        assert emit.of("research_paper")[-1]["title"]


class TestHeadings:
    """Sub-questions are statements of what to find out. Headings are topics.
    Printing the former where the latter belongs is what made the paper read
    as a list of sentences."""

    def test_a_sentence_becomes_a_topic(self):
        from research.engine import _short_heading
        out = _short_heading(
            "Differences in attention span and focus maintenance between ADHD "
            "and neurotypical individuals"
        )
        assert out == "Attention span and focus maintenance"

    def test_question_words_are_dropped(self):
        from research.engine import _short_heading
        assert _short_heading("How does caffeine affect sleep?") == "Caffeine affect sleep"

    def test_length_is_capped(self):
        from research.engine import _short_heading
        assert len(_short_heading(" ".join(f"w{i}" for i in range(20))).split()) <= 6

    def test_a_good_heading_is_left_alone(self):
        from research.engine import _short_heading
        assert _short_heading("Licensing and commercial use") == "Licensing and commercial use"

    def test_empty_input_does_not_crash(self):
        from research.engine import _short_heading
        assert _short_heading("") == "Untitled section"


class TestSectionFallback:
    """A section the model could not write must SAY so. It used to paste the
    top source's abstract in as body prose, which presented someone else's
    paragraph as the paper's own argument."""

    async def test_no_verbatim_passage_is_emitted_as_prose(self):
        eng = make_engine([])  # model returns nothing
        by_n = {1: source(1, "a.com")}
        by_n[1]["passage"] = "OBJECTIVE: The relationship between sex and autism..."
        out, _ = await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        assert len(out) == 1
        assert out[0]["kind"] == "notice"
        assert "OBJECTIVE" not in out[0]["text"]
        # A notice makes no claim, so it must carry no citations and must not
        # mark its source as used.
        assert out[0]["citations"] == []
        assert by_n[1]["used"] is False

    async def test_a_section_that_worked_is_untouched(self):
        eng = make_engine([section([{"text": "Real prose [1].", "citations": [1]}])])
        by_n = {1: source(1, "a.com")}
        out, _ = await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        assert out[0].get("kind") != "notice"
        assert by_n[1]["used"] is True


class TestSourceBreadth:
    async def test_the_writer_sees_far_more_than_eight_sources(self):
        # "Only 2 of 40 sources used" was mostly this number: a section cannot
        # cite what it was never shown.
        from research.engine import SOURCES_PER_SECTION
        assert SOURCES_PER_SECTION >= 12

        eng = make_engine([section([{"text": "x", "citations": [1]}])])
        by_n = {n: source(n, f"d{n}.com") for n in range(1, 21)}
        await eng._write_section("m", "H", "b", list(by_n.values()), by_n)
        sent = eng._llm.calls[-1]["messages"][-1]["content"]
        assert "[12]" in sent          # a source the old window excluded
        assert "[20]" not in sent      # still bounded


class TestLengthBudget:
    """The word target the composer sends is a real constraint, not decoration."""

    def test_no_target_means_no_instruction(self):
        from research.engine import _word_budget
        assert _word_budget(0, 3) == {"intro": 0, "theme": 0, "discussion": 0, "conclusion": 0}

    def test_themes_get_the_bulk_and_the_conclusion_the_least(self):
        from research.engine import _word_budget
        b = _word_budget(2000, 1)
        assert b["theme"] > b["discussion"] > b["intro"] > b["conclusion"]

    def test_the_split_stays_near_the_total(self):
        from research.engine import _word_budget
        n = 4
        b = _word_budget(2000, n)
        total = b["intro"] + b["conclusion"] + b["discussion"] + b["theme"] * n
        assert 1900 <= total <= 2100

    def test_page_cap_reserves_a_page_for_the_bibliography(self):
        from research.engine import WORDS_PER_PAGE, words_for_pages
        assert words_for_pages(5) == 4 * WORDS_PER_PAGE
        # A one-page paper still gets a page of body rather than zero words.
        assert words_for_pages(1) == WORDS_PER_PAGE

    async def test_the_target_reaches_the_prompt(self):
        eng = make_engine([section([{"text": "x", "citations": [1]}])])
        by_n = {1: source(1, "a.com")}
        await eng._write_section("m", "H", "brief", list(by_n.values()), by_n, words=400)
        sent = eng._llm.calls[-1]["messages"][-1]["content"]
        assert "340-460 words" in sent

    async def test_no_target_adds_nothing_to_the_prompt(self):
        eng = make_engine([section([{"text": "x", "citations": [1]}])])
        by_n = {1: source(1, "a.com")}
        await eng._write_section("m", "H", "brief", list(by_n.values()), by_n)
        sent = eng._llm.calls[-1]["messages"][-1]["content"]
        assert "words in this section" not in sent


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
