# PyInstaller spec — freezes the FastAPI backend into resources/backend/.
#
# WHY onedir (not onefile): onefile self-extracts to a temp dir on EVERY
# launch — for an app this size that's 10-20s of startup and a favorite
# false-positive trigger for antivirus. onedir starts fast and scans clean(er).
#
# WHY the collect_all calls: chromadb, llm_guard and their transitive deps
# load data files and hidden submodules dynamically; PyInstaller's static
# analysis can't see them. This is THE classic pain of freezing ML Python —
# when the frozen exe dies with ModuleNotFoundError, add the package here.
#
# Build:  cd python && pyinstaller arthur-backend.spec --noconfirm

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

for pkg in ("chromadb", "llm_guard", "transformers", "tokenizers", "onnxruntime", "tiktoken"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass  # optional heavies may be absent in a slim build profile

hiddenimports += [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "keyring.backends.Windows",
    "pydantic.deprecated.decorator",
    "email_validator",
]

# Sandbox Dockerfiles + scripts ship as data so the runner can build images
datas += [("sandbox/images", "sandbox/images")]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "notebook", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts,
    exclude_binaries=True,
    name="arthur-backend",
    console=False,          # no flashing terminal window behind the app
    disable_windowed_traceback=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="arthur-backend")
