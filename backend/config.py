import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

EMBEDDINGS_MODEL = os.getenv("EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "rag_mcp_docs")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
FILE_PREFIX = os.getenv("FILE_PREFIX", "kb-")
VECTOR_DB_DIR = Path(os.getenv("VECTOR_DB_DIR", str(BASE_DIR / "vector_db")))

BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))

JWT_SECRET = os.getenv("JWT_SECRET", "").strip()

MANIFEST_PATH = Path(os.getenv("MANIFEST_PATH", str(BASE_DIR / "data" / "kb_manifest.json")))
GRAPH_PATH = Path(os.getenv("GRAPH_PATH", str(BASE_DIR / "data" / "form_graph.json")))

RAG_MAX_FILTER_DOCS = max(1, int(os.getenv("RAG_MAX_FILTER_DOCS", "12")))
GRAPH_EXPANSION_HOPS = max(0, int(os.getenv("GRAPH_EXPANSION_HOPS", "1")))
USE_TREE_QUERY_HINTS = os.getenv("USE_TREE_QUERY_HINTS", "true").strip().lower() in ("1", "true", "yes")

SEMANTIC_EDGE_THRESHOLD = max(0.0, min(1.0, float(os.getenv("SEMANTIC_EDGE_THRESHOLD", "0.55"))))
TREE_OVERLAP_THRESHOLD = max(0.0, min(1.0, float(os.getenv("TREE_OVERLAP_THRESHOLD", "0.25"))))
MAX_SEMANTIC_EDGES_PER_DOC = max(1, int(os.getenv("MAX_SEMANTIC_EDGES_PER_DOC", "5")))
