"""
verify.py — prove the JavaScript scoring agrees with the Python scoring.

Two implementations of the same rules WILL drift. This is what stops them.

It extracts the scoring JavaScript out of threshold_game.html, runs it under
Node against a set of threshold/check combinations, and compares every score
with the Python engine. Any disagreement fails loudly.

    python3 verify.py

Run it after any change to game.py or build_html.py. If you do not have Node,
it says so and skips rather than pretending to pass.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "threshold_game"))

from game import FIELDS, CHECKS, DEFAULT_THRESHOLDS, play, strategy  # noqa: E402


def cases(n_random: int = 60) -> list[dict]:
    """Named strategies plus random combinations, including nasty edges."""
    out = []
    for name in ("brief", "accept_everything", "flag_everything", "tuned_hard",
                 "tuned_extreme", "checks_only", "checks_and_tuning"):
        t, c = strategy(name)
        out.append({"name": name, "th": t, "ck": c})

    # each check alone
    for k in CHECKS:
        out.append({"name": f"only_{k}", "th": dict(DEFAULT_THRESHOLDS),
                    "ck": {x: (x == k) for x in CHECKS}})

    rng = random.Random(42)
    grid = [0.0, 0.5, 0.69, 0.75, 0.83, 0.85, 0.86, 0.88, 0.9, 0.94, 0.95, 0.97, 1.0]
    for i in range(n_random):
        out.append({
            "name": f"random_{i}",
            "th": {f: rng.choice(grid) for f in FIELDS},
            "ck": {k: rng.random() < 0.5 for k in CHECKS},
        })
    return out


JS_HARNESS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("no script block found"); process.exit(2); }

let src = m[1];
// strip the DOM-dependent tail: everything from the UI section onward
const cut = src.indexOf("/* ===================== UI =====================");
if (cut === -1) { console.error("UI marker not found"); process.exit(2); }
src = src.slice(0, cut);

const CASES = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const results = [];
const fn = new Function("DATA_IN", "TH_IN", "CK_IN", `
  const DATA = DATA_IN; let TH = TH_IN, CK = CK_IN;
  ${src.replace(/^const DATA = [\s\S]*?;\s*$/m, "")
       .replace(/^let TH = [\s\S]*?;\s*$/m, "")
       .replace(/^let CK = \{\};[\s\S]*?\n/m, "")
       .replace(/^let TAB = [\s\S]*?;\s*$/m, "")}
  return playAll();
`);

for (const c of CASES) {
  const r = fn(CASES.__data, c.th, c.ck);
  results.push({name: c.name, total: r.total,
                per: r.rows.map(x => ({id: x.doc_id, s: x.score}))});
}
console.log(JSON.stringify(results));
"""


def main() -> int:
    node = shutil.which("node")
    html = HERE / "threshold_game.html"
    if not html.exists():
        print("threshold_game.html not found — run build_html.py first.")
        return 1
    if not node:
        print("Node.js not found. Skipping the JavaScript parity check.")
        print("The Streamlit version is unaffected; the HTML version is unverified.")
        return 0

    cs = cases()
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", html.read_text(),
                                re.S).group(1))

    with tempfile.TemporaryDirectory() as td:
        payload = [{"name": c["name"], "th": c["th"], "ck": c["ck"]} for c in cs]
        blob = {"__data": data}
        pfile = Path(td) / "cases.json"
        # attach data as a property on the array for the harness
        arr = payload
        pfile.write_text(json.dumps(arr))
        # rewrite harness to load data separately
        dfile = Path(td) / "data.json"
        dfile.write_text(json.dumps(data))
        harness = JS_HARNESS.replace(
            'const r = fn(CASES.__data, c.th, c.ck);',
            'const r = fn(DATA_JSON, c.th, c.ck);').replace(
            'const CASES = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));',
            'const CASES = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));\n'
            'const DATA_JSON = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));')
        hfile = Path(td) / "h.js"
        hfile.write_text(harness)

        proc = subprocess.run([node, str(hfile), str(html), str(pfile), str(dfile)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print("Node harness failed:")
            print(proc.stderr[-2000:])
            return 1
        js = json.loads(proc.stdout)

    fails = []
    for c, j in zip(cs, js):
        py = play(c["th"], c["ck"])
        if py["total"]["score"] != j["total"]["score"]:
            fails.append((c["name"], py["total"], j["total"]))
            continue
        for prow, jrow in zip(py["rows"], j["per"]):
            if prow["score"] != jrow["s"]:
                fails.append((f"{c['name']}/{prow['doc_id']}",
                              prow["score"], jrow["s"]))

    print(f"Compared {len(cs)} configurations across {len(play(dict(DEFAULT_THRESHOLDS), {k: False for k in CHECKS})['rows'])} documents.")
    if fails:
        print(f"\n{len(fails)} MISMATCH(ES) — the two engines disagree:\n")
        for name, a, b in fails[:12]:
            print(f"  {name:<28} python={a}  js={b}")
        return 1
    print("PASS — the JavaScript and Python engines agree on every configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
