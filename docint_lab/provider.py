"""
provider.py — the seam.

This is the whole switch. Two modules expose the same four functions, and this
picks one:

                    analyze(doc_id)  all_ids()  truth(doc_id)  note(doc_id)
    mock_docint          yes            yes         yes            yes
    azure_docint         yes            yes         yes            yes

`run_lab.py` binds to whichever this returns and never asks which it got.
`extract.py` does not import this file at all.

Keep the seam this narrow and "can we swap Document Intelligence for Textract"
is a one-file question with an honest answer. Let vendor types leak past it and
the same question needs a project.
"""

from __future__ import annotations

from types import SimpleNamespace

import azure_docint
import mock_docint
from config import Settings, get_settings


def get(provider_override: str | None = None) -> tuple[SimpleNamespace, Settings]:
    settings = get_settings(provider_override)

    if settings.provider == "mock":
        impl = mock_docint
    else:
        azure_docint.preflight(settings)
        impl = azure_docint

    # The azure provider needs its settings; the mock does not. Bind them here
    # so the caller gets one identical interface either way.
    return SimpleNamespace(
        name=settings.provider,
        analyze=(mock_docint.analyze if settings.provider == "mock"
                 else lambda d: azure_docint.analyze(d, settings)),
        all_ids=impl.all_ids,
        truth=impl.truth,
        note=impl.note,
        DOCUMENTS=impl.DOCUMENTS,
    ), settings


def describe(settings: Settings) -> str:
    if settings.provider == "mock":
        return "provider: mock  (offline, free, deterministic)"
    return (f"provider: azure  ({settings.model} @ api-version "
            f"{settings.api_version}, /{settings.resolved_route()}/)\n"
            f"          endpoint: {settings.endpoint}\n"
            f"          cache:    "
            f"{'on — delete out/azure/di_raw/ to force a fresh read' if settings.use_cache else 'OFF — every run is billed'}")
