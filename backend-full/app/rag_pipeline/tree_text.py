from __future__ import annotations

from typing import Any


def flatten_tree_titles(tree: dict[str, Any], *, max_nodes: int = 48) -> str:
    """Compact section titles for query augmentation (PageIndex-style hints to the vector retriever)."""
    nodes = tree.get("nodes")
    if not isinstance(nodes, list):
        return ""
    root_id = tree.get("root_id", "n0")
    titles: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("id") == root_id:
            continue
        title = str(n.get("title", "")).strip()
        if title:
            titles.append(title)    
        if len(titles) >= max_nodes:
            break
    return " | ".join(titles)


def build_tree_augmented_query(
    user_query: str,
    file_ids: list[str],
    manifest: dict[str, dict[str, Any]],
    max_chars: int = 2000,
) -> str:
    """Prepend compact outline text so embedding similarity aligns with document structure."""
    chunks: list[str] = []
    remaining = max_chars
    for fid in file_ids:
        row = manifest.get(fid)
        if not row:
            continue
        tree = row.get("tree")
        if not isinstance(tree, dict):
            continue
        flat = flatten_tree_titles(tree)
        if not flat:
            continue
        title = str(row.get("display_title", ""))[:120]
        piece = f"[{title}] {flat}"
        if len(piece) > remaining:
            piece = piece[:remaining]
        chunks.append(piece)
        remaining -= len(piece) + 2
        if remaining <= 0:
            break
    if not chunks:
        return user_query
    hint = " ".join(chunks)
    return f"{user_query}\n\n[Document outline hints for retrieval: {hint}]"
