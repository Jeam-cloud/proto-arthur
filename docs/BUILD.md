# Arthur — Build & Ship

## Dev loop

    # backend
    cd python && python -m venv .venv && .venv\Scripts\activate
    pip install -r requirements-dev.txt
    pytest tests/                 # fast: fakes, no ML deps exercised
    ruff check .

    # app (starts Vite + Electron; Electron spawns the backend from source)
    npm install
    npm run dev

Backend standalone (curl-friendly): set `ARTHUR_AUTH_TOKEN=dev-token` in `python/.env`, run `uvicorn main:app --port 8756`, and the UI's browser fallback will connect.

## Release build

    npm run dist

Order matters and the script enforces it: `vite build` → `pyinstaller arthur-backend.spec` → `electron-builder`. Output: `release/Arthur Setup 0.2.0.exe` (~installer). First-run downloads that remain on the user's machine: the Ollama models (2–8GB) and, if not pre-bundled, LLM-Guard's classifier (~700MB).

Tagging `vX.Y.Z` runs the same pipeline in GitHub Actions and publishes the release + update manifest.

## Code signing (do not skip for public releases)

electron-updater's sha512 check verifies the download matches the manifest; signing verifies the manifest's author. Unsigned means (a) SmartScreen scares users off and (b) a compromised GitHub account can push updates every install trusts. Options, roughly ascending in friction: Azure Trusted Signing (subscription, integrates with electron-builder), an OV certificate (yearly cost, some SmartScreen reputation lag), an EV certificate (hardware token, instant reputation). Set `CSC_LINK`/`CSC_KEY_PASSWORD` secrets and uncomment the lines in `release.yml`.

## Freezing gotchas (PyInstaller + ML Python)

The spec already `collect_all`s chromadb/llm_guard/transformers/onnxruntime. When the frozen exe dies with `ModuleNotFoundError` that the venv doesn't reproduce, add the package to the spec — that's the debugging loop. Keep `numpy<2` pinned (torch compat). Test the installer on a machine that has never seen Python — "works on the dev box" proves nothing about a frozen build.

## Pre-bundling LLM-Guard's model (offline-first installs)

By default the DeBERTa scanner downloads from HuggingFace on first scan. To ship it: run the app once, copy `%USERPROFILE%\.cache\huggingface\hub\models--protectai--deberta-v3-base-prompt-injection-v2` into `python/` as a data dir in the spec, and set `HF_HUB_OFFLINE=1` in the backend env. The heuristic scanner covers the gap either way.

## Clean-machine test checklist (tracker Phase 5/6)

Fresh Windows VM → run installer → wizard finds no Ollama → install → recommended model pulls with visible progress → chat streams → kill Ollama mid-chat (banner + retry, no crash) → disconnect network (research fails with a readable error; chat unaffected) → deny mic (voice shows the fix, no crash) → Docker off (tool modes disabled with reasons) → uninstall keeps user data.
