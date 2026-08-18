"""
app.py — the Threshold Game, as a Streamlit board.

    streamlit run app.py

Two panels. Settings on the left, consequences on the right. Move a slider,
watch the score. That is the whole interface, and it is deliberately the
whole interface — the moment you can see the number move, you start
experimenting instead of planning.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "threshold_game"))

from game import (DOCUMENTS, FIELDS, FIELD_KIND, CHECKS, POINTS,   # noqa: E402
                  DEFAULT_THRESHOLDS, DEFAULT_CHECKS, play, strategy)
import docint                                              # noqa: E402
import providers                                           # noqa: E402
import render                                              # noqa: E402


@st.cache_data(show_spinner=False)
def rendered_pages(doc_id: str) -> list[bytes]:
    """The exact page images that get sent to Document Intelligence.

    Cached because rendering is ~0.2s a page and Streamlit re-runs this whole
    script every time a slider moves.
    """
    import io
    doc = next(d for d in DOCUMENTS if d["id"] == doc_id)
    out = []
    for page in render.pages(doc):
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


@st.cache_data(show_spinner=False)
def rendered_pdf(doc_id: str) -> bytes:
    doc = next(d for d in DOCUMENTS if d["id"] == doc_id)
    return render.build_pdf(doc)

IND = "#2E0C69"
ORG = "#FD600E"
LAV = "#DCD4FF"

st.set_page_config(page_title="The Threshold Game", page_icon="🎯",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown(f"""
<style>
  .block-container {{padding-top: 2rem; padding-bottom: 2rem;}}
  .bigscore {{font-size: 3.4rem; font-weight: 700; line-height: 1;}}
  .sub {{color: #857DA0; font-size: 0.85rem;}}
  .pill {{display:inline-block;padding:2px 10px;border-radius:12px;
          font-size:0.75rem;font-weight:600;margin-right:6px;}}
  .ok {{background:#E2F3EC;color:#10503A;}}
  .bad {{background:#FBE4E8;color:#78122A;}}
  .flag {{background:#FFF4E5;color:#7A2E00;}}
  .stSlider label {{font-size: 0.8rem !important;}}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ state
def init():
    if "th" not in st.session_state:
        st.session_state.th = dict(DEFAULT_THRESHOLDS)
    if "ck" not in st.session_state:
        st.session_state.ck = dict(DEFAULT_CHECKS)
    if "best" not in st.session_state:
        st.session_state.best = None
    for k in ("azure_docs", "azure_report", "azure_error", "azure_bounds",
              "mock_bounds"):
        if k not in st.session_state:
            st.session_state[k] = None


init()


def apply_strategy(name: str):
    t, c = strategy(name)
    st.session_state.th = t
    st.session_state.ck = c


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### Your settings")

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
                # The SAME search, same trials, same seed, run against the
                # mock — because a 2,000-trial random search is a lower
                # bound whose value is dominated by how lucky the search
                # got, and it varies about twofold with the seed. Printed
                # beside the hardcoded 252 it looks like a like-for-like
                # comparison and is not; printed beside the mock's own
                # searched number it is one. Costs no service calls, and
                # runs here rather than on every rerun.
                st.session_state.mock_bounds = providers.search_bounds()
                st.session_state.azure_error = None
            except docint.DocIntError as e:
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.session_state.mock_bounds = None
                st.session_state.azure_error = str(e)
            except Exception as e:                      # noqa: BLE001
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.session_state.mock_bounds = None
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
                st.session_state.mock_bounds = None
                st.rerun()
            if c2.button("Clear cache", use_container_width=True,
                         help="Forces the next run to re-analyse, and to bill."):
                n = providers.clear_cache()
                st.session_state.azure_docs = None
                st.session_state.azure_report = None
                st.session_state.azure_bounds = None
                st.session_state.mock_bounds = None
                st.toast(f"Removed {n} cached responses.")
                st.rerun()

    st.markdown("**Presets**")
    c1, c2 = st.columns(2)
    if c1.button("The brief", use_container_width=True,
                 help="Exactly what the curriculum says: 0.85 everywhere, no checks"):
        apply_strategy("brief")
        st.rerun()
    if c2.button("Reset", use_container_width=True):
        apply_strategy("brief")
        st.rerun()

    st.divider()
    st.markdown("**Confidence thresholds**")
    st.caption("Above the bar, accept automatically. Below it, send to a human.")

    for f in FIELDS:
        kind = FIELD_KIND[f]
        st.session_state.th[f] = st.slider(
            f"{f}  ·  {kind}", 0.0, 1.0, float(st.session_state.th[f]),
            0.01, key=f"th_{f}")

    st.divider()
    st.markdown("**Checks**")
    st.caption("A check can override a high confidence when the value does not "
               "look like what it claims to be.")

    for k, desc in CHECKS.items():
        st.session_state.ck[k] = st.checkbox(k, value=bool(st.session_state.ck[k]),
                                             help=desc, key=f"ck_{k}")

    st.divider()
    cfg = {"thresholds": st.session_state.th, "checks": st.session_state.ck}
    st.download_button("Download policy.json", json.dumps(cfg, indent=2),
                       "policy.json", "application/json", use_container_width=True)


# ------------------------------------------------------------------ play
res = play(st.session_state.th, st.session_state.ck)
tot = res["total"]

AZ = st.session_state.azure_docs
az_res = (play(st.session_state.th, st.session_state.ck, documents=AZ)
          if AZ else None)
az_tot = az_res["total"] if az_res else None

if st.session_state.best is None or tot["score"] > st.session_state.best:
    st.session_state.best = tot["score"]

# ------------------------------------------------------------------ header
st.markdown("## 🎯 The Threshold Game")
st.caption("Ten case application forms. Decide what to accept automatically "
           "and what to send to a human. A flag costs half a minute. A wrongly "
           "accepted value costs a case.")

k = st.columns([1.6, 1, 1, 1, 1, 1.2])
colour = "#1B7F5A" if tot["score"] > 0 else "#C0223B"
k[0].markdown(f"<div class='bigscore' style='color:{colour}'>{tot['score']}</div>"
              f"<div class='sub'>score · best this session {st.session_state.best}</div>",
              unsafe_allow_html=True)
k[1].metric("Correct accepts", tot["correct_accepts"], help=f"{POINTS['correct_accept']:+} each")
k[2].metric("WRONG accepts", tot["wrong_accepts"], help=f"{POINTS['wrong_accept']:+} each",
            delta=None if tot["wrong_accepts"] == 0 else f"{tot['wrong_accepts']} costly",
            delta_color="inverse")
k[3].metric("Flagged", tot["flags"], help=f"{POINTS['flag']:+} each")
k[4].metric("Missing", tot["missing"], help=f"{POINTS['missing_unflagged']:+} each")

TUNING_CEILING = 252
GLOBAL_BEST = 720
AZ_CEILING, AZ_BEST = st.session_state.azure_bounds or (None, None)
MK_CEILING, MK_BEST = st.session_state.mock_bounds or (None, None)
pct = max(0.0, min(1.0, (tot["score"] + 700) / (GLOBAL_BEST + 700)))
k[5].markdown("<div class='sub'>Progress to the best known score</div>",
              unsafe_allow_html=True)
k[5].progress(pct)

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
    a[5].markdown(
        f"<div class='sub'>tuning ceiling &nbsp;live {AZ_CEILING} · "
        f"mock {MK_CEILING}<br>"
        f"best found &nbsp;live {AZ_BEST} · mock {MK_BEST}<br>"
        f"<i>Both columns are lower bounds from the same 2,000-trial random "
        f"search, so they are comparable with each other — a random search "
        f"finds the best it happened to try, never provably the best there "
        f"is. The {TUNING_CEILING}/{GLOBAL_BEST} quoted elsewhere came from "
        f"a different, larger search and is not comparable to either.</i>"
        f"</div>", unsafe_allow_html=True)

if tot["wrong_accepts"] == 0 and tot["score"] > TUNING_CEILING:
    st.success(f"Zero wrong accepts, and you are past {TUNING_CEILING} — which is "
               f"the ceiling for threshold tuning with every check switched off. "
               f"Best known score is {GLOBAL_BEST}.")
elif tot["wrong_accepts"] == 0:
    st.info("Zero wrong accepts. Now see how many flags you can give back.")
elif tot["score"] < 0:
    st.warning(f"{tot['wrong_accepts']} wrong values accepted. Each one costs "
               f"{abs(POINTS['wrong_accept'])} — more than fifty flags.")

st.divider()

# ------------------------------------------------------------------ tabs
tab_names = ["Per document", "Field detail", "How scoring works",
             "Strategy comparison", "The documents"]
if az_res:
    tab_names.append("Mock vs live")
tabs = st.tabs(tab_names)
t1, t2, t3, t4 = tabs[0], tabs[1], tabs[2], tabs[3]
t_docs = tabs[4]
t5 = tabs[5] if az_res else None

with t1:
    cols = st.columns(5)
    for i, row in enumerate(res["rows"]):
        doc = next(d for d in DOCUMENTS if d["id"] == row["doc_id"])
        c = cols[i % 5]
        tone = "#1B7F5A" if row["wrong_accepts"] == 0 else "#C0223B"
        with c:
            st.markdown(f"**{row['doc_id']}**")
            st.markdown(f"<span style='color:{tone};font-size:1.5rem;"
                        f"font-weight:700'>{row['score']}</span>",
                        unsafe_allow_html=True)
            bits = []
            if row["correct_accepts"]:
                bits.append(f"<span class='pill ok'>{row['correct_accepts']} ok</span>")
            if row["wrong_accepts"]:
                bits.append(f"<span class='pill bad'>{row['wrong_accepts']} wrong</span>")
            if row["flags"]:
                bits.append(f"<span class='pill flag'>{row['flags']} flag</span>")
            st.markdown("".join(bits), unsafe_allow_html=True)
            st.caption(doc["note"])
            with st.expander("hint"):
                st.caption(doc["hint"])

with t2:
    pick = st.selectbox("Document", [d["id"] for d in DOCUMENTS],
                        format_func=lambda x: f"{x} — "
                        + next(d['note'] for d in DOCUMENTS if d['id'] == x))
    row = next(r for r in res["rows"] if r["doc_id"] == pick)
    fields = res["per_doc"][pick]["result"]["fields"]

    st.markdown(f"**Score for this document: {row['score']}**")
    table = []
    for d in row["detail"]:
        f = fields.get(d["field"])
        table.append({
            "field": d["field"],
            "raw": f.raw if f else "",
            "parsed": str(f.value) if f else "",
            "conf": f"{f.confidence:.2f}" if f else "",
            "bar": f"{st.session_state.th.get(d['field'], 0.85):.2f}",
            "verdict": d["verdict"],
            "points": d["points"],
            "why": d["why"],
        })
    st.dataframe(table, use_container_width=True, hide_index=True)

    miss = res["per_doc"][pick]["result"]["missing"]
    if miss:
        st.warning(f"Required fields not present at all: {', '.join(miss)} "
                   f"({POINTS['missing_unflagged']} each)")
    drop = res["per_doc"][pick]["result"]["dropped"]
    if drop:
        st.success("Dropped before reaching the profile: "
                   + ", ".join(d["field"] for d in drop))

with t3:
    st.markdown(f"""
| Outcome | Points | Why |
|---|---|---|
| A correct value accepted automatically | **{POINTS['correct_accept']:+}** | The work you saved |
| A **wrong** value accepted automatically | **{POINTS['wrong_accept']:+}** | The case you broke |
| A value sent to a human | **{POINTS['flag']:+}** | Thirty seconds of handler time |
| A required field missing and not flagged | **{POINTS['missing_unflagged']:+}** | A silent gap |

The asymmetry is the point. A wrongly accepted policy number attaches a claim
to a policy that does not exist, and nobody finds out until somebody
downstream notices. A flag costs half a minute and somebody knows it happened.

**Confidence is the model's certainty about what it saw. It is not a statement
about whether the value is correct.**
""")

with t4:
    st.caption("Every one of these was scored by the same engine you are "
               "playing against.")
    names = ["brief", "accept_everything", "flag_everything", "tuned_hard",
             "tuned_extreme", "checks_only", "checks_and_tuning"]
    labels = {
        "brief": "The brief: 0.85 everywhere, no checks",
        "accept_everything": "Accept everything",
        "flag_everything": "Flag everything",
        "tuned_hard": "Tuned hard, no checks",
        "tuned_extreme": "Everything at 0.99, no checks",
        "checks_only": "All checks on, thresholds left at 0.85",
        "checks_and_tuning": "All checks on, plus tight thresholds",
    }
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
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown(f"""
**The two findings hiding in that table.**

A four-thousand-trial search over threshold combinations, with every check
switched off, tops out at **{TUNING_CEILING}** — and still lets two wrong
values through. Simply switching the checks on, leaving every threshold at the
brief's 0.85, scores **576**.

And note that *all checks on, plus tight thresholds* scores **less** than
*all checks on* alone. Tightening on top of working validation buys you flags
and prevents nothing.

> Tuning moves who looks at a value.
> Validation changes whether the value is right.
""")

with t_docs:
    st.caption("These are the actual pages sent to Document Intelligence — "
               "rendered from the same ten documents the mock scores, then "
               "deliberately degraded. What you see here is exactly what the "
               "service sees. Nothing else is sent.")

    pick_d = st.selectbox(
        "Document", [d["id"] for d in DOCUMENTS], key="docs_pick",
        format_func=lambda x: f"{x} — {render.PROFILES[x]} — "
        + next(d["note"] for d in DOCUMENTS if d["id"] == x))

    prof = render.PROFILES[pick_d]
    p = render.PARAMS[prof]
    left, right = st.columns([3, 2])

    with right:
        st.markdown(f"**Degradation profile: `{prof}`**")
        st.dataframe(
            [{"setting": k, "value": v} for k, v in p.items()],
            use_container_width=True, hide_index=True)
        if all(v in (0, 0.0, 1.0) for v in p.values()):
            st.info("This profile applies **no degradation at all** — the page "
                    "goes to the service exactly as drawn.")
        st.download_button("Download this PDF", rendered_pdf(pick_d),
                           f"{pick_d}.pdf", "application/pdf",
                           use_container_width=True)

        if AZ:
            live = next(d for d in AZ if d["id"] == pick_d)
            mockdoc = next(d for d in DOCUMENTS if d["id"] == pick_d)
            got, expected = len(live["kv"]), len(mockdoc["kv"])
            st.markdown("**What the live service made of it**")
            if got < expected:
                st.error(f"{got} of {expected} fields came back usable. "
                         f"The rest were returned with labels the adapter "
                         f"could not recognise — see the sidebar's unmapped "
                         f"list. When the service misreads the *label*, the "
                         f"value is lost even though it was printed clearly.")
            else:
                st.success(f"All {got} fields came back and mapped cleanly.")
            if live["kv"]:
                confs = [k["confidence"] for k in live["kv"]]
                st.caption(f"confidence on what it did read — "
                           f"min {min(confs):.3f}, mean "
                           f"{sum(confs)/len(confs):.3f}, max {max(confs):.3f}")

    with left:
        for i, png in enumerate(rendered_pages(pick_d), 1):
            st.image(png, caption=f"{pick_d} — page {i}",
                     use_container_width=True)


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
