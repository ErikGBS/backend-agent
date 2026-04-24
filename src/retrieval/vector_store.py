import uuid

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from src.core.config import settings

_qdrant = QdrantClient(url=settings.qdrant_url)
_openai = OpenAI(api_key=settings.openai_api_key)

COLLECTION = "backend_agent"
VECTOR_SIZE = 1536  # text-embedding-3-small


def ensure_collection() -> None:
    existing = {c.name for c in _qdrant.get_collections().collections}
    if COLLECTION not in existing:
        _qdrant.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def chunk_id(repo: str, file_path: str, idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{repo}:{file_path}:{idx}"))


def _batch_embed(texts: list[str]) -> list[list[float]]:
    resp = _openai.embeddings.create(
        model="text-embedding-3-small",
        input=[t[:8000] for t in texts],
    )
    return [d.embedding for d in resp.data]


def upsert_chunks(chunks: list[dict]) -> None:
    """chunks: [{id, text, repo, project, file_path, chunk_type}]"""
    if not chunks:
        return
    vectors = _batch_embed([c["text"] for c in chunks])
    points = [
        PointStruct(
            id=c["id"],
            vector=v,
            payload={k: val for k, val in c.items() if k != "id"},
        )
        for c, v in zip(chunks, vectors)
    ]
    _qdrant.upsert(collection_name=COLLECTION, points=points)


def search(query: str, top_k: int = 8, project: str | None = None) -> list[dict]:
    qvec = _batch_embed([query])[0]
    filt = None
    if project:
        filt = Filter(must=[FieldCondition(key="project", match=MatchValue(value=project))])
    result = _qdrant.query_points(
        collection_name=COLLECTION,
        query=qvec,
        query_filter=filt,
        limit=top_k,
    )
    return [h.payload | {"score": h.score} for h in result.points]
