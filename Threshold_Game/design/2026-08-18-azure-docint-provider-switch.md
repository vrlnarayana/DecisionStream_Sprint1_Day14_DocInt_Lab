# Azure Document Intelligence Provider Switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Azure Document Intelligence data source to the Threshold Game's Streamlit board, so learners can score the same policy against the mock and against a live service side by side.

**Architecture:** A narrow seam (`providers.get_documents`) returns ten document dicts. The mock returns them verbatim; the azure path renders each document to a deliberately degraded PDF, sends it to Document Intelligence, adapts the response into the game's `kv` shape, and merges it onto the mock's hand-authored `truth`. The scoring engine never learns which provider produced a document.

**Tech Stack:** Python 3.12, Streamlit 1.56, Pillow (new), stdlib `urllib` for HTTP (no SDK), Azure AI Document Intelligence `prebuilt-layout` @ api-version `2024-11-30`.

**Spec:** `docs/superpowers/specs/2026-08-18-azure-docint-provider-switch-design.md`

## Global Constraints

- **Ground truth is never derived from the service.** Azure supplies `kv` only; `truth`, `note`, `hint` and `id` always come from `game.DOCUMENTS`.
- **Credentials never touch disk.** No `.env`, no config file, no logging of the key. They live in `st.session_state` and in `Settings` objects held in memory.
- **Every document renders onto at most two pages.** The free tier reads two pages and says nothing about the rest.
- **The mock path stays stdlib-only.** Pillow is imported lazily inside `render.py` so the mock game runs on a machine without it.
- **Determinism is measured on page images, not PDF bytes.** Pillow's PDF writer embeds `/CreationDate`, so PDF bytes differ run to run. Hash `Image.tobytes()`.
- **API facts, copied verbatim from the spec — do not re-derive:**
  - Model `prebuilt-layout` with **`&features=keyValuePairs`**. Without the flag: HTTP 200 and zero pairs.
  - api-version `2024-11-30` implies route `/documentintelligence/`; earlier implies `/formrecognizer/`. A mismatch 404s with a message blaming the *model*.
  - `_clean_label` must run `re.sub(r"\s+([.,;:])", r"\1", text)` **before** stripping. DI returns `D.O.B.:` as `D.O.B .:`.
  - Strip the trailing **colon only**, never the full stop.
- **The repo has many unrelated modified files.** Every commit step must use explicit paths. **Never `git add -A` or `git add .`** in this repo.
- **Test idiom:** this project uses standalone scripts with `def main() -> int` printing `PASS`/`FAIL`, run as `python3 <script>.py` (see `verify.py`). There is no pytest. Follow that pattern.
- **Run everything with the project venv:** `.venv/bin/python`.

---

### Task 1: Engine seam — `play()` accepts a document list

**Files:**
- Modify: `threshold_game/game.py:575`
- Test: `threshold_game/selftest_seam.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `game.play(thresholds: dict, checks: dict, documents: list[dict] | None = None) -> dict`. Every later task calls this three-argument form.

- [ ] **Step 1: Write the failing test**

Create `threshold_game/selftest_seam.py`:

```python
"""selftest_seam.py — play() must score whatever document list it is given."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game import DEFAULT_CHECKS, DEFAULT_THRESHOLDS, DOCUMENTS, play

CHECKS_OFF = dict(DEFAULT_CHECKS)


def check(label: str, got, want, fails: list) -> None:
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")


def main() -> int:
    fails: list[str] = []
    th = dict(DEFAULT_THRESHOLDS)

    # 1. No documents argument behaves exactly as before.
    baseline = play(th, CHECKS_OFF)
    check("default doc count", len(baseline["rows"]), 10, fails)

    # 2. An explicit list of one document scores only that document.
    one = play(th, CHECKS_OFF, documents=[DOCUMENTS[0]])
    check("single doc count", len(one["rows"]), 1, fails)
    check("single doc id", one["rows"][0]["doc_id"], "APP-0001", fails)

    # 3. Passing DOCUMENTS explicitly equals passing nothing.
    explicit = play(th, CHECKS_OFF, documents=DOCUMENTS)
    check("explicit == default", explicit["total"], baseline["total"], fails)

    # 4. An empty list scores zero, it does not fall back to DOCUMENTS.
    empty = play(th, CHECKS_OFF, documents=[])
    check("empty doc count", len(empty["rows"]), 0, fails)
    check("empty score", empty["total"]["score"], 0, fails)

    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print("  " + f)
        return 1
    print("PASS — play() honours the documents argument (4 checks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python threshold_game/selftest_seam.py`
Expected: `TypeError: play() got an unexpected keyword argument 'documents'`

- [ ] **Step 3: Make the change**

In `threshold_game/game.py`, replace the `play` signature and its first line:

```python
def play(thresholds: dict, checks: dict,
         documents: list[dict] | None = None) -> dict:
    docs = DOCUMENTS if documents is None else documents
    rows, tot = [], {"score": 0, "correct_accepts": 0, "wrong_accepts": 0,
                     "flags": 0, "missing": 0}
    per_doc = {}
    for doc in docs:
        ...
```

Only the signature, the new `docs` line, and `for doc in docs:` change. The loop body is untouched.

Note the `is None` test rather than a truthy test — an empty list must score zero, not silently fall back to all ten documents.

- [ ] **Step 4: Run the test and the existing parity check**

Run: `.venv/bin/python threshold_game/selftest_seam.py`
Expected: `PASS — play() honours the documents argument (4 checks).`

Run: `.venv/bin/python verify.py`
Expected: `PASS — the JavaScript and Python engines agree on every configuration.` (or a clean skip if Node is absent). This proves scoring did not drift.

- [ ] **Step 5: Commit**

```bash
git add threshold_game/game.py threshold_game/selftest_seam.py
git commit -m "feat(game): play() accepts an explicit document list"
```

---

### Task 2: `render.py` — degraded two-page PDFs

**Files:**
- Create: `threshold_game/render.py`
- Modify: `requirements.txt`
- Test: `threshold_game/selftest_render.py` (create)

**Interfaces:**
- Consumes: `game.DOCUMENTS`, `game.FIELD_KIND`.
- Produces:
  - `render.PROFILES: dict[str, str]` — doc id to profile name.
  - `render.LABELS: dict[str, str]` — internal field name to the label printed on the form.
  - `render.pages(doc: dict) -> list[Image.Image]` — the degraded page images, at most two.
  - `render.build_pdf(doc: dict) -> bytes` — those pages as a PDF.
  - `render.fingerprint(doc: dict) -> str` — sha256 over page image bytes; the cache key used by Task 5.
  - `render.find_font(size: int)` — raises `FontNotFound` with the paths tried.

- [ ] **Step 1: Add the dependency**

Replace `requirements.txt` with:

```
# The terminal version (play.py) and the HTML version need NOTHING.
# Standard library only. These are for the Streamlit board alone.
streamlit>=1.30

# Only the azure provider needs this — it renders the ten documents to
# deliberately degraded PDFs so the live service reports real uncertainty.
# render.py imports it lazily, so the mock game still runs without it.
Pillow>=10.0
```

Install: `.venv/bin/python -m pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `threshold_game/selftest_render.py`:

```python
"""selftest_render.py — the rendered PDFs must be two pages and reproducible."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render
from game import DOCUMENTS


def main() -> int:
    fails: list[str] = []

    # 1. Every document has a degradation profile, and every profile is known.
    known = {"clean", "light", "handwriting", "poor_scan"}
    for doc in DOCUMENTS:
        prof = render.PROFILES.get(doc["id"])
        if prof is None:
            fails.append(f"{doc['id']}: no profile assigned")
        elif prof not in known:
            fails.append(f"{doc['id']}: unknown profile {prof!r}")

    # 2. Every scored field has a printed label, including the four extras
    #    the arithmetic and scope_gate checks depend on.
    needed = set()
    for doc in DOCUMENTS:
        for kv in doc["kv"]:
            needed.add(kv["field"])
    for field in sorted(needed):
        if field not in render.LABELS:
            fails.append(f"no printed label for field {field!r}")

    # 3. At most two pages, always — the free tier reads two and stays silent.
    for doc in DOCUMENTS:
        n = len(render.pages(doc))
        if n < 1 or n > 2:
            fails.append(f"{doc['id']}: rendered {n} pages, want 1 or 2")

    # 4. Deterministic. Hash the IMAGES, not the PDF — Pillow stamps
    #    /CreationDate into PDF output, so PDF bytes differ every run.
    for doc in DOCUMENTS[:3]:
        if render.fingerprint(doc) != render.fingerprint(doc):
            fails.append(f"{doc['id']}: fingerprint is not reproducible")

    # 5. Distinct documents do not collide.
    prints = {d["id"]: render.fingerprint(d) for d in DOCUMENTS}
    if len(set(prints.values())) != len(prints):
        fails.append("two documents produced the same fingerprint")

    # 6. build_pdf returns something that is actually a PDF.
    pdf = render.build_pdf(DOCUMENTS[0])
    if not pdf.startswith(b"%PDF"):
        fails.append("build_pdf did not return PDF bytes")

    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print("  " + f)
        return 1
    print(f"PASS — render: profiles, labels, page cap, determinism "
          f"({len(DOCUMENTS)} documents).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python threshold_game/selftest_render.py`
Expected: `ModuleNotFoundError: No module named 'render'`

- [ ] **Step 4: Write `render.py`**

Create `threshold_game/render.py`:

```python
"""
render.py — the ten documents, drawn as forms and then deliberately damaged.

WHY THIS FILE EXISTS AT ALL

Document Intelligence, reading a cleanly generated PDF, returns a mean
confidence of 0.990. The Threshold Game is a game about confidence
thresholds. Feed it 0.99 everywhere and every slider below 0.98 accepts
everything: the tuning axis — the thing the game is named after — stops
existing.

So the pages are damaged before they are sent, in the way the mock says they
are damaged. APP-0003 is a handwritten form, so its characters wander off the
baseline. APP-0010 is a bad scan, so it is blurred, noisy, low-contrast and
JPEG-mangled. The service is then genuinely unsure, for the reason the
document claims, and the confidence it reports is real.

Everything here is seeded on the document id, so a run is reproducible and
calibration means something.

Pillow is imported lazily: the mock game must keep working on a machine that
has never installed it.
"""

from __future__ import annotations

import hashlib
import io
import random

from game import DOCUMENTS

W, H = 1240, 1754          # A4 at 150 dpi
MARGIN = 90


class FontNotFound(RuntimeError):
    pass


# --------------------------------------------------------------------------
# What the form actually says.
#
# These are a form designer's labels, not our field names, and several of them
# abbreviate with a full stop: "Claim Ref.", "Policy No.", "Sum Ins.", "D.O.B.".
# That is deliberate. It is exactly the shape that breaks a naive label
# cleaner, and docint.adapt has to survive it.
# --------------------------------------------------------------------------
LABELS = {
    "claim_reference":       "Claim Ref.",
    "policy_number":         "Policy No.",
    "applicant_name":        "Applicant Name",
    "date_of_birth":         "D.O.B.",
    "vehicle_registration":  "Vehicle Registration",
    "incident_date":         "Date of Incident",
    "sum_insured":           "Sum Ins.",
    "excess":                "Excess",
    "repair_estimate_total": "Repair Estimate Total",
    "parts_subtotal":        "Parts Subtotal",
    "labour_subtotal":       "Labour Subtotal",
    "driving_licence_number": "Driving Licence No.",
    "licence_postcode":      "Licence Postcode",
}

PROFILES = {
    "APP-0001": "clean",
    "APP-0002": "light",
    "APP-0003": "handwriting",
    "APP-0004": "clean",
    "APP-0005": "clean",
    "APP-0006": "clean",
    "APP-0007": "clean",
    "APP-0008": "clean",
    "APP-0009": "clean",
    "APP-0010": "poor_scan",
}

# blur radius, rotation degrees, noise sigma, contrast factor, jpeg quality
PARAMS = {
    "clean":       dict(blur=0.0, rot=0.0,  noise=0,  contrast=1.0, jpeg=0),
    "light":       dict(blur=0.8, rot=0.3,  noise=8,  contrast=0.95, jpeg=80),
    "handwriting": dict(blur=1.1, rot=0.6,  noise=12, contrast=0.88, jpeg=70),
    "poor_scan":   dict(blur=2.2, rot=1.1,  noise=26, contrast=0.72, jpeg=35),
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def find_font(size: int):
    """A real TTF or a clear error. Never PIL's bitmap fallback — that font
    is tiny and aliased, and it would wreck the confidence bands invisibly."""
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise FontNotFound(
        "No TrueType font found. render.py needs one to draw the forms.\n"
        "  Tried:\n    " + "\n    ".join(FONT_CANDIDATES) + "\n"
        "  Install one (Linux: apt-get install fonts-dejavu-core) or add its "
        "path to render.FONT_CANDIDATES."
    )


def _page_for(kv: dict) -> int:
    """Mock page 1 stays on page 1; anything on page 2 or 3 goes to page 2.

    The free tier reads the first two pages and reports nothing about the
    rest — no warning, no error, the pages array simply ends. Three documents
    author content on page 3 (APP-0005's second estimate, APP-0008's licence,
    APP-0010's total), so page 3 is folded into page 2.

    This is safe: page numbers are display-only in the engine. game.py uses
    kv["page"] at lines 419, 424 and 474 — the dropped list and the
    FieldResult constructor — and never in a scoring or check decision.
    """
    return 1 if kv.get("page", 1) <= 1 else 2


def _draw_text(draw, xy, text, font, rng, profile: str) -> None:
    """Straight text, except for handwriting, which wanders per character."""
    if profile != "handwriting":
        draw.text(xy, text, font=font, fill=0)
        return
    x, y = xy
    for ch in text:
        draw.text((x, y + rng.uniform(-3.2, 3.2)), ch, font=font, fill=0)
        x += draw.textlength(ch, font=font) + rng.uniform(-0.6, 1.4)


def _degrade(img, profile: str, rng):
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter
    p = PARAMS[profile]
    if p["rot"]:
        img = img.rotate(rng.uniform(-p["rot"], p["rot"]), resample=Image.BICUBIC,
                         fillcolor=255)
    if p["blur"]:
        img = img.filter(ImageFilter.GaussianBlur(p["blur"]))
    if p["noise"]:
        # Image.effect_noise is driven by PIL's own RNG, which we cannot seed,
        # so the noise field is built from our seeded rng instead.
        #
        # It is generated at a third of full size and scaled up: that is ~9x
        # cheaper than 2.1M independent samples, and the resulting blobby
        # grain looks more like a scanner than per-pixel salt does.
        nw, nh = img.width // 3, img.height // 3
        data = bytes(max(0, min(255, int(128 + rng.gauss(0, p["noise"]))))
                     for _ in range(nw * nh))
        noise = Image.frombytes("L", (nw, nh), data).resize(img.size)
        # add(a, b, scale, offset) == (a + b) / scale + offset, so an offset
        # of -128 against noise centred on 128 is zero-mean additive noise.
        img = ImageChops.add(img, noise, scale=1, offset=-128)
    if p["contrast"] != 1.0:
        img = ImageEnhance.Contrast(img).enhance(p["contrast"])
    if p["jpeg"]:
        buf = io.BytesIO()
        img.convert("L").save(buf, format="JPEG", quality=p["jpeg"])
        buf.seek(0)
        img = Image.open(buf).convert("L")
    return img


def pages(doc: dict):
    """The document as one or two degraded greyscale page images."""
    from PIL import Image, ImageDraw

    profile = PROFILES[doc["id"]]
    rng = random.Random(f"{doc['id']}:{profile}")

    title = find_font(34)
    label = find_font(26)
    value = find_font(28)

    buckets: dict[int, list[dict]] = {1: [], 2: []}
    for kv in doc["kv"]:
        buckets[_page_for(kv)].append(kv)

    out = []
    for pageno in (1, 2):
        rows = buckets[pageno]
        if pageno == 2 and not rows:
            continue
        img = Image.new("L", (W, H), 255)
        draw = ImageDraw.Draw(img)

        draw.text((MARGIN, 70), "MOTOR CLAIM APPLICATION", font=title, fill=0)
        draw.text((MARGIN, 118), f"{doc['id']}   page {pageno}", font=label, fill=0)
        draw.line((MARGIN, 165, W - MARGIN, 165), fill=0, width=2)

        y = 220
        for kv in rows:
            text = LABELS.get(kv["field"], kv["field"])
            draw.text((MARGIN, y), f"{text}:", font=label, fill=0)
            _draw_text(draw, (MARGIN + 470, y - 2), str(kv["value"]),
                       value, rng, profile)
            y += 74

        out.append(_degrade(img, profile, rng))
    return out


def build_pdf(doc: dict) -> bytes:
    imgs = [p.convert("RGB") for p in pages(doc)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:],
                 resolution=150.0)
    return buf.getvalue()


def fingerprint(doc: dict) -> str:
    """Cache key. Hashes the page IMAGES, not the PDF.

    Pillow stamps /CreationDate into PDF output, so PDF bytes change every
    run even when the pixels do not — using them as a cache key would re-bill
    all ten documents on every restart.
    """
    h = hashlib.sha256()
    h.update(repr(sorted(PARAMS[PROFILES[doc["id"]]].items())).encode())
    for p in pages(doc):
        h.update(p.tobytes())
    return h.hexdigest()[:16]


def build_all() -> dict[str, bytes]:
    return {d["id"]: build_pdf(d) for d in DOCUMENTS}
```

- [ ] **Step 5: Run the test**

Run: `.venv/bin/python threshold_game/selftest_render.py`
Expected: `PASS — render: profiles, labels, page cap, determinism (10 documents).`

- [ ] **Step 6: Eyeball one page**

Rendering is the one part of this a test cannot fully judge — the degradation has to be bad enough to matter and good enough to read.

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'threshold_game')
import render
from game import DOCUMENTS
for i in (0, 2, 9):
    d = DOCUMENTS[i]
    render.pages(d)[0].save(f'/tmp/{d[\"id\"]}.png')
    print(d['id'], render.PROFILES[d['id']], '->', f'/tmp/{d[\"id\"]}.png')
"
```

Open the three PNGs. APP-0001 should be crisp, APP-0003 visibly handwritten and wobbly, APP-0010 clearly a bad photocopy but still humanly readable. If APP-0010 is illegible to you it will be illegible to the service, and you will get missing fields rather than low confidence — turn `poor_scan` down.

- [ ] **Step 7: Commit**

```bash
git add threshold_game/render.py threshold_game/selftest_render.py requirements.txt
git commit -m "feat(render): degraded two-page PDFs for the azure provider"
```

---

### Task 3: `docint.py` — settings and HTTP client

**Files:**
- Create: `threshold_game/docint.py`
- Test: `threshold_game/selftest_docint.py` (create; extended in Task 4)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `docint.Settings(endpoint, key, model="prebuilt-layout", api_version="2024-11-30", route="auto", timeout=120)` with `.resolved_route() -> str`, `.analyze_url() -> str`, `.require() -> None`.
  - `docint.DocIntError(RuntimeError)`.
  - `docint.analyze(pdf: bytes, doc_id: str, settings: Settings) -> dict` — the raw `analyzeResult` payload.

- [ ] **Step 1: Write the failing test**

Create `threshold_game/selftest_docint.py`. Task 4 appends to this file; write it now with the URL section only:

```python
"""selftest_docint.py — the Azure adapter, offline. No credentials, no cost.

Everything here runs against recorded payload shapes. Run it before spending
money on a live batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docint

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def test_urls() -> None:
    s = docint.Settings(endpoint="https://x.cognitiveservices.azure.com/", key="k")

    # 2024-11-30 and later live under /documentintelligence/.
    check("route 2024-11-30", s.resolved_route(), "documentintelligence")

    # Anything earlier is /formrecognizer/. A mismatch 404s with a message
    # about the MODEL, which sends you hunting in the wrong place entirely.
    old = docint.Settings(endpoint="https://x/", key="k", api_version="2023-07-31")
    check("route 2023-07-31", old.resolved_route(), "formrecognizer")

    # An explicit route wins over the api-version.
    forced = docint.Settings(endpoint="https://x/", key="k", route="formrecognizer")
    check("forced route", forced.resolved_route(), "formrecognizer")

    # prebuilt-layout MUST carry the feature flag. Without it the service
    # returns HTTP 200 and zero key-value pairs — a successful, empty and
    # entirely misleading response.
    check("keyValuePairs flag present", "features=keyValuePairs" in s.analyze_url(), True)

    check("analyze url", s.analyze_url(),
          "https://x.cognitiveservices.azure.com/documentintelligence"
          "/documentModels/prebuilt-layout:analyze"
          "?api-version=2024-11-30&features=keyValuePairs")

    # A trailing slash on the endpoint must not double up.
    check("no double slash", "//documentintelligence" in s.analyze_url(), False)

    # A model that is not prebuilt-layout does not get the flag.
    doc = docint.Settings(endpoint="https://x/", key="k", model="prebuilt-document",
                          api_version="2023-07-31")
    check("no flag for prebuilt-document", "features=" in doc.analyze_url(), False)


def test_require() -> None:
    try:
        docint.Settings(endpoint="", key="").require()
        FAILS.append("require(): empty credentials did not raise")
    except docint.DocIntError as e:
        check("require names endpoint", "endpoint" in str(e).lower(), True)
        check("require names key", "key" in str(e).lower(), True)


def main() -> int:
    test_urls()
    test_require()
    if FAILS:
        print(f"FAIL — {len(FAILS)} problem(s):")
        for f in FAILS:
            print("  " + f)
        return 1
    print("PASS — docint offline checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python threshold_game/selftest_docint.py`
Expected: `ModuleNotFoundError: No module named 'docint'`

- [ ] **Step 3: Write the settings and HTTP half of `docint.py`**

Create `threshold_game/docint.py`:

```python
"""
docint.py — Azure AI Document Intelligence, over stdlib urllib.

Ported from the Day-14 lab's azure_docint.py, which was run against a live
resource on 2026-08-16. Every comment below that reads like a warning is
one this code has already paid for once.

There is no SDK here on purpose. The whole client is one POST, one poll loop
and one adapter, and a learner can read all of it.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class DocIntError(RuntimeError):
    pass


@dataclass
class Settings:
    endpoint: str
    key: str
    model: str = "prebuilt-layout"
    api_version: str = "2024-11-30"
    route: str = "auto"
    timeout: int = 120

    def resolved_route(self) -> str:
        if self.route != "auto":
            return self.route
        # 2024-11-30 was the GA rename. String comparison is correct here
        # because the format is fixed-width ISO.
        return ("documentintelligence" if self.api_version >= "2024-11-30"
                else "formrecognizer")

    def analyze_url(self) -> str:
        base = self.endpoint.rstrip("/")
        url = (f"{base}/{self.resolved_route()}/documentModels/"
               f"{self.model}:analyze?api-version={self.api_version}")
        # prebuilt-document was RETIRED in the 2024-11-30 GA API. Key-value
        # pair extraction moved into prebuilt-layout behind an opt-in flag,
        # and WITHOUT the flag layout returns no keyValuePairs at all — a
        # successful, empty, entirely misleading response.
        if self.model == "prebuilt-layout":
            url += "&features=keyValuePairs"
        return url

    def require(self) -> None:
        missing = [n for n, v in (("endpoint", self.endpoint),
                                  ("key", self.key)) if not str(v).strip()]
        if missing:
            raise DocIntError(
                "Azure Document Intelligence needs an "
                + " and a ".join(missing)
                + ".\n  Enter them in the sidebar under 'Azure Document "
                  "Intelligence'. They are held for this session only and "
                  "are never written to disk."
            )


def _request(url: str, settings: Settings, method: str = "GET",
             body: bytes | None = None, content_type: str | None = None):
    headers = {"Ocp-Apim-Subscription-Key": settings.key}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        return e.code, dict(e.headers or {}), {"_error_body": detail}
    except urllib.error.URLError as e:
        raise DocIntError(
            f"Could not reach {url}\n  {e.reason}\n"
            f"  Check the endpoint, and check the resource is not firewalled "
            f"to a VNet you are not on."
        ) from e


def _explain(status: int, payload: dict, settings: Settings) -> str:
    body = payload.get("_error_body", json.dumps(payload)[:800])
    hint = ""
    if status == 404:
        hint = (f"\n  404 is almost always the ROUTE, not the model.\n"
                f"  api-version {settings.api_version} resolved to "
                f"/{settings.resolved_route()}/.\n"
                f"  Override the route in the sidebar's advanced expander.")
    elif status in (401, 403):
        hint = ("\n  401/403 is the key or the region. A key from a different "
                "Azure AI resource\n  returns exactly this.")
    elif status == 429:
        hint = ("\n  429 is the free (F0) tier rate limit. This client already "
                "retries and waits;\n  seeing it here means it gave up.")
    return f"HTTP {status} from Document Intelligence.{hint}\n  body: {body}"


def _post_with_retry(url: str, data: bytes, settings: Settings, doc_id: str):
    """POST the PDF, retrying 429 and honouring Retry-After.

    Day-14 raised on 429 instead of retrying. That survives one document and
    does not survive ten: Day-15's thirty-two document run hit 429 five times
    and spent 94 seconds waiting. Retrying is not an optimisation here.
    """
    for attempt in range(6):
        status, headers, payload = _request(url, settings, "POST", data,
                                            "application/pdf")
        # Some api-versions reject raw binary and want base64 in a JSON body.
        # Both are documented; which one yours accepts is not obvious, so try
        # the other rather than making the learner debug a 415.
        if status == 415:
            body = json.dumps(
                {"base64Source": base64.b64encode(data).decode()}).encode()
            status, headers, payload = _request(url, settings, "POST", body,
                                                "application/json")
        if status != 429:
            return status, headers, payload
        wait = float(headers.get("Retry-After") or headers.get("retry-after") or 20)
        time.sleep(min(wait, 60))
    raise DocIntError(f"{doc_id}: still rate-limited after 6 attempts.")


def analyze(pdf: bytes, doc_id: str, settings: Settings) -> dict:
    """POST the PDF, poll the operation, return the raw payload."""
    settings.require()
    status, headers, payload = _post_with_retry(settings.analyze_url(), pdf,
                                                settings, doc_id)
    if status != 202:
        raise DocIntError(f"{doc_id}: {_explain(status, payload, settings)}")

    op = headers.get("Operation-Location") or headers.get("operation-location")
    if not op:
        raise DocIntError(
            f"{doc_id}: 202 Accepted with no Operation-Location header. "
            f"Nothing to poll.")

    deadline = time.time() + settings.timeout
    delay = 1.0
    while True:
        status, _, payload = _request(op, settings)
        if status != 200:
            raise DocIntError(f"{doc_id}: poll {_explain(status, payload, settings)}")
        state = str(payload.get("status", "")).lower()
        if state == "succeeded":
            return payload
        if state == "failed":
            err = payload.get("error", payload)
            raise DocIntError(f"{doc_id}: analysis failed — {json.dumps(err)[:600]}")
        if time.time() > deadline:
            raise DocIntError(
                f"{doc_id}: still '{state}' after {settings.timeout}s.")
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)        # back off; do not hammer the poll
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python threshold_game/selftest_docint.py`
Expected: `PASS — docint offline checks.`

- [ ] **Step 5: Commit**

```bash
git add threshold_game/docint.py threshold_game/selftest_docint.py
git commit -m "feat(docint): settings, URL resolution and polling client"
```

---

### Task 4: `docint.py` — the label adapter

**Files:**
- Modify: `threshold_game/docint.py` (append)
- Modify: `threshold_game/selftest_docint.py` (append)

**Interfaces:**
- Consumes: `render.LABELS`, `game.FIELDS`.
- Produces:
  - `docint.LABEL_MAP: dict[str, str]` — cleaned lowercase label to field name.
  - `docint._clean_label(text: str) -> str`.
  - `docint.adapt(payload: dict, doc_id: str) -> tuple[list[dict], list[str]]` — a **list** of `{"field", "value", "confidence", "page"}` preserving duplicates, plus the labels it could not map.

- [ ] **Step 1: Write the failing tests**

In `threshold_game/selftest_docint.py`, add these two functions above `main()`, and add `test_clean_label()` and `test_adapt()` to `main()` before the `if FAILS:` block:

```python
def test_clean_label() -> None:
    # The ordinary case: strip the trailing colon.
    check("colon", docint._clean_label("Claim Ref.:"), "Claim Ref.")

    # DO NOT STRIP THE FULL STOP. Half the labels on a real form abbreviate:
    # "D.O.B.", "Sum Ins.", "Claim Ref.", "Policy No.". Tidying the trailing
    # "." off those makes them labels the map has never heard of, and an
    # unmapped label does not raise — the field simply reports as missing.
    check("full stop kept", docint._clean_label("Policy No.:"), "Policy No.")

    # And the other half of the same bug, which only the LIVE service shows.
    # DI reads a page as words, and the final full stop of an abbreviation is
    # its own word. Rejoined, "D.O.B.:" comes back as "D.O.B .:" — strip only
    # the colon and you get "D.O.B .", which is just as unmapped. The mock
    # could not have caught this: its labels are clean strings.
    check("tokenised full stop", docint._clean_label("D.O.B .:"), "D.O.B.")
    check("tokenised, no colon", docint._clean_label("Sum Ins ."), "Sum Ins.")

    # Line breaks and runs of whitespace collapse.
    check("newline", docint._clean_label("Repair Estimate\n  Total :"),
          "Repair Estimate Total")


def _pair(label: str, value: str, conf: float, page: int = 1) -> dict:
    return {
        "key": {"content": label,
                "boundingRegions": [{"pageNumber": page}]},
        "value": {"content": value,
                  "boundingRegions": [{"pageNumber": page}]},
        "confidence": conf,
    }


def test_adapt() -> None:
    payload = {"analyzeResult": {
        "pages": [{"pageNumber": 1}, {"pageNumber": 2}],
        "keyValuePairs": [
            _pair("Claim Ref.:", "CLM-2026-4526", 0.96),
            _pair("D.O.B .:", "1984-02-17", 0.93),          # the tokenised case
            _pair("Sum Ins.:", "6400.00", 0.92),
            # APP-0005's two estimates. If the adapter keys by field name
            # these collapse into one and the conflict check has nothing to
            # fire on — which is the entire point of that document.
            _pair("Repair Estimate Total:", "2150.00", 0.94, 2),
            _pair("Repair Estimate Total:", "2450.00", 0.91, 2),
            _pair("Underwriter Notes:", "n/a", 0.88),       # not a game field
        ]}}

    kvs, unmapped = docint.adapt(payload, "APP-0005")

    check("kv count", len(kvs), 5)
    check("d.o.b. mapped", [k["field"] for k in kvs].count("date_of_birth"), 1)
    check("sum insured mapped", kvs[2]["field"], "sum_insured")

    dupes = [k for k in kvs if k["field"] == "repair_estimate_total"]
    check("duplicates preserved", len(dupes), 2)
    check("duplicate values", sorted(k["value"] for k in dupes),
          ["2150.00", "2450.00"])

    check("confidence is a float", isinstance(kvs[0]["confidence"], float), True)
    check("confidence value", kvs[0]["confidence"], 0.96)
    check("page carried", dupes[0]["page"], 2)

    # An unmapped label is reported, never silently dropped.
    check("unmapped reported", unmapped, ["Underwriter Notes"])

    # A pair with an empty key is skipped without blowing up.
    empty = {"analyzeResult": {"keyValuePairs": [_pair("", "x", 0.5)]}}
    check("empty key skipped", docint.adapt(empty, "X")[0], [])

    # No keyValuePairs member at all — the shape you get when the
    # features=keyValuePairs flag is missing. Must not raise.
    check("no pairs member", docint.adapt({"analyzeResult": {}}, "X")[0], [])
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python threshold_game/selftest_docint.py`
Expected: `AttributeError: module 'docint' has no attribute '_clean_label'`

- [ ] **Step 3: Append the adapter to `docint.py`**

```python
# ==========================================================================
# THE ADAPTER
#
# The only place in this codebase allowed to know Azure's response shape.
# Keep it that way and "could we swap in Textract" stays a one-file question.
# ==========================================================================
import render                                              # noqa: E402

# Printed label (cleaned, lowercased) -> our field name. Built from the
# labels render.py actually prints, plus the aliases a real form uses.
LABEL_MAP = {v.lower(): k for k, v in render.LABELS.items()}
LABEL_MAP.update({
    "claim reference": "claim_reference",
    "claim no.": "claim_reference",
    "policy number": "policy_number",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "d.o.b": "date_of_birth",
    "name": "applicant_name",
    "insured name": "applicant_name",
    "sum insured": "sum_insured",
    "vehicle reg.": "vehicle_registration",
    "vehicle reg": "vehicle_registration",
    "incident date": "incident_date",
    "estimate total": "repair_estimate_total",
    "total": "repair_estimate_total",
    "parts": "parts_subtotal",
    "labour": "labour_subtotal",
    "driving licence number": "driving_licence_number",
})


def _clean_label(text: str) -> str:
    """The key as it appears on the page becomes the key our map is keyed on.

    STRIP THE COLON. DO NOT STRIP THE FULL STOP.

    That reads like a nitpick and it is not. Half the aliases above end in a
    full stop, because that is how people abbreviate on forms: "D.O.B.",
    "Sum Ins.", "Claim Ref.", "Policy No.". Tidy the trailing "." off those
    and they become labels the map has never heard of — and an unmapped label
    does not raise. The document still processes, the board still renders, and
    you have simply lost the applicant's date of birth.

    Then the live service showed us the other half of it. Document
    Intelligence reads a page as words, and the final full stop of an
    abbreviation is its own word. Rejoining them puts a space in front of it,
    so "D.O.B.:" printed on the form comes back as "D.O.B .:" — and stripping
    only the colon leaves "D.O.B .", which is just as unmapped as "D.O.B" was.
    Same silent miss, opposite cause. The mock cannot catch this: its labels
    are clean strings, and only the real service tokenises.

    So whitespace normalisation has to close a gap BEFORE punctuation as well
    as collapse the runs between words.
    """
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)      # "D.O.B .:" -> "D.O.B.:"
    return text.rstrip(":").strip()


def _page_of(part: dict) -> int:
    regions = part.get("boundingRegions") or []
    if regions and isinstance(regions[0], dict):
        return int(regions[0].get("pageNumber", 1))
    return 1


def adapt(payload: dict, doc_id: str) -> tuple[list[dict], list[str]]:
    """analyzeResult -> the game's kv shape, plus the labels we could not map.

    Returns a LIST and preserves duplicate field names. Day-14 keyed its
    results by field name; here APP-0005's two conflicting repair estimates
    are the whole point of the document, and keying by name would silently
    merge them.
    """
    result = payload.get("analyzeResult", payload)
    kvs: list[dict] = []
    unmapped: list[str] = []

    for kp in result.get("keyValuePairs") or []:
        key = kp.get("key") or {}
        val = kp.get("value") or {}
        label = _clean_label(key.get("content", ""))
        if not label:
            continue
        field = LABEL_MAP.get(label.lower())
        if field is None:
            if label not in unmapped:
                unmapped.append(label)
            continue
        kvs.append({
            "field": field,
            "value": re.sub(r"\s+", " ", str(val.get("content", ""))).strip(),
            # A key-value pair always carries a confidence. A missing one is
            # a service change, not a zero — do not silently treat it as
            # garbage the thresholds will flag.
            "confidence": float(kp.get("confidence", 0.0)),
            "page": _page_of(key) or _page_of(val),
        })
    return kvs, unmapped


def pages_returned(payload: dict) -> int:
    result = payload.get("analyzeResult", payload)
    return len(result.get("pages") or [])
```

Add `test_clean_label()` and `test_adapt()` to `main()`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python threshold_game/selftest_docint.py`
Expected: `PASS — docint offline checks.`

- [ ] **Step 5: Commit**

```bash
git add threshold_game/docint.py threshold_game/selftest_docint.py
git commit -m "feat(docint): label adapter preserving duplicate fields"
```

---

### Task 5: `providers.py` — the seam and the response cache

**Files:**
- Create: `threshold_game/providers.py`
- Test: `threshold_game/selftest_providers.py` (create)

**Interfaces:**
- Consumes: `game.DOCUMENTS`, `render.build_pdf`, `render.fingerprint`, `docint.analyze`, `docint.adapt`, `docint.pages_returned`.
- Produces:
  - `providers.get_documents(provider: str, settings=None, on_progress=None, analyze_fn=None) -> tuple[list[dict], dict]` — the documents and a report dict `{"unmapped": [...], "truncated": [...], "empty": [...], "cached": int, "billed": int}`.
  - `providers.CACHE_DIR: Path`.
  - `providers.clear_cache() -> int`.
  - `providers.search_bounds(documents, trials=2000, seed=7) -> tuple[int, int]` — the tuning ceiling and best-known score for a document set, by search.

- [ ] **Step 1: Write the failing test**

Create `threshold_game/selftest_providers.py`:

```python
"""selftest_providers.py — the seam, with a fake service. No credentials."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import providers
from game import DEFAULT_CHECKS, DEFAULT_THRESHOLDS, DOCUMENTS, play

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")


def fake_analyze(pdf: bytes, doc_id: str, settings) -> dict:
    """Stand in for the service: echo the mock kv back as DI-shaped pairs,
    but with every confidence pinned to 0.99 — which is what a real service
    reading a CLEAN pdf actually does, and the reason render.py degrades."""
    import render
    doc = next(d for d in DOCUMENTS if d["id"] == doc_id)
    pairs = []
    for kv in doc["kv"]:
        pairs.append({
            "key": {"content": render.LABELS[kv["field"]] + ":",
                    "boundingRegions": [{"pageNumber": kv.get("page", 1)}]},
            "value": {"content": str(kv["value"]),
                      "boundingRegions": [{"pageNumber": kv.get("page", 1)}]},
            "confidence": 0.99,
        })
    return {"analyzeResult": {"pages": [{"pageNumber": 1}, {"pageNumber": 2}],
                              "keyValuePairs": pairs}}


def main() -> int:
    # 1. mock is unchanged and needs no settings.
    docs, report = providers.get_documents("mock")
    check("mock is the same object list", docs, DOCUMENTS)
    check("mock bills nothing", report["billed"], 0)

    # 2. azure, through the fake, returns ten documents.
    providers.clear_cache()
    calls: list[str] = []

    def counting(pdf, doc_id, settings):
        calls.append(doc_id)
        return fake_analyze(pdf, doc_id, settings)

    adocs, areport = providers.get_documents(
        "azure", settings=object(), analyze_fn=counting)
    check("azure doc count", len(adocs), 10)
    check("azure called once per document", len(calls), 10)

    # 3. TRUTH IS NEVER TAKEN FROM THE SERVICE. This is the load-bearing
    #    assertion of the whole change: if truth came from the service, the
    #    game would be scoring the service against itself.
    for a, m in zip(adocs, DOCUMENTS):
        check(f"{m['id']} truth preserved", a["truth"], m["truth"])
        check(f"{m['id']} id preserved", a["id"], m["id"])
        check(f"{m['id']} note preserved", a["note"], m["note"])
        check(f"{m['id']} hint preserved", a["hint"], m["hint"])

    # 4. The kv DID come from the service — the fake pins 0.99.
    check("azure confidence used", adocs[0]["kv"][0]["confidence"], 0.99)

    # 5. The engine scores azure documents without knowing the difference.
    scored = play(dict(DEFAULT_THRESHOLDS), dict(DEFAULT_CHECKS),
                  documents=adocs)
    check("engine scored all ten", len(scored["rows"]), 10)

    # 6. Second call is served from cache — no further billed calls.
    calls.clear()
    providers.get_documents("azure", settings=object(), analyze_fn=counting)
    check("cache prevented re-billing", len(calls), 0)

    # 7. APP-0005's duplicate estimates survive the round trip.
    five = next(d for d in adocs if d["id"] == "APP-0005")
    dupes = [k for k in five["kv"] if k["field"] == "repair_estimate_total"]
    check("APP-0005 conflict survives", len(dupes), 2)

    # 8. search_bounds returns a sane ordered pair. Few trials on purpose —
    #    this asserts the shape and the ordering, not the numbers.
    ceiling, best = providers.search_bounds(trials=40, seed=1)
    check("ceiling is an int", isinstance(ceiling, int), True)
    check("best >= ceiling", best >= ceiling, True)

    # 9. A response with no keyValuePairs member is reported, not swallowed.
    #    That is exactly what the service returns when the
    #    features=keyValuePairs flag is missing: HTTP 200, recognised text,
    #    and no pairs. Silently, it looks like eighty missing fields.
    providers.clear_cache()
    _, empty_report = providers.get_documents(
        "azure", settings=object(),
        analyze_fn=lambda pdf, doc_id, st_: {"analyzeResult": {
            "pages": [{"pageNumber": 1}]}})
    check("every document reported empty", len(empty_report["empty"]), 10)

    providers.clear_cache()

    if FAILS:
        print(f"FAIL — {len(FAILS)} problem(s):")
        for f in FAILS:
            print("  " + f)
        return 1
    print("PASS — providers seam: truth preserved, cache works, conflict survives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python threshold_game/selftest_providers.py`
Expected: `ModuleNotFoundError: No module named 'providers'`

- [ ] **Step 3: Write `providers.py`**

```python
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

import json
import random
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


def _cache_file(doc_id: str, fingerprint: str) -> Path:
    # The fingerprint is in the NAME, so changing a degradation parameter
    # invalidates the entry automatically instead of serving a response that
    # belongs to a different image.
    return CACHE_DIR / f"{doc_id}-{fingerprint}.json"


def get_documents(provider: str, settings: docint.Settings | None = None,
                  on_progress=None, analyze_fn=None):
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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    out: list[dict] = []
    report = {"unmapped": [], "truncated": [], "empty": [], "cached": 0,
              "billed": 0}

    for i, doc in enumerate(DOCUMENTS):
        if on_progress:
            on_progress(i, len(DOCUMENTS), doc["id"])

        fp = render.fingerprint(doc)
        cache = _cache_file(doc["id"], fp)

        if cache.exists():
            payload = json.loads(cache.read_text())
            report["cached"] += 1
        else:
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
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python threshold_game/selftest_providers.py`
Expected: `PASS — providers seam: truth preserved, cache works, conflict survives.`

- [ ] **Step 5: Commit**

```bash
git add threshold_game/providers.py threshold_game/selftest_providers.py
git commit -m "feat(providers): mock/azure seam with response cache"
```

---

### Task 6: `app.py` — credentials sidebar and the analyse action

**Files:**
- Modify: `app.py` (imports at 13-24; new sidebar section before the Presets block at ~70)

**Interfaces:**
- Consumes: `providers.get_documents`, `providers.clear_cache`, `docint.Settings`, `docint.DocIntError`.
- Produces: `st.session_state.azure_docs` (list or `None`), `st.session_state.azure_report` (dict or `None`), `st.session_state.azure_error` (str or `None`).

- [ ] **Step 1: Extend the imports**

In `app.py`, after the existing `from game import ...` line, add:

```python
import docint                                              # noqa: E402
import providers                                           # noqa: E402
```

- [ ] **Step 2: Extend `init()`**

```python
def init():
    if "th" not in st.session_state:
        st.session_state.th = dict(DEFAULT_THRESHOLDS)
    if "ck" not in st.session_state:
        st.session_state.ck = dict(DEFAULT_CHECKS)
    if "best" not in st.session_state:
        st.session_state.best = None
    for k in ("azure_docs", "azure_report", "azure_error", "azure_bounds"):
        if k not in st.session_state:
            st.session_state[k] = None
```

- [ ] **Step 3: Add the sidebar section**

In `app.py`, immediately after `st.markdown("### Your settings")` and **before** the `**Presets**` block, insert:

```python
    with st.expander("🔌 Azure Document Intelligence", expanded=False):
        st.caption("Optional. Reads the same ten documents with the live "
                   "service so you can see which of your conclusions survive "
                   "it. Credentials are held for this browser session only "
                   "and are never written to disk.")

        endpoint = st.text_input(
            "Endpoint", value="", placeholder="https://<resource>.cognitiveservices.azure.com/",
            key="az_endpoint")
        key = st.text_input("Key", value="", type="password", key="az_key")

        with st.expander("advanced"):
            model = st.text_input("Model", "prebuilt-layout", key="az_model")
            api_version = st.text_input("api-version", "2024-11-30", key="az_api")
            route = st.selectbox(
                "Route", ["auto", "documentintelligence", "formrecognizer"],
                key="az_route",
                help="A 404 is almost always this, not the model name.")

        go = st.button("Connect & analyse", use_container_width=True,
                       type="primary", disabled=not (endpoint and key))

        if go:
            # One call per press. Streamlit re-runs this whole script on
            # every widget change, so an unguarded call here would re-bill
            # all ten documents on each slider drag.
            settings = docint.Settings(endpoint=endpoint, key=key, model=model,
                                       api_version=api_version, route=route)
            bar = st.progress(0.0, text="starting")
            try:
                docs, report = providers.get_documents(
                    "azure", settings=settings,
                    on_progress=lambda i, n, d: bar.progress(
                        i / n, text=f"{d}  ({i}/{n})"))
                st.session_state.azure_docs = docs
                st.session_state.azure_report = report
                # Searched ONCE, here — not on every rerun. See the docstring
                # on search_bounds for why this is a search and not a max
                # over the named strategies.
                bar.progress(1.0, text="measuring the live tuning ceiling")
                st.session_state.azure_bounds = providers.search_bounds(docs)
                st.session_state.azure_error = None
            except docint.DocIntError as e:
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.session_state.azure_error = str(e)
            except Exception as e:                      # noqa: BLE001
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.session_state.azure_error = f"{type(e).__name__}: {e}"
            bar.empty()
            st.rerun()

        if st.session_state.azure_error:
            st.error(st.session_state.azure_error)

        if st.session_state.azure_docs:
            r = st.session_state.azure_report
            st.success(f"Live results loaded — {r['billed']} analysed, "
                       f"{r['cached']} from cache.")
            if r["empty"]:
                st.error(
                    f"{len(r['empty'])} document(s) came back with no "
                    f"key-value pairs at all: {', '.join(r['empty'])}.\n\n"
                    f"If that is ALL of them, the model is returning text but "
                    f"no pairs — check that the model is `prebuilt-layout`, "
                    f"which is the only one that carries the "
                    f"`features=keyValuePairs` flag. If it is one or two, "
                    f"those pages are degraded past what the service can "
                    f"read; ease off their profile in `render.PARAMS`.")
            if r["truncated"]:
                st.warning("Pages the service did not read:\n\n"
                           + "\n".join(f"- {t}" for t in r["truncated"]))
            if r["unmapped"]:
                with st.expander(f"{len(r['unmapped'])} unmapped labels"):
                    st.caption("Labels the service returned that the adapter "
                               "has no field for. Not an error — but a field "
                               "you expected and cannot see is probably here.")
                    for u in r["unmapped"]:
                        st.write(f"- {u}")
            c1, c2 = st.columns(2)
            if c1.button("Clear results", use_container_width=True):
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.rerun()
            if c2.button("Clear cache", use_container_width=True,
                         help="Forces the next run to re-analyse, and to bill."):
                n = providers.clear_cache()
                # Clear all three together. Leaving azure_report or
                # azure_bounds behind shows a previous run's numbers beside a
                # state the user just cleared.
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.toast(f"Removed {n} cached responses.")
                st.rerun()
```

- [ ] **Step 4: Verify the app still runs in mock mode**

Run: `.venv/bin/python -c "import sys; sys.argv=['app.py']; import runpy; runpy.run_path('app.py', run_name='__main__')" 2>&1 | tail -3`
Expected: no traceback. (Streamlit prints bare-mode warnings; those are fine.)

Then run it for real and confirm the board is unchanged with no credentials entered:

```bash
.venv/bin/streamlit run app.py --server.port 8501 --server.headless true
```

Expected: the expander appears, **Connect & analyse** is disabled until both fields are filled, and every existing tab behaves exactly as before.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat(app): azure credentials sidebar and analyse action"
```

---

### Task 7: `app.py` — the side-by-side board

**Files:**
- Modify: `app.py` (the play/header block at ~110-145, the tabs block at ~148 onward)

**Interfaces:**
- Consumes: `st.session_state.azure_docs`, `game.play(..., documents=)`, `game.strategy`.
- Produces: nothing downstream.

- [ ] **Step 1: Score both providers**

Replace the `# ---- play` block (currently `res = play(...)` through the `st.session_state.best` update) with:

```python
# ------------------------------------------------------------------ play
res = play(st.session_state.th, st.session_state.ck)
tot = res["total"]

AZ = st.session_state.azure_docs
az_res = (play(st.session_state.th, st.session_state.ck, documents=AZ)
          if AZ else None)
az_tot = az_res["total"] if az_res else None

if st.session_state.best is None or tot["score"] > st.session_state.best:
    st.session_state.best = tot["score"]
```

- [ ] **Step 2: Add the live bounds beside the mock constants**

**Leave `TUNING_CEILING = 252` and `GLOBAL_BEST = 720` at `app.py:130-131` exactly as they are.** Add one line directly beneath them:

```python
AZ_CEILING, AZ_BEST = st.session_state.azure_bounds or (None, None)
```

Do not replace the mock constants with a computed value. They came from a threshold search, and the board quotes them verbatim in learner-facing copy in the "Strategy comparison" tab ("tops out at 252", "best known score is 720"). Recomputing them from the seven named strategies yields **-488** and **576** — the named strategies are not where those numbers came from, and substituting them would silently rewrite the session narrative.

The live equivalents are searched for once, in Task 6's button handler, by `providers.search_bounds`. They are a lower bound, and the UI must say so.

- [ ] **Step 3: Add the second headline score**

Immediately after the existing `k[5].progress(pct)` line, add:

```python
if az_tot:
    st.markdown("")
    a = st.columns([1.6, 1, 1, 1, 1, 1.2])
    az_colour = "#1B7F5A" if az_tot["score"] > 0 else "#C0223B"
    a[0].markdown(
        f"<div class='bigscore' style='color:{az_colour}'>{az_tot['score']}</div>"
        f"<div class='sub'>same policy, scored on what the LIVE service read</div>",
        unsafe_allow_html=True)
    a[1].metric("Correct accepts", az_tot["correct_accepts"],
                delta=az_tot["correct_accepts"] - tot["correct_accepts"])
    a[2].metric("WRONG accepts", az_tot["wrong_accepts"],
                delta=az_tot["wrong_accepts"] - tot["wrong_accepts"],
                delta_color="inverse")
    a[3].metric("Flagged", az_tot["flags"],
                delta=az_tot["flags"] - tot["flags"], delta_color="inverse")
    a[4].metric("Missing", az_tot["missing"],
                delta=az_tot["missing"] - tot["missing"], delta_color="inverse")
    a[5].markdown(f"<div class='sub'>live tuning ceiling {AZ_CEILING}<br>"
                  f"live best found {AZ_BEST}<br>"
                  f"<i>2,000-trial search — a lower bound, and not derived "
                  f"the same way as the mock's {TUNING_CEILING}/"
                  f"{GLOBAL_BEST}</i></div>", unsafe_allow_html=True)
```

- [ ] **Step 4: Add the "Mock vs live" tab**

Change the tabs line to:

```python
tab_names = ["Per document", "Field detail", "How scoring works",
             "Strategy comparison"]
if az_res:
    tab_names.append("Mock vs live")
tabs = st.tabs(tab_names)
t1, t2, t3, t4 = tabs[0], tabs[1], tabs[2], tabs[3]
t5 = tabs[4] if az_res else None
```

Then append at the end of the file:

```python
if t5 is not None:
    with t5:
        st.caption("The same policy, the same scoring engine, two sources of "
                   "extraction. Everything here is a difference — matching "
                   "rows are omitted.")

        flipped = []
        for doc in DOCUMENTS:
            mf = res["per_doc"][doc["id"]]["result"]["fields"]
            af = az_res["per_doc"][doc["id"]]["result"]["fields"]
            for name in sorted(set(mf) | set(af)):
                m, a = mf.get(name), af.get(name)
                mv = m.verdict if m else "absent"
                av = a.verdict if a else "absent"
                if mv == av and (m and a and abs(m.confidence - a.confidence) < 0.02):
                    continue
                flipped.append({
                    "document": doc["id"],
                    "field": name,
                    "mock conf": f"{m.confidence:.2f}" if m else "—",
                    "live conf": f"{a.confidence:.2f}" if a else "—",
                    "mock": mv,
                    "live": av,
                    "changed": "VERDICT" if mv != av else "confidence",
                })

        verdict_flips = [f for f in flipped if f["changed"] == "VERDICT"]
        if verdict_flips:
            st.error(f"{len(verdict_flips)} field(s) got a DIFFERENT verdict "
                     f"against the live service under your current settings.")
        else:
            st.success("No verdict changed. Every accept/flag decision your "
                       "policy makes survived the real service.")

        st.dataframe(flipped, use_container_width=True, hide_index=True)

        st.markdown(f"""
**Read this table before you trust your score.**

Your policy scores **{tot['score']}** on the mock and **{az_tot['score']}**
on what the service actually read. The gap is not the service being worse
than the mock, and it is not the mock lying. It is the difference between a
document you designed and a document you received.

The rows marked **VERDICT** are the ones that matter: a threshold that caught
something in the mock and missed it live, or the reverse. A threshold tuned
against one extraction is tuned against that extraction — not against the
field, and not against next month's scans.
""")
```

- [ ] **Step 5: Add an azure column to the strategy table**

Inside `with t4:`, change the loop that builds `rows` to:

```python
    rows = []
    for n in names:
        t, c = strategy(n)
        r = play(t, c)["total"]
        row = {"strategy": labels[n], "score": r["score"],
               "correct": r["correct_accepts"],
               "WRONG": r["wrong_accepts"], "flags": r["flags"]}
        if AZ:
            row["live score"] = play(t, c, documents=AZ)["total"]["score"]
        rows.append(row)
    yours = {"strategy": "— your settings —", "score": tot["score"],
             "correct": tot["correct_accepts"], "WRONG": tot["wrong_accepts"],
             "flags": tot["flags"]}
    if AZ:
        yours["live score"] = az_tot["score"]
    rows.append(yours)
```

- [ ] **Step 6: Verify**

Run: `.venv/bin/python threshold_game/selftest_seam.py && .venv/bin/python verify.py`
Expected: both PASS. Scoring must not have drifted.

Confirm the mock's published constants are untouched and the named-strategy scores still match the board's copy:

```bash
grep -n "TUNING_CEILING = 252" app.py && grep -n "GLOBAL_BEST = 720" app.py
.venv/bin/python -c "
import sys; sys.path.insert(0,'threshold_game')
from game import play, strategy
print('brief      ', play(*strategy('brief'))['total']['score'], '(want -600)')
print('checks_only', play(*strategy('checks_only'))['total']['score'], '(want 576)')
"
```

Expected: both greps hit, and the two scores are -600 and 576. The 576 is quoted verbatim in the strategy tab's narrative; if it moved, Task 1 changed scoring — stop and fix that before going on.

Then run the app and confirm that with no credentials entered nothing has visibly changed.

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "feat(app): side-by-side mock vs live board"
```

---

### Task 8: Calibration tool, ignore rules, and the README

**Files:**
- Create: `threshold_game/calibrate.py`
- Modify: `.gitignore` (repo root), `README.md`

**Interfaces:**
- Consumes: `render`, `docint`, `providers`.
- Produces: nothing importable.

- [ ] **Step 1: Ignore the cache and the venv**

Append to the repo-root `.gitignore` (verify `.venv/` is already there before adding it again):

```
# Threshold_Game — cached Document Intelligence responses and rendered PDFs
**/out/azure/
```

**The leading `**/` is required, not decorative.** Git anchors any pattern
containing a slash anywhere but at the end to the directory of the `.gitignore`
that declares it. The repo root's `.gitignore` sits many levels above
`Threshold_Game/`, so a bare `out/azure/` would only ever match
`<repo-root>/out/azure/` and would silently fail to ignore the cache. Verify
with `git check-ignore -v out/azure/di_raw` rather than assuming.

- [ ] **Step 2: Write `calibrate.py`**

```python
"""
calibrate.py — measure what the live service actually reports, per document.

TRAINER TOOL. Not part of the learner flow, and not reachable from the board.

render.py claims APP-0010 will come back around 0.55-0.67 and APP-0003 around
0.69-0.90. Those are targets, not facts. This prints what the service really
says so PARAMS can be tuned until the claim is true.

    AZURE_DOCINT_ENDPOINT=https://... AZURE_DOCINT_KEY=... \
        .venv/bin/python threshold_game/calibrate.py

Credentials are read from the environment at run time and are never written
anywhere. Each run costs ten analyses unless they are already cached.
"""

from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docint
import providers
import render
from game import DOCUMENTS

TARGETS = {
    "clean":       (0.90, 0.97),
    "light":       (0.87, 0.91),
    "handwriting": (0.69, 0.90),
    "poor_scan":   (0.55, 0.67),
}


def main() -> int:
    endpoint = os.environ.get("AZURE_DOCINT_ENDPOINT", "")
    key = os.environ.get("AZURE_DOCINT_KEY", "")
    if not (endpoint and key):
        print("Set AZURE_DOCINT_ENDPOINT and AZURE_DOCINT_KEY in the "
              "environment.\nThey are read at run time and never stored.")
        return 2

    settings = docint.Settings(endpoint=endpoint, key=key)
    docs, report = providers.get_documents(
        "azure", settings=settings,
        on_progress=lambda i, n, d: print(f"  [{i}/{n}] {d}", flush=True))

    print(f"\n{report['billed']} analysed, {report['cached']} from cache.\n")
    print(f"{'document':<12}{'profile':<14}{'n':>3}  {'min':>6}{'mean':>7}"
          f"{'max':>7}   target        verdict")
    print("-" * 78)

    out_of_band = 0
    for doc in docs:
        confs = [k["confidence"] for k in doc["kv"]]
        prof = render.PROFILES[doc["id"]]
        lo, hi = TARGETS[prof]
        if not confs:
            print(f"{doc['id']:<12}{prof:<14}{0:>3}  "
                  f"{'— no fields returned —':>27}   "
                  f"{lo:.2f}-{hi:.2f}   TOO DEGRADED")
            out_of_band += 1
            continue
        mn, mean, mx = min(confs), statistics.mean(confs), max(confs)
        if mean < lo:
            verdict, bad = "too low  (ease off)", True
        elif mean > hi:
            verdict, bad = "too high (degrade more)", True
        else:
            verdict, bad = "in band", False
        out_of_band += bad
        print(f"{doc['id']:<12}{prof:<14}{len(confs):>3}  {mn:>6.3f}{mean:>7.3f}"
              f"{mx:>7.3f}   {lo:.2f}-{hi:.2f}   {verdict}")

    print()
    if report["unmapped"]:
        print("Unmapped labels (add to docint.LABEL_MAP if a field is missing):")
        for u in report["unmapped"]:
            print(f"  {u}")
        print()
    if out_of_band:
        print(f"{out_of_band} document(s) outside their target band. "
              f"Adjust render.PARAMS, then:")
        print("  .venv/bin/python -c \"import sys;sys.path.insert(0,"
              "'threshold_game');import providers;providers.clear_cache()\"")
        print("and run this again — the cache is keyed on the image "
              "fingerprint, so changed parameters re-analyse automatically.")
        return 1
    print("All documents inside their target confidence bands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run the offline suite end to end**

```bash
.venv/bin/python threshold_game/selftest_seam.py && \
.venv/bin/python threshold_game/selftest_render.py && \
.venv/bin/python threshold_game/selftest_docint.py && \
.venv/bin/python threshold_game/selftest_providers.py && \
.venv/bin/python verify.py
```

Expected: five PASS lines. Every one of these is free. Do not spend money before they all pass.

- [ ] **Step 4: Document it in the README**

After the existing section `### 2 · The Streamlit board — nicer, needs one install`, insert:

```markdown
### 2a · The Streamlit board against the LIVE service

The board can read the same ten documents with a real Azure AI Document
Intelligence resource and score your policy against both, side by side.

Open the **🔌 Azure Document Intelligence** panel in the sidebar, paste an
endpoint and key, and press **Connect & analyse**. The credentials are held
for that browser session only — there is no `.env`, and nothing is written to
disk. Responses are cached under `out/azure/`, so moving a slider afterwards
costs nothing.

Then open the **Mock vs live** tab. The rows marked `VERDICT` are the point:
those are the fields where your threshold decided one thing against the mock
and something else against the real service.

**What to know before you run it.**

The ten documents are rendered to PDFs and then deliberately degraded — APP-0003
wobbles like handwriting, APP-0010 is a bad photocopy. That is not decoration.
Document Intelligence reading a clean generated PDF returns ~0.99 confidence on
everything, which would leave every threshold below 0.98 accepting everything
and turn the slider into a no-op. The damage is what makes the service
genuinely unsure, so the confidence it reports is real.

If a document comes back with no fields at all, it is degraded too far for the
service to read. Check it with:

    AZURE_DOCINT_ENDPOINT=... AZURE_DOCINT_KEY=... \
        .venv/bin/python threshold_game/calibrate.py

which prints measured confidence per document against its target band.

Everything else — `play.py`, the HTML board — stays mock-only. Neither has
anywhere safe to put a key.
```

- [ ] **Step 5: Commit**

```bash
git add threshold_game/calibrate.py README.md .gitignore
git commit -m "feat(calibrate): confidence measurement tool, docs and ignores"
```

---

### Task 9: Live calibration run (billed — needs a real resource)

**Files:**
- Modify: `threshold_game/render.py` (`PARAMS` only, if the bands are missed)

This task cannot be done by an agent without credentials. It is here so it is not forgotten.

- [ ] **Step 1: Confirm the offline suite passes** (Task 8 Step 3). Never spend before this.

- [ ] **Step 2: Run the calibration**

```bash
AZURE_DOCINT_ENDPOINT=https://<resource>.cognitiveservices.azure.com/ \
AZURE_DOCINT_KEY=<key> \
.venv/bin/python threshold_game/calibrate.py
```

Cost: ten analyses on the first run, free on repeats until `render.PARAMS` changes.

- [ ] **Step 3: Tune and repeat**

For each document reported `too high`, increase `blur`/`noise` and drop `jpeg` quality for that profile. For `too low` or `TOO DEGRADED`, ease off. Change one profile at a time, re-run, and expect two or three rounds.

- [ ] **Step 4: Record the outcome**

If a profile cannot be made to land in band — in particular if `poor_scan` goes straight from confident to returning nothing — record that in the spec's §7 and decide which lesson APP-0010 teaches: *the service was unsure* (low confidence, scored by threshold) or *the service could not read it* (missing fields, −20 each). Both are legitimate; they are not the same lesson, and the session narrative needs to pick one.

- [ ] **Step 5: Commit any parameter changes**

```bash
git add threshold_game/render.py
git commit -m "fix(render): degradation parameters calibrated against the live service"
```

---

## Verification checklist

Run before calling this done:

```bash
.venv/bin/python threshold_game/selftest_seam.py       # play(documents=)
.venv/bin/python threshold_game/selftest_render.py     # 2 pages, deterministic
.venv/bin/python threshold_game/selftest_docint.py     # adapter, D.O.B ., dupes
.venv/bin/python threshold_game/selftest_providers.py  # truth preserved, cache
.venv/bin/python verify.py                             # JS/Python parity
```

Then, per `CLAUDE.md`: this is still a draft until it has been migrated to the
Techademy-provided Azure VM and tested there. "Works in `.venv`" is not done.
