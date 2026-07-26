"""Mode-aware recommendation table — the contract behind the model menu."""

from core.model_recs import CATALOG, MODE_RECS, MODEL_SIZES_GB, catalog_search, recommendations
from tools.base import TaskMode

CATALOG_TYPES = {"general", "code", "reasoning", "embed"}


class TestTableIntegrity:
    def test_every_mode_has_recommendations(self):
        assert set(MODE_RECS) == {m.value for m in TaskMode}
        assert all(len(recs) >= 2 for recs in MODE_RECS.values())

    def test_every_recommended_model_has_a_size(self):
        """A model without a size would get a guessed fit — keep the table honest."""
        for recs in MODE_RECS.values():
            for model, _note in recs:
                assert model in MODEL_SIZES_GB, f"{model} missing from MODEL_SIZES_GB"


class TestRecommendations:
    def test_fits_respects_budget_with_headroom(self):
        # boundary sits between llama3.1:8b (4.9*1.15≈5.64) and qwen3:8b (5.2*1.15≈5.98)
        out = recommendations([], budget_gb=5.8)
        by_model = {r["model"]: r for r in out["general"]}
        assert by_model["qwen3:4b"]["fits"] is True
        assert by_model["llama3.1:8b"]["fits"] is True
        assert by_model["qwen3:8b"]["fits"] is False

    def test_big_machine_fits_everything(self):
        out = recommendations([], budget_gb=32.0)
        assert all(r["fits"] for recs in out.values() for r in recs)

    def test_installed_matches_ignore_quant_suffix(self):
        out = recommendations(["qwen3:14b-q4_K_M", "llama3.1:8b"], budget_gb=16)
        finance = {r["model"]: r for r in out["finance"]}
        assert finance["qwen3:14b"]["installed"] is True
        assert finance["llama3.1:8b"]["installed"] is True
        assert finance["qwen3:8b"]["installed"] is False

    def test_order_preserved_best_first(self):
        out = recommendations([], budget_gb=16)
        assert out["code"][0]["model"] == MODE_RECS["code"][0][0]


class TestCatalogIntegrity:
    """The Model hub renders these fields directly. A missing key isn't a
    styling bug, it's a blank cell in the table, so assert on all of them."""

    def test_every_entry_has_the_fields_the_hub_renders(self):
        required = {"model", "family", "org", "type", "params_b", "size_gb", "ctx", "tags", "desc"}
        for entry in CATALOG:
            missing = required - set(entry)
            assert not missing, f"{entry.get('model', entry)} missing {missing}"

    def test_types_are_from_the_filter_dropdown(self):
        """The hub's type <select> has fixed options; an entry with a type
        outside that set would be unreachable through the filter."""
        for entry in CATALOG:
            assert entry["type"] in CATALOG_TYPES, f"{entry['model']} has unknown type {entry['type']}"

    def test_no_duplicate_model_tags(self):
        names = [e["model"] for e in CATALOG]
        assert len(names) == len(set(names))


class TestCatalogSearch:
    def test_fit_label_matches_score_band(self):
        for row in catalog_search([], budget_gb=12):
            if row["score"] >= 88:
                assert row["fit"] == "OPTIMAL"
            elif row["score"] >= 70:
                assert row["fit"] == "SUITABLE"
            elif row["score"] >= 45:
                assert row["fit"] == "MARGINAL"
            else:
                assert row["fit"] == "UNSUITABLE"

    def test_fit_labels_are_from_the_known_set(self):
        """The hub colours rows by looking the label up in a fixed map. A new
        label the UI doesn't know about renders as uncoloured text."""
        known = {"OPTIMAL", "SUITABLE", "MARGINAL", "UNSUITABLE"}
        for budget in (2, 8, 12, 48):
            for row in catalog_search([], budget_gb=budget):
                assert row["fit"] in known

    def test_sorted_best_fit_first(self):
        scores = [r["score"] for r in catalog_search([], budget_gb=12)]
        assert scores == sorted(scores, reverse=True)

    def test_type_filter_narrows_results(self):
        code = catalog_search([], budget_gb=12, type_filter="code")
        assert code and all(r["type"] == "code" for r in code)
        assert len(code) < len(catalog_search([], budget_gb=12))

    def test_query_searches_org_not_just_name(self):
        """Typing a lab name ("google") should find its models even though
        the string appears in no model tag."""
        rows = catalog_search([], budget_gb=12, query="google")
        assert rows and all(r["org"] == "Google" for r in rows)

    def test_runs_flips_to_cpu_when_over_budget(self):
        rows = {r["model"]: r for r in catalog_search([], budget_gb=6)}
        assert rows["qwen3:4b"]["runs"] == "gpu"
        assert rows["llama3.1:70b"]["runs"] == "cpu+gpu"

    def test_installed_ignores_quant_suffix(self):
        rows = {r["model"]: r for r in catalog_search(["qwen3:14b-q4_K_M"], budget_gb=16)}
        assert rows["qwen3:14b"]["installed"] is True
        assert rows["qwen3:8b"]["installed"] is False
