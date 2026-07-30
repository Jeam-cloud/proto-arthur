"""Local vs remote models.

This is a TRUST boundary, not a cosmetic label. Arthur records every Ollama
reply as `provider: "local"`, shows no cloud badge for local replies, and the
research composer prints "Nothing leaves this computer." An Ollama `:cloud` tag
runs on Ollama's infrastructure through the identical code path — so without
this distinction, every prompt and every attached file goes to a third party
while the UI insists otherwise.
"""

from __future__ import annotations

from core.model_kind import CLOUD_PROVIDER, is_cloud_model, locality_note, provider_for
from core.model_recs import catalog_search


class TestDetection:
    def test_both_ollama_cloud_shapes_are_caught(self):
        # The library uses a bare tag and a size-qualified suffix.
        assert is_cloud_model("kimi-k3:cloud") is True
        assert is_cloud_model("gemma4:cloud") is True
        assert is_cloud_model("gemma4:31b-cloud") is True

    def test_local_models_are_not_flagged(self):
        for name in ("gemma4:latest", "llama3.1:8b", "qwen3:32b", "qwen2.5-coder:7b"):
            assert is_cloud_model(name) is False, name

    def test_a_name_that_merely_mentions_cloud_is_not_a_cloud_model(self):
        # A false positive here would slander a local model and, worse, teach
        # the user that the badge is noise.
        assert is_cloud_model("cloudy-llama:7b") is False
        assert is_cloud_model("nimbus:cloudburst") is False

    def test_whitespace_and_empties_are_safe(self):
        assert is_cloud_model("  kimi-k3:cloud  ") is True
        assert is_cloud_model("") is False
        assert is_cloud_model(None) is False


class TestProviderAttribution:
    def test_a_cloud_reply_is_not_recorded_as_local(self):
        # THE load-bearing assertion. `provider` drives the transcript AND the
        # `cloud · provider` badge in the UI.
        assert provider_for("kimi-k3:cloud") == CLOUD_PROVIDER
        assert CLOUD_PROVIDER != "local"

    def test_a_local_reply_still_says_local(self):
        assert provider_for("llama3.1:8b") == "local"

    def test_the_privacy_claim_is_never_printed_for_a_cloud_model(self):
        assert "Nothing leaves this computer" in locality_note("llama3.1:8b")
        cloud = locality_note("kimi-k3:cloud")
        assert "Nothing leaves this computer" not in cloud
        assert "leave this computer" in cloud


class TestCatalogScoring:
    def _rows(self, budget=11.3):
        return {r["model"]: r for r in catalog_search([], budget_gb=budget)}

    def test_local_models_still_score_against_the_budget(self):
        rows = self._rows()
        assert rows["qwen3:4b"]["fit"] in {"OPTIMAL", "SUITABLE"}
        assert rows["llama3.1:70b"]["fit"] == "UNSUITABLE"

    def test_a_cloud_entry_would_not_be_scored_as_a_perfect_fit(self):
        # A cloud tag has size_gb 0, and the fit formula would turn that into
        # 100/OPTIMAL -- making the least local thing in the table look like the
        # single best recommendation on an 11GB machine.
        from core.model_recs import CATALOG

        cloud = [e for e in CATALOG if is_cloud_model(e["model"])]
        if not cloud:
            # None in the catalog today; the guard still has to hold for the
            # moment one is added, which is the point of testing it now.
            import copy
            entry = copy.deepcopy(CATALOG[0])
            entry.update(model="kimi-k3:cloud", size_gb=0, params_b=0)
            CATALOG.append(entry)
            try:
                row = self._rows()["kimi-k3:cloud"]
            finally:
                CATALOG.pop()
        else:
            row = self._rows()[cloud[0]["model"]]

        assert row["fit"] == "CLOUD"
        assert row["cloud"] is True
        assert row["runs"] == "remote"
        assert row["fit"] != "OPTIMAL"


class TestMissingParsers:
    def test_a_missing_module_becomes_an_instruction(self):
        from core.attachments import _missing_module_message

        msg = _missing_module_message(ModuleNotFoundError("No module named 'pypdf'", name="pypdf"))
        assert "pip install pypdf" in msg

    def test_an_unrecognised_module_still_names_the_remedy(self):
        from core.attachments import _missing_module_message

        msg = _missing_module_message(ModuleNotFoundError("nope", name="somethingelse"))
        assert "requirements.txt" in msg

    def test_ordinary_failures_are_left_alone(self):
        # Only an install problem should be reported as an install problem.
        from core.attachments import _missing_module_message

        assert _missing_module_message(ValueError("corrupt file")) is None

    def test_a_pdf_with_no_parser_says_how_to_fix_it(self, tmp_path, monkeypatch):
        import builtins

        from core.attachments import extract_text

        real_import = builtins.__import__

        def no_pypdf(name, *args, **kwargs):
            if name == "pypdf":
                raise ModuleNotFoundError("No module named 'pypdf'", name="pypdf")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_pypdf)
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4")
        text, err = extract_text(p, "document")
        assert text == ""
        assert "pip install pypdf" in err
        # And crucially NOT the raw exception, which read as a problem with the
        # file rather than with the installation.
        assert "No module named" not in err
