# 🎯 The Threshold Game

**Birlasoft FORGE FDE Academy · Sprint 1 Day 14 · Confidential — Trainer & Learner Use**

Ten case application forms. Decide what to accept automatically and what to
send to a human. A flag costs half a minute. A wrongly accepted value costs a
case.

**▶ Play it now — nothing to install:
[docint-threshold-demo.streamlit.app](https://docint-threshold-demo.streamlit.app/)**

That hosted board is the fastest way into the game and the one to send a room
to. It runs the offline mock, which is the whole exercise. The live Document
Intelligence panel is there too, and it asks for an endpoint and a key —
credentials are held for your browser session only and are never written to
disk, so nothing you paste there is stored or shared. If you would rather not
put a key into a hosted app at all, run it locally instead (option 2 below);
the game is identical either way.

---

## Three ways to play. Pick the one that runs on your laptop.

### 1 · The HTML board — zero install

Double-click **`threshold_game.html`**. That is the whole setup.

No Python, no pip, no server, no internet. Works from a USB stick and works on
a locked-down machine. **This is the one to default to in a room** — it is the
version that will not cost you fifteen minutes of the session.

### 2 · The Streamlit board — nicer, needs one install

```bash
pip install streamlit
streamlit run app.py
```

Same game, better sliders, live tables.

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

**On the hosted board specifically.** Two things differ from running locally,
and both only affect the live panel — the mock game is unaffected.

`packages.txt` installs `fonts-dejavu-core`, because `render.py` draws the
forms with a real TrueType font and deliberately refuses to fall back to
Pillow's bitmap font — that fallback would wreck the confidence bands
invisibly. Without that apt package the live panel stops with a
`FontNotFound` naming the paths it tried. Keep the file if you redeploy.

The response cache under `out/azure/` belongs to the running process, not to
your browser session, so on a shared hosted app it is shared by everyone using
it and is wiped whenever the app restarts. Nothing sensitive lives in it — the
ten documents are synthetic and no credential is ever cached — but it does mean
someone else's earlier run can serve your "Connect & analyse", and that a
restart re-bills the next run. For a session where you want the measurement to
be yours, run locally.

### 3 · The terminal + `policy.json` — the one that feels like engineering

```bash
python3 play.py --watch
```

Standard library only. Then **split your VS Code window**: `policy.json` on the
left, the watching terminal on the right. Change a number, hit save, and the
score moves before you have looked back up.

That loop is the point. Under a second and people experiment. Over five and
they start planning instead, which is a slower way to learn the same thing.

> `Ctrl+Shift+B` runs the watcher — the VS Code tasks are already in
> `.vscode/tasks.json`.

Other commands:

```bash
python3 play.py --doc APP-0003     # one document, field by field
python3 play.py --compare          # the reference strategies
python3 play.py --hint             # what each document is testing
```

---

## How scoring works

| Outcome | Points | Why |
|---|---|---|
| A correct value accepted automatically | **+10** | The work you saved |
| A **wrong** value accepted automatically | **−150** | The case you broke |
| A value sent to a human | **−2** | Thirty seconds of handler time |
| A required field missing and not flagged | **−20** | A silent gap |

The asymmetry is the whole design. **One wrong acceptance costs more than
seventy flags** — because a wrongly accepted policy number attaches a claim to
a policy that does not exist, and nobody finds out until somebody downstream
notices. A flag costs half a minute and somebody knows it happened.

---

## What players discover

Start with **The brief** preset — 0.85 everywhere, no checks, exactly what the
curriculum specifies. It scores **−600**.

Then people tune. And tuning does not save them:

| Strategy | Score | Wrong accepts |
|---|---|---|
| The brief (0.85 everywhere, no checks) | −600 | 8 |
| Accept everything | −284 | 7 |
| Flag everything | −516 | 2 |
| Tuned hard, no checks | −488 | 4 |
| Everything at 0.99, no checks | −516 | 2 |
| **Best tuning found by 4,000-trial search, no checks** | **252** | **2** |
| **All checks on, thresholds left at 0.85** | **576** | **0** |
| All checks on + tight thresholds | 480 | 0 |

**Two findings, and neither is asserted — both are produced by the same engine
the room is playing against.**

**One.** An exhaustive-ish search over threshold combinations with every check
switched off tops out at **252**, and still lets two wrong values through.
Simply turning the checks on, leaving every threshold at the brief's 0.85,
scores **576**. Nobody finds 252 by hand in twenty minutes — so in practice
whoever is winning is winning because they enabled validation.

**Two.** *All checks on plus tight thresholds* scores **less** than *all checks
on* alone. Tightening on top of working validation buys flags and prevents
nothing. That is the same result the Day 14 batch lab produces, arrived at by
the room rather than delivered to it.

> Tuning moves **who looks at** a value.
> Validation changes **whether the value is right**.

---

## The ten documents

| ID | What it is | What it teaches |
|---|---|---|
| APP-0001 | Clean baseline | What good looks like |
| APP-0002 | Lower confidence, all correct | Set the bar too high and you pay for nothing |
| **APP-0003** | **Handwritten — letter O for zero at 0.86** | **Confidence is not accuracy** |
| APP-0004 | Two required fields absent | Missing is not the same as wrong |
| APP-0005 | Same field twice, different values | Never resolve a conflict silently |
| APP-0006 | Line items do not sum to the total | Arithmetic no model will do for you |
| APP-0007 | `04/03/2026` — March or April? | Return nothing rather than guess |
| APP-0008 | Driving licence attached | Scope, not accuracy |
| APP-0009 | Estimate 22,000 vs sum insured 6,100 | Not impossible, never silent |
| APP-0010 | Poor scan, low confidence throughout | The **safest** document here |

**APP-0003 is the one the game is built around.** Its policy number came back
as `MOT-45O3901` — a letter **O** where a zero should be — at confidence
**0.86**. No threshold catches it without also flagging most of the clean
documents. The pattern check catches it for free.

**APP-0010 is the one that surprises people.** It is uniformly low confidence,
so everything gets flagged and a human looks. It is the *least* dangerous
document in the set, because it says it is unsure.

---

## The seven checks

| Check | What it does | Worth alone |
|---|---|---|
| `pattern` | Identifiers must match their expected shape | +148 |
| `ocr_repair` | Flag a value that only matches after repairing OCR | **−12** |
| `arithmetic` | Line items must sum to the stated total | +148 |
| `date_sanity` | Reject impossible and ambiguous dates | +296 |
| `cross_field` | Estimate vs sum insured, excess vs estimate, age | +148 |
| `conflict` | Never silently pick between two values | +148 |
| `scope_gate` | Identity data never reaches the profile | **+300** |

Two of those are worth pointing at.

**`scope_gate` is worth +300 on its own** — more than any other single check —
because it removes two wrongly accepted identity fields. The most valuable
validator in the set is not an accuracy control at all. It is a scope control.

**`ocr_repair` alone scores −12.** It adds flags without removing a wrong
accept, because `pattern` is what catches APP-0003. It only earns its place
once `pattern` is on. That is a real phenomenon — a check that costs you until
it is paired — and it is worth letting somebody discover it and complain.

---

## Running the session (45 minutes)

| Time | What happens |
|---|---|
| 0–5 | Everyone opens the HTML board on **The brief**. Score: −600. |
| 5–20 | **Tune only.** Checks stay off. Pods compete on score. |
| 20–25 | Collect scores. Nobody is above ~150. Ask why. |
| 25–35 | **Checks unlocked.** Watch the leaderboard invert within two minutes. |
| 35–45 | Best pod presents their settings. Then show the strategy table. |

The 20–25 minute pause matters. Let the room be stuck on tuning before you
unlock the checks — the frustration is what makes the second half land.

**A question worth asking at minute 22:** *"has anyone got zero wrong accepts
yet, and what did it cost you?"* The honest answer is that flagging everything
gets you to two wrong accepts and −516, which nobody wants to admit is their
best idea.

---

## Files

```
threshold_game.html      the zero-install board — start here
app.py                   the Streamlit board
play.py                  the terminal version, with --watch
policy.json              the file players edit (created on first run)
build_html.py            regenerates the HTML from the Python source
verify.py                proves the JS and Python engines agree
threshold_game/game.py   documents, scoring, and the reference strategies
.vscode/tasks.json       Ctrl+Shift+B runs the watcher
```

---

## For whoever maintains this

The scoring exists twice — Python in `game.py`, JavaScript inside the HTML.
Two implementations of the same rules will drift, so:

```bash
python3 build_html.py    # regenerate after any change to game.py
python3 verify.py        # 74 configurations, both engines, must agree
```

`verify.py` currently reports **PASS across 74 configurations and 10
documents**. If you change the rules and skip it, the two versions will start
teaching different lessons and nobody will notice for a while.

The document corpus is single-sourced from `game.py` and emitted into the HTML,
so at least the data cannot drift — only the rules can.

**The scoring constants are tuned deliberately.** If you raise
`correct_accept` or soften `wrong_accept`, check the strategy table again —
the game only teaches the right thing while validation strictly dominates
tuning, and that is a property of those four numbers.
