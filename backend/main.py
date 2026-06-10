from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import BACKEND_PORT, COLLECTION_NAME, EMBEDDINGS_MODEL, GRAPH_PATH, MANIFEST_PATH, VECTOR_DB_DIR
from graph_store import ensure_graph_store
from routes.graph_rebuild import router as graph_rebuild_router
from routes.ingest import router as ingest_router
from routes.query import router as query_router
from vector_store import ensure_store, ping

app = FastAPI(title="RAG MCP Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)
    ensure_graph_store()
    ensure_store()
    print(f"Backend ready — collection={COLLECTION_NAME}, model={EMBEDDINGS_MODEL}")


@app.get("/health")
def health():
    db_ok = ping()
    return {
        "status": "healthy" if db_ok else "degraded",
        "vector_store": "ready" if db_ok else "unavailable",
        "embeddings_model": EMBEDDINGS_MODEL,
        "collection": COLLECTION_NAME,
        "vector_db_dir": str(VECTOR_DB_DIR),
        "manifest": str(MANIFEST_PATH),
        "graph": str(GRAPH_PATH),
    }


app.include_router(ingest_router)
app.include_router(graph_rebuild_router)
app.include_router(query_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=BACKEND_PORT)
