"""Mode-aware recommendation table — the contract behind the model menu."""

from core.model_recs import MODE_RECS, MODEL_SIZES_GB, recommendations
from tools.base import TaskMode


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
