#!/usr/bin/env python3
"""
Bulk-ingest a folder of documents into the RAG backend.

Uses the same extraction, file_id, and PageIndex tree logic as the admin portal UI.
Run from the admin-portal directory (see data/README.md in backend).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

# Allow `python scripts/ingest_folder.py` from admin-portal/
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app import config  # noqa: E402
from app.extract_text import SUPPORTED, extract_text  # noqa: E402
from app.page_index import build_document_tree  # noqa: E402
from app.slug_file_id import build_semantic_file_id  # noqa: E402


def _strip_nul(value: str) -> str:
    return value.replace("\x00", "")


def _posix_rel(root: Path, file_path: Path, prefix: str) -> str:
    rel = file_path.relative_to(root).as_posix()
    return f"{prefix.rstrip('/')}/{rel}" if prefix else rel


def ingest_file(
    client: httpx.Client,
    *,
    backend_url: str,
    ingest_root: Path,
    file_path: Path,
    path_prefix: str,
    source: str,
    dry_run: bool,
) -> tuple[str, str | None]:
    """Returns (status, detail) where status is ok | skip | error."""
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED:
        return "skip", f"unsupported type {suffix}"

    raw = file_path.read_bytes()
    rel = _posix_rel(ingest_root, file_path, path_prefix)
    file_id = build_semantic_file_id(config.FILE_PREFIX, ingest_root, file_path)
    tree = build_document_tree(file_path)
    text, err = extract_text(raw, file_path.name, work_path=file_path)
    if err:
        return "error", err
    text = _strip_nul(text)
    if not text.strip():
        return "error", "no usable text after extraction"

    display = file_path.stem
    payload = {
        "file_id": file_id,
        "content": text,
        "metadata": {"source": source},
        "display_title": display,
        "relative_path": rel,
        "tree": tree,
    }

    if dry_run:
        return "ok", f"dry-run {file_id} ({len(text)} chars)"

    r = client.post(f"{backend_url}/api/ingest", json=payload)
    if not r.is_success:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        return "error", str(detail)

    data = r.json()
    chunks = data.get("chunks_stored", "?")
    return "ok", f"{file_id} ({chunks} chunks)"


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk ingest documents into the RAG backend.")
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Folder containing documents (searched recursively).",
    )
    parser.add_argument(
        "--backend-url",
        default=config.RAG_BACKEND_URL,
        help=f"Backend base URL (default: {config.RAG_BACKEND_URL}).",
    )
    parser.add_argument(
        "--path-prefix",
        default="bulk-ingest",
        help="Prefix for relative_path stored in the manifest (default: bulk-ingest).",
    )
    parser.add_argument(
        "--source",
        default="ingest-folder-script",
        help="metadata.source value on each ingest payload.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and build file_id only; do not call the backend.",
    )
    args = parser.parse_args()

    ingest_root = args.root.resolve()
    if not ingest_root.is_dir():
        print(f"Not a directory: {ingest_root}", file=sys.stderr)
        return 1

    backend_url = args.backend_url.rstrip("/")
    paths = collect_files(ingest_root)
    if not paths:
        print(f"No supported files under {ingest_root}")
        print(f"Supported: {', '.join(sorted(SUPPORTED))}")
        return 1

    print(f"Backend: {backend_url}")
    print(f"Files: {len(paths)}")
    if args.dry_run:
        print("Mode: dry-run")

    ok = err = skip = 0
    timeout = httpx.Timeout(config.REQUEST_TIMEOUT)

    with httpx.Client(timeout=timeout) as client:
        if not args.dry_run:
            try:
                health = client.get(f"{backend_url}/health")
                health.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"Backend unreachable: {exc}", file=sys.stderr)
                return 1

        for file_path in paths:
            status, detail = ingest_file(
                client,
                backend_url=backend_url,
                ingest_root=ingest_root,
                file_path=file_path,
                path_prefix=args.path_prefix,
                source=args.source,
                dry_run=args.dry_run,
            )
            label = file_path.relative_to(ingest_root)
            if status == "ok":
                ok += 1
                print(f"  OK   {label} — {detail}")
            elif status == "skip":
                skip += 1
                print(f"  SKIP {label} — {detail}")
            else:
                err += 1
                print(f"  ERR  {label} — {detail}", file=sys.stderr)

    print(f"\nDone: {ok} ok, {err} errors, {skip} skipped")
    if ok and not args.dry_run:
        print("Next: POST /api/graph/rebuild on the backend (see backend/data/README.md)")
    return 1 if err else 0


if __name__ == "__main__":
    raise SystemExit(main())
