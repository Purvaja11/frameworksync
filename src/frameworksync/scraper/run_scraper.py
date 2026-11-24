"""Entry point: download every configured doc source to disk.

Run with:  uv run python -m frameworksync.scraper.run_scraper
"""

import hashlib
import json
from datetime import datetime, timezone

from frameworksync.config import DOC_SOURCES, RAW_DOCS_DIR
from frameworksync.scraper.fetch import FetchError, fetch_text

MANIFEST_PATH = RAW_DOCS_DIR / "manifest.json"


def _hash(text: str) -> str:
    """Short fingerprint of a page's content, to detect real changes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run() -> None:
    RAW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()

    for source in DOC_SOURCES:
        library_dir = RAW_DOCS_DIR / source.library
        library_dir.mkdir(parents=True, exist_ok=True)
        out_path = library_dir / f"{source.name}.md"

        print(f"Fetching {source.name} ({source.library}) ...", end=" ")
        try:
            text = fetch_text(source.url)
        except FetchError as err:
            print(f"FAILED: {err}")
            continue

        content_hash = _hash(text)
        previous = manifest.get(source.name)

        if previous is not None and previous["hash"] == content_hash:
            print("unchanged")
        else:
            status = "updated" if previous else "new"
            print(status)
            out_path.write_text(text, encoding="utf-8")

        manifest[source.name] = {
            "library": source.library,
            "url": source.url,
            "hash": content_hash,
            "path": str(out_path.relative_to(RAW_DOCS_DIR)),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    _save_manifest(manifest)
    print(f"\nManifest saved to {MANIFEST_PATH}")


if __name__ == "__main__":
    run()