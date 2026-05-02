import logging

from flashrank import Ranker, RerankRequest

logger = logging.getLogger(__name__)

# ms-marco-MiniLM-L-12-v2: ~33MB, fast, good quality for code/text retrieval
_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="/tmp/flashrank")


def rerank(query: str, hits: list[dict], top_n: int = 6) -> list[dict]:
    """Re-order Qdrant hits by cross-encoder relevance score.

    Args:
        query: The original user query.
        hits:  Raw hits from vector_store.search (each has at least a 'text' key).
        top_n: How many results to keep after reranking.

    Returns:
        Up to top_n hits sorted by rerank score descending, each augmented with
        a 'rerank_score' field and a 'vector_score' field (original cosine score).
    """
    if not hits:
        return hits

    passages = [{"id": i, "text": h.get("text", "")} for i, h in enumerate(hits)]
    request = RerankRequest(query=query, passages=passages)
    results = _ranker.rerank(request)

    reranked = []
    for r in results[:top_n]:
        hit = dict(hits[r["id"]])
        hit["vector_score"] = hit.pop("score", 0.0)
        hit["rerank_score"] = float(r["score"])
        reranked.append(hit)

    logger.debug(
        "rerank query=%r candidates=%d → kept=%d top_score=%.3f",
        query[:60],
        len(hits),
        len(reranked),
        reranked[0]["rerank_score"] if reranked else 0,
    )
    return reranked
