"""Golden dataset para evaluar el agente de refinamiento."""
from dataclasses import dataclass


@dataclass
class EvalCase:
    id: str
    project: str
    query: str
    expected_repos: list[str]   # repos que deberían aparecer en repos_impacted
    notes: str = ""


EVAL_DATASET: list[EvalCase] = [
    EvalCase(
        id="cantera-001",
        project="Cantera",
        query=(
            "Como administrador quiero ver un historial de cotizaciones rechazadas "
            "de los últimos 30 días, con filtro por fecha y motivo de rechazo."
        ),
        expected_repos=["maestro-bff-api"],
        notes="Endpoint GET + query SQLAlchemy + paginación",
    ),
    EvalCase(
        id="cantera-002",
        project="Cantera",
        query=(
            "Como maestro de obra quiero poder adjuntar fotos a una orden de trabajo "
            "para documentar el avance antes de cerrarla."
        ),
        expected_repos=["maestro-bff-api"],
        notes="Upload de archivos, storage, schema de orden",
    ),
    EvalCase(
        id="progresol-001",
        project="Progresol",
        query=(
            "Como ferretero quiero recibir una notificación cuando un maestro de obra "
            "solicita una cotización para mis productos."
        ),
        expected_repos=["nanaykuna-bff-integration"],
        notes="Integración con Azure Functions, trigger HTTP o Queue",
    ),
    EvalCase(
        id="progresol-002",
        project="Progresol",
        query=(
            "Como administrador de Progresol quiero exportar el reporte mensual "
            "de ventas por ferretería en formato CSV."
        ),
        expected_repos=["nanaykuna-backoffice-api"],
        notes="Endpoint de exportación, query agregada, streaming de archivo",
    ),
]
