# The Threshold Game — live Azure Document Intelligence provider

**Date:** 2026-08-18
**Status:** approved design, not yet implemented
**Scope:** `Threshold_Game/` only

---

## 1. What this adds and why

The Threshold Game currently plays against ten hand-authored `DOCUMENTS`
dicts in `threshold_game/game.py`. Each carries a `kv` list (field, value,
confidence, page) and a `truth` dict. Nothing ever touches a real service.

This change adds a second data source: the ten documents rendered as PDFs and
read by a live **Azure AI Document Intelligence** resource, so learners can ask
the only question the mock cannot answer — *how much of what I concluded
survives contact with the real service?*

The mock stays the default and stays unchanged. Azure is opt-in, Streamlit-only,
and additive.

### The design constraint that shapes everything

Real Document Intelligence, reading a cleanly generated PDF, returns a mean
confidence of **0.990** (measured on 2026-08-16 during the Day-14 lab, same
resource family, same API version). The mock's mean is 0.892, and its spread
from 0.55 to 0.97 is the entire tuning axis of this game.

If azure mode fed 0.99 into every field, every threshold below 0.98 would accept
everything, the tuning ceiling of 252 would be meaningless, and APP-0003 and
APP-0010 would stop behaving as designed. **The threshold slider — the thing the
game is named after — would do nothing.**

So the PDFs are deliberately degraded before they are sent, until the service
reports genuine uncertainty for genuine reasons. This is decision **D1** below.

---

## 2. Decisions taken

| # | Decision | Rejected alternatives |
|---|---|---|
| D1 | **Degrade the rendered PDFs** so DI returns real low confidence | Feed real 0.99 confidences and accept that tuning goes quiet; keep azure values but substitute mock confidences (fiction in a game about what confidence means) |
| D2 | **Credentials via sidebar only**, held in `st.session_state`, never written to disk | `.env` reusing the Day-14 resource; `.env` with sidebar override |
| D3 | **Side-by-side mock vs azure** scoring of the same policy | Straight flip with one scoreboard; adding bring-your-own-document upload |
| D4 | **Streamlit only.** `play.py` and the HTML board stay mock-only | A shared seam across all three surfaces |
| D5 | **Ground truth stays hand-authored.** Azure supplies `kv` only | Deriving truth from the service (circular — it would score the service against itself) |
| D6 | **Render onto at most two pages**, always | Requiring an S0-tier resource to lift the F0 two-page cap |

D4 follows from D2: with no credentials on disk, a learner-facing CLI has no way
to obtain a key, so `play.py` and the HTML board stay mock-only.

The one exception is `calibrate.py` (§4.6), a trainer-only tool that reads
credentials from an environment variable at run time. It is not part of the
learner flow, is never invoked by the Streamlit app, and still writes no
credentials to disk — so it is consistent with D2, not an exception to it.

---

## 3. Architecture

```
app.py                       Streamlit board — provider toggle, creds, comparison
threshold_game/
  game.py                    engine (ONE line changes: play() takes documents=)
  providers.py           NEW the seam: get_documents(provider, creds)
  render.py              NEW ten DOCUMENTS -> degraded 2-page PDFs
  docint.py              NEW Azure client + label->field adapter
  calibrate.py           NEW trainer-only CLI: measure confidence, tune degradation
  selftest_docint.py     NEW offline adapter tests, no credentials, no cost
play.py                      unchanged (mock only)
build_html.py                unchanged
verify.py                    unchanged — must still pass
out/azure/di_raw/        NEW git-ignored cache of raw service responses
```

Modules inside `threshold_game/` import each other flat (`import game`), matching
the existing `sys.path.insert` pattern at `app.py:20`.

### Data flow, azure path

```
game.DOCUMENTS[i]
      |  render.build_pdf(doc)            deterministic, seeded, <=2 pages
      v
   PDF bytes  --> out/azure/di_raw/ cache hit? --> skip the call
      |  docint.analyze(pdf, settings)    prebuilt-layout + keyValuePairs
      v
   raw DI JSON
      |  docint.adapt(payload, doc_id)    label cleaning, dup preservation
      v
   kv list  +  game.DOCUMENTS[i]["truth"], ["note"], ["hint"], ["id"]
      v
   azure document dict  -->  game.play(thresholds, checks, documents=azure_docs)
```

The engine never learns which provider produced a document.

---

## 4. Component specifications

### 4.1 `game.py` — the only engine change

```python
def play(thresholds: dict, checks: dict, documents: list[dict] | None = None) -> dict:
    docs = documents if documents is not None else DOCUMENTS
    ...
```

Everything else in `game.py` is untouched. `process()` and `score_doc()` already
take a document as an argument. `strategy()` is provider-independent.

This keeps `play.py`, `build_html.py` and `verify.py` working with no edits, which
matters: `verify.py` proves the JavaScript and Python engines agree, and that
proof must remain valid.

### 4.2 `render.py` — deterministic degraded PDFs

```python
PROFILES: dict[str, str]                    # doc_id -> profile name
def build_pdf(doc: dict) -> bytes           # PIL pages -> multi-page PDF
def build_all() -> dict[str, bytes]         # doc_id -> pdf bytes
```

Each page is drawn as a PIL image at 150 DPI (1240x1754), degraded, then saved
via `Image.save(..., save_all=True)`. Degradation uses `random.Random(seed)` keyed
on the document id, so output bytes are identical run to run — calibration is
meaningless otherwise.

| Profile | Documents | Treatment | Target confidence band |
|---|---|---|---|
| `clean` | 0001, 0004, 0005, 0006, 0007, 0008, 0009 | none | 0.90 – 0.97 |
| `light` | 0002 | mild blur, 0.3 deg skew, light noise | 0.87 – 0.91 |
| `handwriting` | 0003 | per-character baseline jitter, thin stroke, blur | 0.69 – 0.90 |
| `poor_scan` | 0010 | heavy blur, gaussian noise, low contrast, JPEG artifacts | 0.55 – 0.67 |

The bands mirror the mock's existing confidences so the two columns are
comparable. They are **targets, not guarantees** — see §7.

**Fonts.** Probe an ordered candidate list (macOS `/System/Library/Fonts/
Supplemental/Arial.ttf`, Linux `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`,
and others) and raise a clear, actionable error if none is found. Never silently
fall back to PIL's bitmap font — it would wreck the confidence bands invisibly.

**Two-page rule (D6).** APP-0005's second estimate, APP-0008's driving licence and
APP-0010's total are authored on page 3. The free (F0) tier reads only the first
two pages and says nothing about it: HTTP 200, no warning, the `pages` array simply
ends. Because the credentials are supplied at runtime the tier is unknowable, so
every document renders onto at most two pages.

This is safe. Page numbers are display-only in the engine — `kv["page"]` feeds
`FieldResult.page` and the `dropped` list and is never consulted by a scoring or
check decision. Moving content from page 3 to page 2 changes nothing about what
these documents teach. Day-14's page-truncation check is still ported and will
report loudly if returned pages ever fall short of posted pages.

### 4.3 `docint.py` — client and adapter

```python
@dataclass
class Settings:                 # endpoint, key, model, api_version, timeout, use_cache
def analyze(pdf: bytes, doc_id: str, settings: Settings) -> dict
def adapt(payload: dict, doc_id: str) -> list[dict]      # -> game._kv-shaped dicts
def preflight(settings: Settings) -> None                # cheap credential check
```

Ported from the live-verified Day-14 `docint_lab/azure_docint.py`, retaining every
fact that module paid for:

- `prebuilt-layout` with **`features=keyValuePairs`**. `prebuilt-document` was
  retired in the 2024-11-30 GA API; without the feature flag the call returns
  HTTP 200 and zero pairs.
- api-version `2024-11-30` implies the **`/documentintelligence/`** route.
  Earlier versions use `/formrecognizer/`. A mismatch 404s with a message about
  the *model*, which sends you hunting in the wrong place.
- **`D.O.B.:` comes back as `D.O.B .:`** — DI tokenises the final full stop of an
  abbreviation as its own word. `_clean_label` must run
  `re.sub(r"\s+([.,;:])", r"\1", text)` before mapping. Missing this silently
  cost three fields on Day-14.
- Strip only the trailing **colon** from a DI key, never the full stop —
  `D.O.B.`, `Sum Ins.`, `Claim Ref.`, `Policy No.` are all legitimate aliases.
- Retry HTTP 429 honouring `Retry-After` (Day-15's fix). Day-14 raised instead,
  which does not survive a ten-document run.

**One deliberate change from Day-14:** `adapt()` returns a **list and preserves
duplicate field names**. Day-14 keyed results by field name. Here, APP-0005's two
conflicting `repair_estimate_total` values are the entire point of that document —
collapsing them leaves the `conflict` check with nothing to fire on.

`LABEL_MAP` covers the nine scored fields plus `parts_subtotal`, `labour_subtotal`,
`driving_licence_number` and `licence_postcode`, since the `arithmetic` and
`scope_gate` checks depend on those four.

Confidence per field comes from the key-value pair's own `confidence`.

### 4.4 `providers.py` — the seam

```python
def get_documents(provider: str, creds: Settings | None = None) -> list[dict]
def describe(provider: str, creds: Settings | None) -> str
```

`mock` returns `game.DOCUMENTS`. `azure` renders, analyses, adapts, and merges the
service's `kv` onto each mock document's `id`, `note`, `hint` and `truth` (D5).

### 4.5 `app.py` — the board

**Sidebar**, new section above Presets:

- Radio: `Mock (offline)` / `Azure Document Intelligence (live)`
- When azure: endpoint and key as `type="password"` inputs; model and api-version
  in an expander with working defaults; a **Connect & analyse** button; a progress
  bar over the ten documents; a status line; a **Clear cached results** button.
- The key is never rendered back to the page, not even masked with a suffix.

**Analysis runs only on the button press.** Streamlit re-runs the entire script on
every widget change, so an unguarded call would re-bill all ten documents on each
slider drag.

**Caching.** Raw service responses are cached under `out/azure/di_raw/`
(git-ignored) keyed by document id and a hash of the rendered PDF. Slider moves
are free, and a mid-session restart does not re-bill ten documents across fifteen
pods. Only responses are cached — never credentials. The forms are synthetic
(`[PERSON_01]`), so the cache holds nothing sensitive.

**Main board**, once azure results exist:

- Two headline scores side by side, mock and azure, for the same policy.
- Two mini-scores per document card in the *Per document* tab.
- A provider selector on the *Field detail* table.
- An azure column in the *Strategy comparison* table.
- A new tab, **Mock vs live**: per-field confidence delta, and the list of
  verdicts that flipped — which documents your thresholds caught in mock and
  missed against the real service. This table is the teaching payload of the
  whole change.

`TUNING_CEILING = 252` and `GLOBAL_BEST = 720` (`app.py:130-131`) are mock-specific
constants. Azure equivalents are recomputed at analyse time by re-running the
strategy sweep over the azure documents — no additional DI calls, only re-scoring
of cached results.

With no credentials entered, the app behaves exactly as it does today.

### 4.6 `calibrate.py` — trainer-only

A CLI that renders each document at several degradation strengths, analyses them,
and prints measured confidence per field per document, so the `render.PROFILES`
parameters can be tuned toward the target bands. Reads credentials from an
environment variable at run time and writes none. Not part of the learner flow.

---

## 5. Error handling

| Condition | Behaviour |
|---|---|
| No credentials entered | Azure panel shows "not configured"; board stays mock-only; no error |
| Bad endpoint or key | `preflight()` fails before any document is sent; one plain message naming the likely cause |
| HTTP 404 | Message states the route/api-version mismatch explicitly, since DI's own text blames the model |
| Zero key-value pairs returned | Message names the missing `features=keyValuePairs` flag |
| HTTP 429 | Retry honouring `Retry-After`; progress bar reports the wait |
| Returned pages < posted pages | Loud "pages the service did not read" warning naming the documents |
| A field the adapter cannot map | Recorded in an "unmapped labels" expander rather than dropped silently |
| Font not found | Hard error at render time with the paths tried |

---

## 6. Testing

| Test | Cost | Asserts |
|---|---|---|
| `verify.py` (existing, unchanged) | free | JS and Python engines still agree — scoring untouched |
| `selftest_docint.py` (new) | free | Adapter: `D.O.B .:` cleaning, colon-only stripping, duplicate-key preservation, confidence extraction, unmapped-label handling |
| Render tests (new) | free | Every document is <= 2 pages; two runs produce identical bytes |
| Streamlit smoke (existing pattern) | free | App runs in mock mode with no behaviour change |
| Live calibration run | **billed** | Measured confidences land in the §4.2 bands |

Everything except the last is offline and free, and must pass before any billed
run happens.

---

## 7. Risks

**Calibration is empirical and costs money.** How hard DI must be pushed before it
reports 0.6 cannot be known from here. Expect two or three billed rounds of ten
documents. Budget this before the session, not during it.

**Degradation may not produce the intended failure mode.** If DI stays confident
under heavy degradation, the likely outcome is that it stops returning fields at
all — APP-0010 becomes "the service could not read it" (missing fields, scored at
-20 each) rather than "the service was unsure" (low confidence, scored by
threshold). That is still a legitimate lesson, but it is a *different* lesson, and
it changes what APP-0010 demonstrates. Decide before the session which one is
being taught.

**The two columns will not match.** Real DI will not reproduce every mock defect
exactly, so some documents will score differently. This is the finding, not a
defect — but the session narrative should lead with it rather than be caught out
by it. Day-15's experience is the precedent worth repeating: there, azure results
were *identical* to mock, and saying so up front turned an apparent anticlimax
into the strongest point in the lab.

**New dependency.** Pillow is added to `requirements.txt`. The mock path keeps its
stdlib-only guarantee; only the azure path needs it.

---

## 8. Delivery note

Anything built in the local `.venv` is a draft. This lab must be migrated to the
delivery VM the cohort actually uses, and tested there, before it counts as done —
"works on my laptop" is not done for teaching material that fifteen people will run
simultaneously on someone else's machine.
