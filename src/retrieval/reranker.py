import logging

logger = logging.getLogger(__name__)

_ranker = None  # lazy-initialized on first use


def _get_ranker():
    global _ranker
    if _ranker is None:
        try:
            from flashrank import Ranker
            _ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="/tmp/flashrank")
            logger.info("reranker loaded: ms-marco-MiniLM-L-12-v2")
        except Exception as exc:
            logger.warning("reranker_init_failed error=%s — reranking disabled", exc)
    return _ranker


def rerank(query: str, hits: list[dict], top_n: int = 6) -> list[dict]:
    """Re-order Qdrant hits by cross-encoder relevance score.

    Falls back to original order if the reranker is unavailable.
    """
    if not hits:
        return hits

    ranker = _get_ranker()
    if ranker is None:
        logger.warning("reranker unavailable — returning top-%d by vector score", top_n)
        return hits[:top_n]

    try:
        from flashrank import RerankRequest
        passages = [{"id": i, "text": h.get("text", "")} for i, h in enumerate(hits)]
        request = RerankRequest(query=query, passages=passages)
        results = ranker.rerank(request)

        reranked = []
        for r in results[:top_n]:
            hit = dict(hits[r["id"]])
            hit["vector_score"] = hit.pop("score", 0.0)
            hit["rerank_score"] = float(r["score"])
            reranked.append(hit)

        logger.debug(
            "rerank query=%r candidates=%d → kept=%d top_score=%.3f",
            query[:60], len(hits), len(reranked),
            reranked[0]["rerank_score"] if reranked else 0,
        )
        return reranked
    except Exception as exc:
        logger.warning("rerank_failed error=%s — returning top-%d by vector score", exc, top_n)
        return hits[:top_n]
