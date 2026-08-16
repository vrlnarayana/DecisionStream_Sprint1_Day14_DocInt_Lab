"""
config.py — the provider switch, and everything it needs.

    DOCINT_PROVIDER=mock     ten synthetic responses, offline, free   (default)
    DOCINT_PROVIDER=azure    ten real PDFs through Azure AI Document Intelligence

Set it in `.env`. A real environment variable always wins over the file, so CI
and the Azure VM can override without editing anything.

WHY A SWITCH AND NOT A REWRITE
------------------------------
`extract.py` does not import this module and never learns which provider ran.
It receives the same dictionary either way. That is the adapter pattern from
Day 11: the vendor boundary is one function wide, and everything downstream —
the label map, the thresholds, the validators, the scoring — is untouched.

If pointing a pipeline at a real service means editing your business logic, the
boundary is in the wrong place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = LAB_ROOT / ".env"

PROVIDERS = ("mock", "azure")


class ConfigError(RuntimeError):
    """Raised when the azure provider is asked to run without what it needs.
    Fail loudly at startup, not per-document halfway through a billed run."""


# --------------------------------------------------------------------------
# .env loading — 12 lines, no dependency. Real environment variables win.
# --------------------------------------------------------------------------
def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        loaded[k] = v
        os.environ.setdefault(k, v)          # a real env var already set wins
    return loaded


@dataclass
class Settings:
    provider: str
    endpoint: str
    key: str
    model: str
    api_version: str
    route: str
    timeout: int
    max_documents: int
    use_cache: bool
    doc_dir: Path
    out_dir: Path

    # ----------------------------------------------------------------------
    # The analyse URL.
    #
    # Microsoft has moved this path once already. API versions from 2024-11-30
    # live under /documentintelligence/; everything before it lives under
    # /formrecognizer/. Getting this wrong returns 404 with a body that talks
    # about the MODEL, which sends people hunting for a deployment problem that
    # does not exist. Check the route before you check the model name.
    # ----------------------------------------------------------------------
    def resolved_route(self) -> str:
        if self.route != "auto":
            return self.route
        return "documentintelligence" if self.api_version >= "2024-11-30" else "formrecognizer"

    def analyze_url(self) -> str:
        base = self.endpoint.rstrip("/")
        url = (f"{base}/{self.resolved_route()}/documentModels/{self.model}:analyze"
               f"?api-version={self.api_version}")
        # prebuilt-document was retired in the 2024-11-30 GA API. Key-value
        # pairs moved to prebuilt-layout behind an opt-in feature flag, and
        # WITHOUT the flag layout returns no keyValuePairs at all — a
        # successful, empty, entirely misleading response.
        if self.model == "prebuilt-layout":
            url += "&features=keyValuePairs"
        return url

    def require_azure(self) -> None:
        missing = [n for n, v in (("AZURE_DOCINT_ENDPOINT", self.endpoint),
                                  ("AZURE_DOCINT_KEY", self.key)) if not v]
        if missing:
            raise ConfigError(
                f"DOCINT_PROVIDER=azure needs {' and '.join(missing)}.\n"
                f"  cp .env.example .env   and fill them in\n"
                f"  (looked in {ENV_FILE})"
            )
        # https, or a local stub. Anything else is a key about to be sent in
        # clear text to a host somebody typed wrong, so it stops here.
        local = self.endpoint.startswith(("http://127.0.0.1", "http://localhost"))
        if not (self.endpoint.startswith("https://") or local):
            raise ConfigError(
                f"AZURE_DOCINT_ENDPOINT must be the https resource URL "
                f"(http is allowed only for a local stub on 127.0.0.1), got: "
                f"{self.endpoint!r}"
            )


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def get_settings(provider_override: str | None = None) -> Settings:
    load_env()
    provider = (provider_override or os.environ.get("DOCINT_PROVIDER", "mock")).strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"DOCINT_PROVIDER must be one of {PROVIDERS}, got {provider!r}")

    return Settings(
        provider=provider,
        endpoint=os.environ.get("AZURE_DOCINT_ENDPOINT", "").strip(),
        key=os.environ.get("AZURE_DOCINT_KEY", "").strip(),
        model=os.environ.get("AZURE_DOCINT_MODEL", "prebuilt-layout").strip(),
        api_version=os.environ.get("AZURE_DOCINT_API_VERSION", "2024-11-30").strip(),
        route=os.environ.get("AZURE_DOCINT_ROUTE", "auto").strip(),
        timeout=_int("AZURE_DOCINT_TIMEOUT", 120),
        max_documents=_int("DOCINT_MAX_DOCUMENTS", 25),
        use_cache=os.environ.get("DOCINT_USE_CACHE", "true").strip().lower()
        not in ("0", "false", "no"),
        doc_dir=LAB_ROOT / "docs",
        out_dir=LAB_ROOT / "out",
    )
