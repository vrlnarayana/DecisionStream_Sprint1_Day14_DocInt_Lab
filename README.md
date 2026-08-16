# Sprint 1 · Day 14 Lab — Document Intelligence Field Extraction

**Birlasoft FORGE FDE Academy · Confidential — For Learner Use Only**

Ten synthetic case application PDFs → key-value pairs → the DecisionStream
schema → a structured decision profile, with confidence handling and error
handling that survives a real form.

---

## It runs offline

```bash
python3 run_lab.py --compare
```

Standard library only. No Azure, no key, no `pip install`.

When you are ready for the real service, it is a line in `.env` — not a
rewrite. See [Running it against Azure](#running-it-against-azure) at the
bottom. That path is standard library too.

---

## Running the lab

### What you need

| Requirement | Detail |
|---|---|
| Python | **3.9 or newer.** `python3 --version` to check. Nothing else. |
| Packages | **None.** No `pip install`, no virtualenv, no `requirements.txt`. |
| Network | Not needed for the mock path — the whole lab runs on a plane. |
| Azure | Only for the optional azure path. See [the setup section](#running-it-against-azure). |

If `python3` is not on your PATH inside the Azure VM, use the full path VS Code
shows in its interpreter picker. Everything below is run from the lab folder:

```bash
cd DecisionStream_Sprint1_Day14_DocInt_Lab
```

### The five commands

```bash
python3 run_lab.py                          # default policy, flat_0.85
python3 run_lab.py --policy tiered          # one named policy
python3 run_lab.py --compare                # all three, with the summary
python3 run_lab.py --show APP-0003          # one document, every policy, in full
python3 run_lab.py --selftest               # prove the azure adapter, offline
```

Plus two for the azure path:

```bash
python3 run_lab.py --make-pdfs              # render docs/APP-XXXX.pdf
python3 run_lab.py --provider azure --compare
```

### Every flag

| Flag | What it does |
|---|---|
| `--policy NAME` | One of `flat_0.85`, `flat_0.85_checked`, `tiered`. Default `flat_0.85`. |
| `--compare` | Runs all three policies and prints the comparison summary. This is the one that makes the teaching point. |
| `--show DOC_ID` | Full field-by-field trace for one document under every policy — raw value, parsed value, confidence, verdict, and which validator fired. Use it the moment a number surprises you. |
| `--provider mock\|azure` | Overrides `DOCINT_PROVIDER` for a single run without editing `.env`. |
| `--make-pdfs` | Renders the ten documents to `docs/APP-XXXX.pdf`. Required once before the azure path. |
| `--selftest` | 141 offline assertions that the azure adapter reproduces the mock pipeline exactly. No network, no cost. |
| `-v`, `--verbose` | Prints every self-test check, not just failures. |

### The order to run them in

**1 · See what the brief actually asks for.**

```bash
python3 run_lab.py --policy flat_0.85
```

Read the `WRONG` column. Five values were accepted and are wrong. Nothing
errored; every one produced a well-formed record.

**2 · Look at the clearest one.**

```bash
python3 run_lab.py --show APP-0003
```

`policy_number` came back `MOT-45O3901` — letter `O` for zero — at confidence
**0.86**, which a 0.85 threshold accepts.

**3 · Compare all three policies.**

```bash
python3 run_lab.py --compare
```

Read the summary block at the bottom before the table. It tells you which of the
two changes actually bought you anything.

**4 · Then read your outputs**, in `out/mock/`. `extraction_report.md` ends with
six questions; they are the deliverable, not the table.

### What a run writes

Every run overwrites `out/<provider>/`. Nothing else on disk is touched.

| Path | What it is |
|---|---|
| `out/mock/extraction_report.md` | The report for your ADR, ending in six questions |
| `out/mock/field_results.csv` | 270 rows — every field, every document, every policy, every verdict |
| `out/mock/profiles/APP-XXXX.json` | What your Azure Function would return for that document |

`out/mock/` and `out/azure/` sit side by side deliberately. Comparing them is
the exercise.

### Reading the table

```
doc         acc   ok  WRONG  flag  miss  note
APP-0003      3    2      1     6     0  handwritten form  <-- policy_number=MOT-45O3901
```

| Column | Meaning |
|---|---|
| `acc` | Fields **accepted** into the decision profile without human review |
| `ok` | Of those, how many match ground truth |
| `WRONG` | **Accepted and wrong.** The only column that can hurt a customer. |
| `flag` | Sent to the review queue — a cost, not a failure |
| `miss` | Required field not extracted at all |

`acc` is not `ok + WRONG`: a field can be accepted and have no ground-truth
entry to check against. Judge a policy on `WRONG` first and `flag` second.

### If something goes wrong

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: mock_docint` | You ran a file inside `docint_lab/` directly. Always run `run_lab.py` from the lab root. |
| `python3: command not found` | Use the interpreter path from the VS Code picker. |
| Numbers differ from this README | Check the banner line — you are probably on `azure`, not `mock`. The two are not expected to match; that is the point. |

---

## The three policies, in the order you should run them

| Policy | What it is | Why run it |
|---|---|---|
| `flat_0.85` | **Exactly what the brief says.** Accept above 0.85, otherwise flag. Confidence and nothing else. | To see what gets through |
| `flat_0.85_checked` | Same threshold, but pattern and cross-field checks can override a high confidence | To see what fixed it |
| `tiered` | Per-field thresholds — identifiers at 0.97, names at 0.80 — plus the checks | To see whether tuning was worth it |

```bash
python3 run_lab.py --policy flat_0.85       # start here
python3 run_lab.py --show APP-0003          # then look at this document
python3 run_lab.py --compare                # then all three
```

---

## What you will find

### 1 · The brief's threshold lets five wrong values through

`flat_0.85` accepts **five values that are wrong**. None of them errors. Every
one produces a well-formed record.

The clearest is **APP-0003**, a handwritten form. Its policy number came back as
`MOT-45O3901` — a letter **O** where a **zero** should be — at confidence
**0.86**. A 0.85 threshold accepts it. The record is now attached to a policy
that does not exist.

> **Confidence is the model's certainty about what it saw.**
> **It is not a statement about whether the value is correct.**

### 2 · The checks, not the thresholds, did the work

Run `--compare` and read the summary at the bottom:

- Adding the **checks** removed **all five** wrong acceptances
- Adding **tiered thresholds** on top removed **zero more** — and added
  sixteen fields to the review queue

That is an uncomfortable result and it is the most useful thing in this lab.
Tightening thresholds *feels* safer, produces a bigger review queue, costs
handler time, and in this case bought nothing. The pattern check on the claim
reference is what caught the error, because it knows something the confidence
score never can: **what a valid claim reference looks like.**

Be ready to say that to a client who assumes tighter is safer.

### 3 · Flagging is not failure

A flagged field costs a handler thirty seconds. A wrongly accepted identifier
costs a rework cycle, a complaint, or a regulatory finding — and nobody knows it
happened until somebody notices downstream.

The only column that can hurt a customer is **wrong accepted**.

---

## The ten documents

| Doc | Pages | What makes it hard |
|---|---|---|
| APP-0001 | 2 | Clean baseline — everything present and confident |
| APP-0002 | 2 | Invented field labels: "D.O.B.", "Reg No", "Sum Ins." |
| APP-0003 | 2 | **Handwriting.** High confidence, wrong characters |
| APP-0004 | 1 | Two required fields simply absent |
| APP-0005 | 3 | Same field on two pages with **different values** |
| APP-0006 | 2 | A checkbox whose meaning **inverts** the sentence |
| APP-0007 | 3 | **Driving licence attached** — data you should not extract |
| APP-0008 | 2 | Line items do not sum to the stated total |
| APP-0009 | 2 | `04/03/2026` — 4 March or 3 April? |
| APP-0010 | 4 | Poor scan, low confidence throughout |

Every one of these happens in production. None of them throws an error.

---

## Three design decisions worth arguing about

**Conflicting values are never resolved automatically.** APP-0005 has two repair
totals on two pages. The pipeline surfaces both and flags. Choosing between them
is a handler decision, and a system that quietly picks one is making an
underwriting call it has no authority to make.

**Ambiguous dates return nothing.** `04/03/2026` could be either. Guessing gets a
claim dated four weeks in the wrong direction, and the error is invisible.

**Identity document data is dropped before it reaches the profile.** APP-0007
contains a driving licence. Look at `FORBIDDEN_PREFIXES` in `extract.py`. That is
the Sprint 0 Day 3 decision — you need the verification **verdict**, not the
passport — implemented as a scope gate that runs before anything else.

---

## Files

```
run_lab.py                 the command you run
.env.example               the provider switch — copy to .env
docint_lab/
  mock_docint.py           10 synthetic DI responses + ground truth
  extract.py               schema, label map, confidence policy, validators
  config.py                .env loading, the switch, URL construction
  provider.py              the seam — picks mock or azure
  azure_docint.py          the real service, over REST
  make_pdfs.py             renders the 10 documents as actual PDFs
  selftest.py              proves the adapter, offline, before you spend
docs/APP-XXXX.pdf          generated — what the azure provider posts
out/<provider>/
  extraction_report.md     the report for your ADR
  field_results.csv        every field, every document, every verdict
  profiles/APP-XXXX.json   what your Azure Function would return
  di_raw/APP-XXXX.json     azure only — the untouched service response
  confidence_delta.md      azure only — the mock's numbers against real ones
```

`out/mock/` and `out/azure/` sit side by side deliberately. Comparing them is
the exercise.

---

## Running it against Azure

The lab runs `mock` by default and that is where you should start. The real
service is a setting, not a rewrite. The whole path is standard library — there
is still nothing to `pip install`.

### Step 1 · Create the Document Intelligence resource

**Portal.** Search the Azure portal for **Document Intelligence** and click
Create. Five fields matter:

| Field | Choose | Why |
|---|---|---|
| Subscription / Resource group | Your training subscription; a fresh RG such as `rg-forge-docint` | So teardown is one delete |
| Region | An **EU/UK region** — West Europe, North Europe or UK South | Personal data in the forms. Match the client's data-residency answer from Sprint 0. |
| Name | Globally unique, e.g. `doc-int-<yourname>-1` | It becomes your endpoint hostname |
| Pricing tier | **S0 (Standard)** if you can — see the warning below | F0 silently truncates documents to two pages |
| Network | Public endpoint (all networks) for the lab | A private endpoint is right in production, and out of scope here |

**CLI.** The same thing, if you prefer:

```bash
az group create --name rg-forge-docint --location westeurope

az cognitiveservices account create \
  --name doc-int-<yourname>-1 \
  --resource-group rg-forge-docint \
  --kind FormRecognizer \
  --sku S0 \
  --location westeurope \
  --yes
```

`--kind FormRecognizer` is the Document Intelligence resource — the ARM kind
never caught up with the rebrand from Form Recognizer. `--sku F0` gives you the free
tier; read the next paragraph before you choose it.

> ### ⚠ F0 reads two pages and stops
>
> The free tier analyses the **first two pages of every document** and tells you
> nothing about it — HTTP 200, no error, no `warnings` member, a `pages` array
> that simply ends early. Three of the ten documents here are longer than two
> pages, so on F0 you silently lose `APP-0005`'s conflicting repair total,
> `APP-0007`'s entire driving-licence page, and part of `APP-0010`.
>
> The lab detects this and prints a **PAGES THE SERVICE DID NOT READ** block, so
> you will not be misled — but two teaching points do not survive the run. Use
> **S0** if the subscription allows it. S0 is pay-as-you-go: the ten documents
> in this lab cost a few pence.

### Step 2 · Copy the endpoint and key

Portal → your resource → **Keys and Endpoint**. Copy `KEY 1` and the endpoint,
which looks like `https://doc-int-<yourname>-1.cognitiveservices.azure.com/`.

Or:

```bash
az cognitiveservices account show \
  --name doc-int-<yourname>-1 --resource-group rg-forge-docint \
  --query properties.endpoint -o tsv

az cognitiveservices account keys list \
  --name doc-int-<yourname>-1 --resource-group rg-forge-docint \
  --query key1 -o tsv
```

If `az` returns `AADSTS130507` or a similar access-pass error, your account
cannot reach the **management** plane. That does not block the lab — the key
gives you the **data** plane, which is all `run_lab.py` uses. Read the endpoint
and key off the portal blade instead.

### Step 3 · Configure `.env`

```bash
cp .env.example .env
```

Then fill in the two values that have no default:

```bash
AZURE_DOCINT_ENDPOINT=https://doc-int-<yourname>-1.cognitiveservices.azure.com/
AZURE_DOCINT_KEY=<your key 1>
```

The full set of settings, all optional except those two:

| Variable | Default | What it controls |
|---|---|---|
| `DOCINT_PROVIDER` | `mock` | `mock` or `azure`. `--provider` overrides it per run. |
| `AZURE_DOCINT_ENDPOINT` | — | **Required for azure.** The `https://` resource URL. |
| `AZURE_DOCINT_KEY` | — | **Required for azure.** Key 1 or Key 2. |
| `AZURE_DOCINT_MODEL` | `prebuilt-layout` | `prebuilt-document` only if pinned to an older api-version |
| `AZURE_DOCINT_API_VERSION` | `2024-11-30` | Also decides the URL route |
| `AZURE_DOCINT_ROUTE` | `auto` | `documentintelligence` / `formrecognizer`. Force only when debugging a 404. |
| `AZURE_DOCINT_TIMEOUT` | `120` | Seconds to wait for one document |
| `DOCINT_USE_CACHE` | `true` | Reuse `out/azure/di_raw/` instead of paying again |
| `DOCINT_MAX_DOCUMENTS` | `25` | Guard against pointing this at 4,000 documents |

Note the prefixes: the provider switch and the two cache/guard settings are bare
`DOCINT_`, everything Azure-specific is `AZURE_DOCINT_`. A real environment
variable always beats the file, so CI and the VM can override without editing.

**`.env` is git-ignored and holds a live key.** Do not commit it, paste it into
chat, or screenshot it. Rotate the key in the portal if it leaks — Key 2 exists
so you can rotate without downtime.

### Step 4 · Verify before you spend

```bash
python3 run_lab.py --selftest
```

141 assertions, no network, no cost. It re-encodes the ten mock documents as
real `analyzeResult` JSON and proves the adapter produces byte-identical output
to the mock pipeline. **If this fails, fix it before you spend anything** — a
wrong adapter does not crash, it quietly returns a different decision profile
and a report that still looks perfectly reasonable.

### Step 5 · Render the PDFs and run

```bash
python3 run_lab.py --make-pdfs                    # once
python3 run_lab.py --provider azure --compare
```

`--provider azure` overrides `.env` for one run; set `DOCINT_PROVIDER=azure` in
`.env` to make it the default.

### What it costs, and why the second run is free

Ten documents, twenty-two pages, one `prebuilt-layout` analysis each — a few
pence on S0, nothing on F0. Raw responses are cached under `out/azure/di_raw/`,
so **re-running the three policies over real data costs nothing**: the
extraction is deterministic, the OCR is the part you pay for. Delete that
directory, or set `DOCINT_USE_CACHE=false`, to force a fresh read.

That distinction is worth carrying into the client conversation. Cache the
expensive, non-deterministic step; re-run the cheap, deterministic one as often
as you like.

### When it does not work

| Symptom | Almost always |
|---|---|
| `404` mentioning the **model** | The **route** is wrong, not the model. api-version ≥ `2024-11-30` lives under `/documentintelligence/`, earlier under `/formrecognizer/`. Check the route first. |
| `HTTP 200` but **zero fields** | `prebuilt-layout` without `features=keyValuePairs`. `run_lab.py` adds the flag; you only see this if you hand-rolled the URL. |
| `401` / `403` | Wrong key, or key from a different resource. Re-copy Key 1. |
| `AZURE_DOCINT_ENDPOINT must be the https resource URL` | You pasted a portal deep-link, not the endpoint from Keys and Endpoint. |
| `429` | Free-tier rate limit — F0 is throttled to a very low request rate. Wait and re-run; the cache keeps whatever already succeeded. |
| Fields report **missing** that are clearly on the form | Check for the **PAGES THE SERVICE DID NOT READ** block. On F0 anything after page two was never read. |
| `... does not exist. Render the forms first` | You skipped `--make-pdfs`. |

### Tearing it down

```bash
az group delete --name rg-forge-docint --yes --no-wait
```

Do this at the end of the day. A Document Intelligence resource left running
costs nothing at idle, but the key in your `.env` stays live until the resource
is gone.

### Why there is a `--make-pdfs` step

Azure will not accept a Python dictionary. `make_pdfs.py` renders the same ten
documents — same labels, same values, same page numbers — as real PDFs, so both
providers are driven from one source of truth in `mock_docint.py`.

### What the switch actually costs you

One function. `extract.py` is byte-for-byte the file you read this morning: it
does not import the config, does not know which provider ran, and returns the
same decision profile either way.

```python
# docint_lab/provider.py — the entire seam
analyze = mock_docint.analyze  if provider == "mock"  else azure_docint.analyze
```

That is the test of a vendor boundary. If pointing a pipeline at a real service
means editing your business logic, the boundary is in the wrong place.

### Five things that will bite you, and did

**The route moved.** api-version `2024-11-30` and later live under
`/documentintelligence/`. Earlier versions live under `/formrecognizer/`.
Mismatch them and you get a 404 whose message talks about the *model*, which
sends people hunting for a deployment problem that does not exist.

**`prebuilt-document` is gone.** It was retired in the 2024-11-30 GA API. Its
key-value pair extraction moved into `prebuilt-layout` behind an opt-in
`features=keyValuePairs` flag. Omit the flag and you get HTTP 200, no error, and
no key-value pairs at all. Nothing fails. You just get zero fields.

**Tidy the label too hard and you lose data silently.** The adapter strips the
trailing colon Document Intelligence returns on a key. An earlier version also
stripped the full stop — which turns `D.O.B.` into `D.O.B`, a label
`LABEL_MAP` has never heard of. The field does not error; it reports as
*missing*. `selftest.py` is what caught it, and there is now a regression check
pinning all four abbreviated aliases.

**The free tier reads two pages and stops.** An F0 resource analyses the first
two pages of every document and says nothing about it — HTTP 200, no error, no
`warnings` member, a `pages` array that simply ends. `APP-0005`, `APP-0007` and
`APP-0010` are 3, 3 and 4 pages, so on F0 you silently lose the conflicting
repair total, the whole driving-licence page, and part of the poor scan. Those
fields then report as *missing*, which reads as "the extractor could not manage
it" when the truth is nobody ever showed it the page. The run now compares the
pages it posted against the pages it got back and prints a **PAGES THE SERVICE
DID NOT READ** block. Upgrade to S0 to lift the limit.

**The service spaces its punctuation differently from the page.** Document
Intelligence reads a page as words, and the final full stop of an abbreviation
is its own word — so `D.O.B.:` printed on the form comes back as `D.O.B .:`.
Strip only the colon and you are left with `D.O.B .`, which `LABEL_MAP` has
never heard of, so the field reports *missing* with no error. This is the exact
mirror of the over-tidying bug above, and the mock could not have caught it: mock
labels are clean strings, and only the live service tokenises. It cost three
fields on `APP-0002` the first time this lab ran for real.

All five are the "verify the SDK, model name and API version before you build"
habit made concrete. Checking is the habit, not the inconvenience. Note which
two of the five only the real service could have shown you — that is the
argument for spending the twenty pence, and it is worth making to a client who
wants to defer the integration until the logic is "finished".

### Honest limits of the Azure run

These PDFs are **digitally generated** — the text is embedded, not scanned. So:

- Confidence measured here is a **ceiling**, not an estimate. The service reads
  clean embedded text far more confidently than it reads the forms this lab
  models. To get a real number you need the client's own scans, and asking for
  a sample of those is the first thing to do on a real engagement.
- **APP-0010 will not come back low-confidence.** Its whole purpose in the mock
  is a poor scan. It is rendered faint, small and skewed to push the score
  down; do not quote a number you have not read in `confidence_delta.md`.
- **Handwriting is not simulated.** You cannot fake it with an embedded font,
  and pretending otherwise in front of a client is how you lose the room.
- **On a free-tier resource, three of the ten documents are truncated to two
  pages.** `APP-0005`'s conflict and `APP-0007`'s identity-document page are
  both on page 3, so those two teaching points do not survive the azure run on
  F0 — the pipeline never sees the data. `APP-0005` reports one accepted repair
  total rather than a conflict, and that is a measurement artefact, not a
  validator failure. Run those two on the mock, or on S0.

What *does* survive the round trip is the part that matters: `CLM-2026-45O3` is
rendered with a letter O and comes back with a letter O, at high confidence.
Nothing errors, nothing scores low, and only the pattern check knows it is
wrong.

### Using the SDK instead

`azure_docint.py` is deliberately raw REST over `urllib` so you can read the
request — no `pip install`, no client class hiding the wire format. In
production, use the SDK; the call it replaces is:

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential

client = DocumentIntelligenceClient(endpoint, DefaultAzureCredential())
poller = client.begin_analyze_document("prebuilt-layout", body=pdf_bytes,
                                       features=["keyValuePairs"])
result = poller.result()
```

Prefer `DefaultAzureCredential` over the key in `.env`. The key is here because
it is one less moving part on day one of a lab; a managed identity is what
belongs in the Function.

For the **Azure Function**, the pipeline in `extract.py` is the handler body:
PDF in, decision profile JSON out, with `needs_human` set when anything was
flagged. Blob Storage holds the original — and the original is what an auditor
asks for, so it is written before the response is returned.

---

## Before you present

- [ ] You ran `flat_0.85` first and can name what it let through
- [ ] You can explain APP-0003 in one sentence
- [ ] You can say whether the checks or the thresholds did the work, and why
- [ ] You have a per-field threshold proposal with a reason for each tier
- [ ] You know your flag rate, and what it costs at 4,000 documents a month
- [ ] You can say where the identity-document decision is recorded
- [ ] If you ran `--provider azure`, you have read `confidence_delta.md` and can
      say which of your mock conclusions survived the real service — and which
      numbers you are **not** entitled to quote from it
