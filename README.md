# Backend Agent

> API REST que actúa como agente de IA para guiar al equipo de desarrollo backend en la implementación de nuevas funcionalidades sobre los proyectos **Cantera** y **Progresol**, respetando sus arquitecturas y convenciones.

## ¿Qué hace?

El agente indexa los repositorios de Azure DevOps y, ante una consulta (historia de usuario, requerimiento, duda técnica), recupera los archivos más relevantes del código y los pasa a Claude Sonnet junto con el contexto de arquitectura. La respuesta incluye rutas exactas, código concreto y un checklist pre-commit.

```
Consulta del equipo
      │
      ▼
search_repos(index)        ← búsqueda por tokens en nombre/endpoints/servicios
      │
      ▼
fetch_relevant_files()     ← descarga archivos clave desde Azure DevOps
      │
      ▼
Claude Sonnet 4.6          ← prompt de sistema con reglas de cada proyecto
      │
      ▼
AgentResponse: answer + repos_consulted + files_fetched
```

## Proyectos que conoce

### Cantera (`maestro-bff-api`)
- Patrón: Service & Repository (Clean Architecture), FastAPI + SQLAlchemy
- Estructura: `app/api/v1/` → `app/application/services/` → `app/infrastructure/repositories/`
- Schemas Pydantic v2 en `app/application/schemas/`; mappers en `app/application/mappers.py`
- Toda la DI en `app/infrastructure/dependencies.py` (`get_*_repo`, `get_*_service`)
- ORM columns en camelCase; tablas PascalCase con esquema `Workforce`

### Progresol
- Múltiples Azure Functions en .NET y Python
- Triggers HTTP, Queue y Timer según el caso

## Requisitos

- Python 3.11+
- Cuenta Anthropic con API key activa
- PAT de Azure DevOps con permiso de lectura sobre los repos de Cantera y Progresol
- Docker (opcional, para despliegue en contenedor)

## Configuración

```bash
cp .env.example .env
```

Editar `.env` con los valores reales:

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic | `sk-ant-...` |
| `AZURE_DEVOPS_ORG` | Organización en Azure DevOps | `mi-org` |
| `AZURE_DEVOPS_PAT` | Personal Access Token (lectura de repos) | `xxxx...` |
| `AZURE_DEVOPS_PROJECTS` | Lista JSON de proyectos a indexar | `["Cantera","Progresol"]` |
| `CLAUDE_MODEL` | Modelo de Claude a usar | `claude-sonnet-4-6` |
| `INDEX_PATH` | Ruta del archivo de índice generado | `data/index.json` |
| `API_KEY` | Clave para autenticar las peticiones a esta API | `mi-clave-interna` |

## Instalación local

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Construir el índice

El índice (`data/index.json`) es un snapshot de los repos de Azure DevOps con endpoints, servicios, schemas y archivos clave. Hay que generarlo antes del primer uso y actualizarlo cuando los repos cambien significativamente.

```bash
python scripts/run_indexer.py
```

El script conecta a Azure DevOps, detecta automáticamente si cada repo es Python o .NET (por presencia de `.csproj`), y guarda el índice en `data/index.json` (ignorado por git).

El índice también se actualiza de forma incremental y automática cuando llega un webhook de Azure DevOps — ver sección de endpoints.

## Levantar el API

### Local

```bash
uvicorn src.main:app --reload
```

La API queda disponible en `http://localhost:8000`. Documentación interactiva en `http://localhost:8000/docs`.

### Docker

```bash
docker compose up --build
```

Expone el puerto `8000` y monta `./data` como volumen para persistir el índice.

## Endpoints

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

### `POST /api/v1/agent/query`

Consulta al agente. Requiere autenticación por header.

**Headers**

```
X-API-Key: <valor de API_KEY en .env>
Content-Type: application/json
```

**Body**

```json
{
  "query": "Necesito agregar un endpoint para listar candidatos por estado",
  "project": "Cantera",
  "image_base64": null,
  "image_media_type": "image/jpeg"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `query` | string | sí | Consulta o historia de usuario |
| `project` | string | no | `"Cantera"`, `"Progresol"` o `null` (ambos) |
| `image_base64` | string | no | Imagen adjunta codificada en base64 (ej. screenshot de ticket) |
| `image_media_type` | string | no | MIME type de la imagen; por defecto `image/jpeg` |

**Response `200`**

```json
{
  "answer": "## Endpoint: GET /api/v1/candidates\n...",
  "repos_consulted": ["maestro-bff-api"],
  "files_fetched": ["app/api/v1/candidates.py", "app/application/services/candidate_service.py"]
}
```

**Errores**

| Código | Causa |
|---|---|
| `403` | `X-API-Key` inválida o ausente |
| `503` | Índice no disponible — ejecutar `python scripts/run_indexer.py` primero |

**Ejemplo con curl**

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/query \
  -H "X-API-Key: mi-clave-interna" \
  -H "Content-Type: application/json" \
  -d '{"query": "Agregar endpoint para crear un postulante en Cantera", "project": "Cantera"}' \
  | python -m json.tool
```

---

### `POST /api/v1/webhook/azure-devops`

Recibe el evento `push` de Azure DevOps y reindexar el repo afectado en background. Configurar en Azure DevOps → Project Settings → Service Hooks → Web Hooks, apuntando a `https://<host>/api/v1/webhook/azure-devops`.

No requiere `X-API-Key` (Azure DevOps envía su propio token de validación configurable en la plataforma).

**Response `200`**

```json
{"status": "accepted"}
```

## Estructura del repositorio

```
src/
├── main.py                    # FastAPI app, routers, /health
├── core/
│   └── config.py              # Settings via pydantic-settings + .env
├── agent/
│   ├── core.py                # Orquesta el flujo: search → fetch → Claude
│   └── prompt.py              # SYSTEM_PROMPT con reglas + build_user_message()
├── api/v1/
│   ├── agent.py               # POST /agent/query (autenticado)
│   └── webhook.py             # POST /webhook/azure-devops
├── indexer/
│   ├── azure_devops.py        # Cliente REST de Azure DevOps (PAT)
│   ├── crawler_python.py      # Extrae endpoints/services/schemas de repos Python
│   ├── crawler_dotnet.py      # Extrae endpoints/services de repos .NET
│   └── index_builder.py       # build_index(), load_index(), _persist()
├── retrieval/
│   ├── index_store.py         # search_repos() por tokens, build_context_summary()
│   └── file_fetcher.py        # Descarga archivos relevantes on-demand
└── models/
    ├── index.py               # GlobalIndex, RepoIndex, EndpointInfo
    └── query.py               # AgentQuery, AgentResponse
scripts/
└── run_indexer.py             # Job offline: python scripts/run_indexer.py
data/
└── index.json                 # Generado por el indexer, ignorado por git
```

## Desarrollo

```bash
# Linting y formato
ruff check src/ scripts/ tests/
ruff format src/ scripts/ tests/

# Tests
pytest
```

- Ruff: `line-length = 100`, `target-version = py311`, reglas `E, F, I, UP`
- Pytest: `asyncio_mode = auto`

## Pendientes

- `tests/` está vacío — agregar tests de integración para `agent/core.py`, `retrieval/` y endpoints
- No hay licencia en el repositorio
