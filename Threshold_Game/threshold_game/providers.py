"""
providers.py — the seam.

One function decides where the ten documents come from, and the scoring
engine never learns which answer it got.

    mock    game.DOCUMENTS, verbatim, offline, free
    azure   the same documents rendered, degraded, and read by a live
            Document Intelligence resource

THE RULE THAT MAKES THIS WORK: the service supplies `kv` and nothing else.
`truth`, `note`, `hint` and `id` always come from the hand-authored mock.
Truth is what the form SAYS. If truth came from the service, the game would
be scoring the service against itself and every answer would be 'correct'.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

import docint
import render
from game import CHECKS, DOCUMENTS, FIELDS, play

CACHE_DIR = Path(__file__).resolve().parent.parent / "out" / "azure" / "di_raw"


def clear_cache() -> int:
    """Delete cached service responses. Returns how many were removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        n += 1
    return n


def _settings_key(settings: docint.Settings) -> str:
    # model, api_version and route all change what the service returns —
    # prebuilt-layout vs prebuilt-document, a keyValuePairs feature flag
    # baked into the URL by model, an api-version/route mismatch that
    # 404s. None of that shows up in render.fingerprint(), which only
    # covers the document and its degradation. Without this, changing the
    # model and re-running silently serves the PREVIOUS model's cached
    # response — exactly the "learn the wrong lesson with no visible clue"
    # failure this lab is built to demonstrate, except aimed at the tool
    # instead of the learner.
    raw = f"{settings.model}|{settings.api_version}|{settings.resolved_route()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _cache_file(doc_id: str, fingerprint: str, settings_key: str) -> Path:
    # The fingerprint and the settings key are both in the NAME, so changing
    # a degradation parameter OR a service configuration parameter
    # invalidates the entry automatically instead of serving a response that
    # belongs to a different image or a different model/api-version/route.
    return CACHE_DIR / f"{doc_id}-{fingerprint}-{settings_key}.json"


def get_documents(provider: str, settings: docint.Settings | None = None,
                  on_progress=None, analyze_fn=None, preflight_fn=None):
    """Returns (documents, report).

    report = {"unmapped": [...], "truncated": [...], "empty": [...],
              "cached": int, "billed": int}
    """
    if provider == "mock":
        return DOCUMENTS, {"unmapped": [], "truncated": [], "empty": [],
                           "cached": 0, "billed": 0}

    if settings is None:
        raise docint.DocIntError("The azure provider needs credentials.")

    call = analyze_fn or docint.analyze

    # BEFORE THE LOOP, so it runs on a fully warm cache too. Credentials are
    # only otherwise checked inside docint.analyze(), which a cache hit never
    # reaches — so on a shared classroom VM whose cache a trainer had already
    # warmed, any two strings typed into the sidebar produced a complete
    # "live results loaded" board that had touched no service at all.
    (preflight_fn or docint.preflight)(settings)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    skey = _settings_key(settings)

    out: list[dict] = []
    report = {"unmapped": [], "truncated": [], "empty": [], "cached": 0,
              "billed": 0}

    for i, doc in enumerate(DOCUMENTS):
        if on_progress:
            on_progress(i, len(DOCUMENTS), doc["id"])

        fp = render.fingerprint(doc)
        cache = _cache_file(doc["id"], fp, skey)

        payload = None
        if cache.exists():
            try:
                payload = json.loads(cache.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                # A run interrupted mid-write leaves a truncated file, and a
                # bare JSONDecodeError out of here would then break EVERY
                # later run with a traceback that names json, not the cache.
                # A damaged entry is just a miss: re-analyse it.
                payload = None
                print(f"  warning: cached response for {doc['id']} is "
                      f"unreadable ({type(e).__name__}) and will be "
                      f"re-analysed.\n  If this repeats, use 'Clear cache' "
                      f"in the sidebar (or providers.clear_cache()) to "
                      f"remove every cached response.", file=sys.stderr)
            # Counted only if we actually got something usable back: a file
            # holding the literal `null` parses fine and is still a miss.
            if payload is not None:
                report["cached"] += 1

        if payload is None:
            payload = call(render.build_pdf(doc), doc["id"], settings)
            cache.write_text(json.dumps(payload))
            report["billed"] += 1

        kvs, unmapped = docint.adapt(payload, doc["id"])
        for label in unmapped:
            if label not in report["unmapped"]:
                report["unmapped"].append(label)

        # Zero pairs from EVERY document is the signature of a missing
        # features=keyValuePairs flag: the service returns HTTP 200, a full
        # page of recognised text, and no key-value pairs at all. Without
        # this the symptom is eighty missing fields and no stated cause.
        if not kvs:
            report["empty"].append(doc["id"])

        # The free tier reads the first two pages and says nothing about the
        # rest: HTTP 200, no warning, the pages array simply ends. render.py
        # keeps everything inside two pages so this should never fire — but
        # if it ever does, it must be loud, because the symptom is a field
        # that reports as missing for no visible reason.
        posted = len(render.pages(doc))
        returned = docint.pages_returned(payload)
        if returned and returned < posted:
            report["truncated"].append(
                f"{doc['id']}: posted {posted} pages, service read {returned}")

        out.append({**doc, "kv": kvs})       # truth/note/hint/id ride along

    if on_progress:
        on_progress(len(DOCUMENTS), len(DOCUMENTS), "done")
    return out, report


def search_bounds(documents=None, trials: int = 2000,
                  seed: int = 7) -> tuple[int, int]:
    """The tuning ceiling and the best known score for a document set.

    WHY A SEARCH AND NOT THE NAMED STRATEGIES: the mock's published numbers
    (252 and 720, quoted in the board's strategy tab) came from a threshold
    search. The best of the seven named strategies is -488 with checks off
    and 576 with them on, so taking a max over those would quietly rewrite
    the headline figures the session narrative depends on.

    This is a LOWER BOUND — a random search finds the best it happened to
    try, never provably the best there is. Say so wherever it is displayed.

    Call this ONCE, when live results load. Streamlit re-runs the whole
    script on every slider move and 2000 trials is roughly 1.5 seconds.
    """
    rng = random.Random(seed)
    grid = [0.0, 0.5, 0.6, 0.69, 0.75, 0.8, 0.83, 0.85, 0.86, 0.88, 0.9,
            0.92, 0.94, 0.95, 0.97, 0.99, 1.01]
    off = {k: False for k in CHECKS}
    on = {k: True for k in CHECKS}
    ceiling = best = -10 ** 9
    for _ in range(trials):
        th = {f: rng.choice(grid) for f in FIELDS}
        ceiling = max(ceiling, play(th, off, documents=documents)["total"]["score"])
        best = max(best, play(th, on, documents=documents)["total"]["score"])
    return ceiling, max(best, ceiling)


def describe(provider: str, settings: docint.Settings | None) -> str:
    if provider == "mock":
        return "mock — offline, free, deterministic"
    if settings is None:
        return "azure — not configured"
    return (f"azure — {settings.model} @ {settings.api_version}, "
            f"/{settings.resolved_route()}/")
