import json
import logging

from pydantic import BaseModel

from src.models.query import AgentQuery, RefinementAnalysis

logger = logging.getLogger(__name__)

_REFLECTION_PROMPT = """Eres un revisor técnico senior. Recibirás una historia de usuario y el análisis
técnico generado por un agente. Tu trabajo es evaluar si el análisis está completo y preciso.

Evalúa estos criterios:

1. **Cobertura de repos**: ¿Hay repos que deberían estar afectados pero no aparecen en repos_impacted?
2. **Open questions investigables**: ¿Hay open_questions que podrían resolverse buscando en el código
   en vez de quedar como preguntas abiertas?
3. **Señales de complejidad**: ¿complexity_signals está vacío o es demasiado genérico?
4. **Endpoints**: ¿Los endpoints_affected tienen rutas reales con método HTTP o son vagos?
5. **Flow**: ¿El flujo técnico refleja los pasos reales del código o es genérico?

Responde ÚNICAMENTE con este JSON:
{
  "approved": true | false,
  "verdict": "Una frase explicando la decisión",
  "gaps": [
    "Buscar en repo X el servicio Y que maneja Z",
    "Verificar si existe migración para la nueva columna",
    "Confirmar el schema QuotationResponse en el archivo schemas/"
  ]
}

- approved=true si el análisis es sólido y los gaps son menores o inexistentes.
- approved=false si hay gaps concretos que mejorarían el análisis significativamente.
- gaps debe contener instrucciones de búsqueda específicas, no observaciones vagas.
- Si approved=true, gaps puede ser [].
- Máximo 3 gaps. Solo los más importantes.
"""


class ReflectionResult(BaseModel):
    approved: bool
    verdict: str
    gaps: list[str]


async def reflect(
    query: AgentQuery,
    analysis: RefinementAnalysis,
    repos_used: list[str],
    files_used: list[str],
    client,
    model: str,
) -> ReflectionResult:
    """Evaluate the quality of a RefinementAnalysis and return gaps if any."""

    user_content = (
        f"## Historia de usuario\n{query.query}\n\n"
        f"## Proyecto seleccionado\n{query.project or 'todos'}\n\n"
        f"## Repos consultados\n{', '.join(repos_used) or 'ninguno'}\n\n"
        f"## Archivos leídos\n{len(files_used)} archivos\n\n"
        f"## Análisis generado\n```json\n{analysis.model_dump_json(indent=2)}\n```"
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=512,
            system=_REFLECTION_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = next((b.text for b in response.content if b.type == "text"), "{}")
        # Strip markdown fences if present
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        result = ReflectionResult.model_validate(data)
        logger.info(
            "reflection approved=%s verdict=%r gaps=%d",
            result.approved,
            result.verdict[:80],
            len(result.gaps),
        )
        return result
    except Exception as exc:
        logger.warning("reflection_failed error=%s — defaulting to approved", exc)
        return ReflectionResult(approved=True, verdict="Reflection falló, se aprueba por defecto", gaps=[])
