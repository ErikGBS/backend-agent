"""
RAG Evaluation Metrics — equivalentes a RAGAS pero usando Claude directamente.

Métricas implementadas:
  - context_relevancy:  ¿los chunks recuperados son útiles para la query?
  - faithfulness:       ¿el análisis está anclado en el código recuperado?
  - answer_relevancy:   ¿la respuesta responde la pregunta real?

Cada métrica devuelve un float entre 0.0 y 1.0.
"""
import json
import logging

import anthropic

logger = logging.getLogger(__name__)

_EVAL_MODEL = "claude-haiku-4-5-20251001"  # modelo barato para evaluación

_CONTEXT_RELEVANCY_PROMPT = """Eres un evaluador de sistemas RAG.
Dada una pregunta y un conjunto de fragmentos de código recuperados, evalúa si los fragmentos
son relevantes para responder la pregunta.

Pregunta: {query}

Fragmentos recuperados:
{contexts}

Responde SOLO con este JSON:
{{"score": <float 0.0-1.0>, "reason": "<una frase breve>"}}

- 1.0 = todos los fragmentos son directamente relevantes
- 0.5 = mezcla de relevantes e irrelevantes
- 0.0 = ningún fragmento es relevante para la pregunta"""

_FAITHFULNESS_PROMPT = """Eres un evaluador de sistemas RAG.
Dada una respuesta de análisis técnico y los fragmentos de código en los que se basa,
evalúa si las afirmaciones del análisis están respaldadas por el código recuperado.

Análisis generado:
{answer}

Fragmentos de código recuperados:
{contexts}

Responde SOLO con este JSON:
{{"score": <float 0.0-1.0>, "reason": "<una frase breve>"}}

- 1.0 = todas las afirmaciones están respaldadas por el código
- 0.5 = algunas afirmaciones no tienen respaldo en el código recuperado
- 0.0 = el análisis inventa paths, clases o lógica que no aparece en el código"""

_ANSWER_RELEVANCY_PROMPT = """Eres un evaluador de sistemas RAG.
Dada una pregunta (historia de usuario) y la respuesta generada por el agente,
evalúa si la respuesta aborda directamente lo que pide la historia.

Historia de usuario: {query}

Respuesta del agente:
{answer}

Responde SOLO con este JSON:
{{"score": <float 0.0-1.0>, "reason": "<una frase breve>"}}

- 1.0 = la respuesta aborda completa y directamente la historia
- 0.5 = la respuesta es parcialmente relevante
- 0.0 = la respuesta no responde lo que pide la historia"""


async def _score(client: anthropic.AsyncAnthropic, prompt: str) -> tuple[float, str]:
    """Call Claude and extract score + reason from JSON response."""
    try:
        response = await client.messages.create(
            model=_EVAL_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next((b.text for b in response.content if b.type == "text"), "{}")
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        return float(data.get("score", 0.0)), str(data.get("reason", ""))
    except Exception as exc:
        logger.warning("eval_score_failed error=%s", exc)
        return 0.0, f"Error: {exc}"


async def context_relevancy(
    query: str, contexts: list[str], client: anthropic.AsyncAnthropic
) -> tuple[float, str]:
    contexts_text = "\n---\n".join(contexts[:6])  # máximo 6 chunks
    prompt = _CONTEXT_RELEVANCY_PROMPT.format(query=query, contexts=contexts_text)
    return await _score(client, prompt)


async def faithfulness(
    answer: str, contexts: list[str], client: anthropic.AsyncAnthropic
) -> tuple[float, str]:
    contexts_text = "\n---\n".join(contexts[:6])
    prompt = _FAITHFULNESS_PROMPT.format(answer=answer[:3000], contexts=contexts_text)
    return await _score(client, prompt)


async def answer_relevancy(
    query: str, answer: str, client: anthropic.AsyncAnthropic
) -> tuple[float, str]:
    prompt = _ANSWER_RELEVANCY_PROMPT.format(query=query, answer=answer[:3000])
    return await _score(client, prompt)
