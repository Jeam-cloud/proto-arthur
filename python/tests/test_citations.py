"""Citation formatting.

These are the tests that make the feature trustworthy. A citation is the one
thing in a research paper that is either right or wrong with no middle
ground, and because Arthur formats them deterministically from provider
metadata (rather than asking a model to write them), they can actually be
pinned down with known inputs and expected strings.

If a style below is wrong, it is wrong for every paper the app will ever
produce -- so the assertions are written against the real rules, not against
whatever the code currently happens to emit.
"""

from __future__ import annotations

from research import citations


def paper(**over) -> dict:
    src = {
        "n": 1,
        "id": "e1",
        "kind": "paper",
        "title": "Open Weights Are Not Open Source",
        "authors_list": [
            {"given": "Adaeze", "family": "Okonjo"},
            {"given": "Rafael", "family": "Vasquez"},
            {"given": "Mira", "family": "Lindqvist"},
        ],
        "year": "2026",
        "venue": "FAccT",
        "doi": "10.1145/3719284.4402117",
        "url": "https://example.org/paper",
        "domain": "openalex.org",
    }
    src.update(over)
    return src


def web(**over) -> dict:
    src = {
        "n": 2,
        "id": "e2",
        "kind": "web",
        "title": "Llama 3.1 Community License Agreement",
        "authors_list": [],
        "year": "2025",
        "venue": "",
        "doi": "",
        "url": "https://llama.meta.com/license",
        "domain": "llama.meta.com",
    }
    src.update(over)
    return src


class TestInText:
    def test_apa_is_author_comma_year(self):
        assert citations.in_text(paper(), "apa") == "(Okonjo et al., 2026)"

    def test_mla_has_no_year(self):
        # MLA in-text is author (+page). We have no reliable page numbers, so
        # author-only is correct -- inventing a page would be worse.
        assert citations.in_text(paper(), "mla") == "(Okonjo et al.)"

    def test_chicago_omits_the_comma(self):
        assert citations.in_text(paper(), "chicago") == "(Okonjo et al. 2026)"

    def test_ieee_is_numeric(self):
        assert citations.in_text(paper(n=7), "ieee") == "[7]"

    def test_apa_uses_an_ampersand_for_two_authors(self):
        # APA 7 §8.17: ampersand inside parentheses, "and" in running text.
        # Every other author-date style spells it out.
        two = paper(authors_list=[
            {"given": "Adaeze", "family": "Okonjo"},
            {"given": "Rafael", "family": "Vasquez"},
        ])
        assert citations.in_text(two, "apa") == "(Okonjo & Vasquez, 2026)"

    def test_chicago_spells_out_and_for_two_authors(self):
        two = paper(authors_list=[
            {"given": "Adaeze", "family": "Okonjo"},
            {"given": "Rafael", "family": "Vasquez"},
        ])
        assert citations.in_text(two, "chicago") == "(Okonjo and Vasquez 2026)"

    def test_single_author_has_no_et_al(self):
        one = paper(authors_list=[{"given": "Adaeze", "family": "Okonjo"}])
        assert citations.in_text(one, "apa") == "(Okonjo, 2026)"

    def test_authorless_web_page_falls_back_to_title(self):
        # Most of the web has no byline. Every author-date style prescribes a
        # shortened title for this case rather than "Anonymous".
        out = citations.in_text(web(), "apa")
        assert "Llama 3.1 Community License" in out
        assert "2025" in out

    def test_missing_year_becomes_nd(self):
        assert "n.d." in citations.in_text(paper(year=""), "apa")

    def test_unknown_style_falls_back_to_apa_not_a_crash(self):
        assert citations.in_text(paper(), "klingon") == "(Okonjo et al., 2026)"


class TestReferences:
    def test_apa_uses_initials_and_ampersand(self):
        out = citations.reference(paper(), "apa")
        assert out.startswith("Okonjo, A., Vasquez, R., & Lindqvist, M. (2026).")
        assert "https://doi.org/10.1145/3719284.4402117" in out

    def test_mla_spells_the_first_name_and_uses_et_al(self):
        out = citations.reference(paper(), "mla")
        assert out.startswith("Okonjo, Adaeze, et al.")
        assert '"Open Weights Are Not Open Source."' in out

    def test_chicago_puts_year_after_the_author(self):
        out = citations.reference(paper(), "chicago")
        assert out.startswith("Okonjo, Adaeze, Rafael Vasquez, and Mira Lindqvist. 2026.")

    def test_harvard_uses_available_at(self):
        assert "Available at:" in citations.reference(paper(), "harvard")

    def test_ieee_leads_with_the_number_and_initials_first(self):
        out = citations.reference(paper(n=3), "ieee")
        assert out.startswith("[3] A. Okonjo, R. Vasquez, and M. Lindqvist,")

    def test_doi_is_preferred_over_a_bare_url(self):
        # A DOI is stable; a landing-page URL rots. Every style guide says use
        # the DOI when one exists.
        assert "doi.org" in citations.reference(paper(), "apa")

    def test_web_source_without_doi_uses_its_url(self):
        assert "llama.meta.com/license" in citations.reference(web(), "apa")


class TestReferenceList:
    def test_author_date_styles_sort_alphabetically(self):
        a = paper(n=1, authors_list=[{"given": "Zoe", "family": "Zhang"}])
        b = paper(n=2, authors_list=[{"given": "Adam", "family": "Adler"}])
        out = citations.reference_list([a, b], "apa")
        assert out[0]["text"].startswith("Adler")

    def test_ieee_keeps_citation_order(self):
        # IEEE numbers ARE the order, so alphabetising would make [1] not first.
        a = paper(n=1, authors_list=[{"given": "Zoe", "family": "Zhang"}])
        b = paper(n=2, authors_list=[{"given": "Adam", "family": "Adler"}])
        out = citations.reference_list([a, b], "ieee")
        assert out[0]["n"] == 1

    def test_entries_keep_their_source_id_for_ui_linking(self):
        out = citations.reference_list([paper()], "apa")
        assert out[0]["id"] == "e1"


class TestRenderInText:
    def test_markers_are_replaced_with_formatted_citations(self):
        by_n = {1: paper()}
        out = citations.render_in_text("Licences differ [1].", by_n, "apa")
        assert out == "Licences differ (Okonjo et al., 2026)."

    def test_unknown_marker_is_left_visible_rather_than_silently_dropped(self):
        out = citations.render_in_text("Claim [9].", {1: paper()}, "apa")
        assert "[9]" in out

    def test_adjacent_duplicates_collapse(self):
        txt = "Both agree (Okonjo et al., 2026) (Okonjo et al., 2026)."
        assert citations.dedupe_adjacent(txt) == "Both agree (Okonjo et al., 2026)."


class TestCustomStyle:
    async def test_falls_back_to_apa_when_the_model_is_unavailable(self):
        class Dead:
            async def chat_json(self, *a, **k):
                raise RuntimeError("ollama down")

        out = await citations.custom_reference_list(Dead(), "m", [paper()], "however you like")
        assert out[0]["text"].startswith("Okonjo, A.")

    async def test_hallucinated_source_numbers_are_dropped(self):
        class Inventive:
            async def chat_json(self, *a, **k):
                return {"references": [
                    {"n": 1, "text": "real entry"},
                    {"n": 99, "text": "a source that does not exist"},
                ]}

        out = await citations.custom_reference_list(Inventive(), "m", [paper()], "x")
        assert [e["n"] for e in out] == [1]

    async def test_mostly_empty_output_falls_back_rather_than_shipping_a_gap(self):
        class Lazy:
            async def chat_json(self, *a, **k):
                return {"references": [{"n": 1, "text": "only one of four"}]}

        four = [paper(n=i, id=f"e{i}") for i in (1, 2, 3, 4)]
        out = await citations.custom_reference_list(Lazy(), "m", four, "x")
        assert len(out) == 4  # fell back to APA for all of them


class TestNameSplitting:
    def test_display_names_split_on_the_last_token(self):
        from research.providers import split_name

        assert split_name("Adaeze Okonjo") == {"given": "Adaeze", "family": "Okonjo"}

    def test_single_token_is_treated_as_a_family_name(self):
        from research.providers import split_name

        assert split_name("Plato") == {"given": "", "family": "Plato"}

    def test_empty_input_does_not_crash(self):
        from research.providers import split_name

        assert split_name("") == {"given": "", "family": ""}
