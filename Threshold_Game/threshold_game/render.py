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
