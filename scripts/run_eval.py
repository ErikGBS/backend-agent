"""
RAG Evaluation Runner

Ejecuta el dataset de evaluación contra el agente e imprime un reporte
con las métricas de context_relevancy, faithfulness y answer_relevancy.

Uso:
    python scripts/run_eval.py
    python scripts/run_eval.py --case cantera-001
    python scripts/run_eval.py --output results/eval_2026-05-01.json
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic

from src.agent.tools import execute_tool
from src.core.config import settings
from src.evaluation.dataset import EVAL_DATASET, EvalCase
from src.evaluation.metrics import answer_relevancy, context_relevancy, faithfulness
from src.indexer.index_builder import load_index
from src.models.query import AgentQuery
from src.retrieval.reranker import rerank
from src.retrieval.vector_store import search as vector_search


async def run_case(case: EvalCase, eval_client: anthropic.AsyncAnthropic) -> dict:
    """Run a single eval case: retrieve context + run agent + score metrics."""
    print(f"\n  [{case.id}] {case.query[:70]}...")

    index = load_index()
    if not index:
        return {"id": case.id, "error": "Index not available"}

    # ── 1. Retrieve context (same as search_code tool) ──
    hits = vector_search(case.query, top_k=12, project=case.project)
    hits = rerank(case.query, hits, top_n=6)
    contexts = [h.get("text", "") for h in hits]

    # ── 2. Run the agent ──
    from src.agent.core import run_agent
    agent_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    query = AgentQuery(query=case.query, project=case.project)

    t0 = time.monotonic()
    try:
        response = await run_agent(query, index)
    except Exception as exc:
        return {"id": case.id, "error": str(exc)}
    elapsed = time.monotonic() - t0

    answer = response.answer

    # ── 3. Score metrics ──
    cr_score, cr_reason = await context_relevancy(case.query, contexts, eval_client)
    fa_score, fa_reason = await faithfulness(answer, contexts, eval_client)
    ar_score, ar_reason = await answer_relevancy(case.query, answer, eval_client)

    # ── 4. Repo coverage check ──
    repos_found = set(response.repos_consulted)
    repos_expected = set(case.expected_repos)
    repo_hit = repos_expected.issubset(repos_found)

    result = {
        "id": case.id,
        "project": case.project,
        "query": case.query,
        "elapsed_s": round(elapsed, 2),
        "repos_expected": list(repos_expected),
        "repos_found": list(repos_found),
        "repo_coverage": repo_hit,
        "reflection_approved": response.reflection_approved,
        "reflection_verdict": response.reflection_verdict,
        "metrics": {
            "context_relevancy":  {"score": round(cr_score, 3), "reason": cr_reason},
            "faithfulness":       {"score": round(fa_score, 3), "reason": fa_reason},
            "answer_relevancy":   {"score": round(ar_score, 3), "reason": ar_reason},
            "avg":                round((cr_score + fa_score + ar_score) / 3, 3),
        },
    }

    # ── 5. Print inline summary ──
    flag = "PASS" if repo_hit and result["metrics"]["avg"] >= 0.6 else "WARN"
    print(
        f"  {flag} | avg={result['metrics']['avg']:.2f} "
        f"| ctx={cr_score:.2f} | faith={fa_score:.2f} | ans={ar_score:.2f} "
        f"| repos={'OK' if repo_hit else 'MISS'} | {elapsed:.1f}s"
    )
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Run only this case id")
    parser.add_argument("--output", help="Save JSON results to this path")
    args = parser.parse_args()

    cases = EVAL_DATASET
    if args.case:
        cases = [c for c in cases if c.id == args.case]
        if not cases:
            print(f"Case '{args.case}' not found. Available: {[c.id for c in EVAL_DATASET]}")
            sys.exit(1)

    eval_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    print(f"\n{'='*60}")
    print(f"RAG Evaluation — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Cases: {len(cases)}")
    print(f"{'='*60}")

    results = []
    for case in cases:
        result = await run_case(case, eval_client)
        results.append(result)

    # ── Aggregate report ──
    valid = [r for r in results if "error" not in r]
    if valid:
        avg_ctx   = sum(r["metrics"]["context_relevancy"]["score"] for r in valid) / len(valid)
        avg_faith = sum(r["metrics"]["faithfulness"]["score"] for r in valid) / len(valid)
        avg_ans   = sum(r["metrics"]["answer_relevancy"]["score"] for r in valid) / len(valid)
        avg_all   = sum(r["metrics"]["avg"] for r in valid) / len(valid)
        repo_ok   = sum(1 for r in valid if r["repo_coverage"])

        print(f"\n{'='*60}")
        print("RESUMEN")
        print(f"{'='*60}")
        print(f"  Context Relevancy:  {avg_ctx:.3f}")
        print(f"  Faithfulness:       {avg_faith:.3f}")
        print(f"  Answer Relevancy:   {avg_ans:.3f}")
        print(f"  Promedio global:    {avg_all:.3f}")
        print(f"  Repo coverage:      {repo_ok}/{len(valid)}")
        print(f"{'='*60}")

    report = {
        "run_at": datetime.now().isoformat(),
        "total_cases": len(cases),
        "results": results,
        "summary": {
            "context_relevancy": round(avg_ctx, 3),
            "faithfulness": round(avg_faith, 3),
            "answer_relevancy": round(avg_ans, 3),
            "avg": round(avg_all, 3),
            "repo_coverage": f"{repo_ok}/{len(valid)}",
        } if valid else {},
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nResultados guardados en {args.output}")

    return report


if __name__ == "__main__":
    asyncio.run(main())
