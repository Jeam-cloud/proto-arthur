"""Route handlers must not shadow the modules they call.

THE BUG THIS CAUGHT, live in production for weeks:

    from research import export as research_export      # module
    ...
    async def research_export(request, body):           # rebinds the name
        data = await asyncio.to_thread(research_export.to_pdf, ...)

By the time the handler ran, `research_export` in globals was the HANDLER, so
`research_export.to_pdf` raised AttributeError. Research export was broken for
every user, in both formats, and it surfaced only as "Failed to fetch" in the
browser — a network-shaped error message for a name-resolution bug.

Ruff had been reporting it as F811 the whole time, in a file with other
pre-existing findings, which is exactly how a real one hides among noise.

This test is generic rather than about that one function: any module imported by
the routes module that later becomes something else is the same bug, and the
next one will be in a different file.
"""

from __future__ import annotations

import types

import core.api.routes as routes


def test_imported_modules_are_still_modules():
    """Every `from x import y as z` in the routes module must still resolve to a
    module after the whole file has executed."""
    rebound = [
        name for name, value in vars(routes).items()
        if name.endswith(("_export", "_engine", "_citations"))
        and not isinstance(value, types.ModuleType)
    ]
    assert rebound == [], (
        f"these route handlers shadow an imported module: {rebound}. "
        "Rename the handler — the module is what the handler body calls."
    )


def test_the_export_module_is_reachable_from_the_routes_module():
    """The specific regression: the functions the export route calls have to be
    findable through the name the route body uses."""
    assert isinstance(routes.research_export, types.ModuleType)
    for fn in ("to_pdf", "to_docx", "filename_for"):
        assert hasattr(routes.research_export, fn), fn


def test_the_export_route_is_still_registered():
    """Renaming the handler must not silently unregister the endpoint."""
    paths = {getattr(r, "path", None) for r in routes.router.routes}
    assert "/research/export" in paths
