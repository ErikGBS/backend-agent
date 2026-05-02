# Backend Agent

> API REST que actúa como agente de IA para guiar al equipo de desarrollo backend en la implementación de nuevas funcionalidades sobre los proyectos **Cantera** y **Progresol**, respetando sus arquitecturas y convenciones.

## ¿Qué hace?

El agente indexa los repositorios de Azure DevOps (chunks vectorizados con `text-embedding-3-small` en Qdrant + un índice estructural en `data/index.json`) y, ante una historia de usuario, deja que Claude Sonnet decida qué buscar usando *tool use*. El resultado es un briefing de impacto técnico (repos afectados, endpoints, schemas, complejidad y preguntas abiertas) en JSON + Markdown.

```
Consulta del equipo (+ proyecto seleccionado)
      │
      ▼
Claude Sonnet 4.6 ─┐
      │           │  loop de tool use:
      │           │   • search_code   (Qdrant + embeddings, filtrado por proyecto)
      │           │   • fetch_file    (Azure DevOps REST)
      │           │   • list_endpoints / get_schema  (índice estructural)
      ▼           │
JSON RefinementAnalysis ← se valida y se devuelve al cliente
      │
      ▼
AgentResponse: answer (markdown) + analysis + repos_consulted + files_fetched
```

## Proyectos que conoce

> Los nombres-marca **Cantera** y **Progresol** corresponden a proyectos reales en Azure DevOps llamados `maestro` y `product development` respectivamente. Esos son los valores que viajan en `AZURE_DEVOPS_PROJECTS` y en el campo `project` del request.

### Cantera — proyecto ADO `maestro`
- Repos: `maestro-bff-api` (BFF FastAPI), `maestro-backoffice-ui`
- Patrón: Service & Repository (Clean Architecture), FastAPI + SQLAlchemy
- Estructura típica de `maestro-bff-api`: `app/api/v1/` → `app/application/services/` → `app/infrastructure/repositories/`
- Schemas Pydantic v2 en `app/application/schemas/`; mappers en `app/application/mappers.py`
- Toda la DI en `app/infrastructure/dependencies.py` (`get_*_repo`, `get_*_service`)
- ORM columns en camelCase; tablas PascalCase con esquema `Workforce`

### Progresol — proyecto ADO `product development`
- Repo BFF principal: `nanaykuna-bff-integration`
- Backoffice API: `nanaykuna-backoffice-api`
- Múltiples Azure Functions en .NET y Python (`nanaykuna-*-functions`) con triggers HTTP, Queue y Timer

## Requisitos

- Python 3.11+
- Cuenta Anthropic con API key activa
- Cuenta OpenAI con API key (se usa `text-embedding-3-small` para los embeddings de Qdrant)
- Qdrant accesible (local vía Docker o remoto)
- PAT de Azure DevOps con permiso de lectura sobre los repos a indexar
- Docker (opcional, para despliegue en contenedor)

## Configuración

```bash
cp .env.example .env
```

Editar `.env` con los valores reales:

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | API key de OpenAI (embeddings) | `sk-proj-...` |
| `AZURE_DEVOPS_ORG` | Organización en Azure DevOps | `mi-org` |
| `AZURE_DEVOPS_PAT` | Personal Access Token (lectura de repos) | `xxxx...` |
| `AZURE_DEVOPS_PROJECTS` | Lista JSON con los nombres reales de los proyectos en ADO | `["maestro","product development"]` |
| `CLAUDE_MODEL` | Modelo de Claude a usar | `claude-sonnet-4-6` |
| `INDEX_PATH` | Ruta del archivo de índice estructural | `data/index.json` |
| `QDRANT_URL` | URL del servicio Qdrant | `http://localhost:6333` |
| `API_KEY` | Clave para autenticar `/agent/query` | `mi-clave-interna` |
| `WEBHOOK_SECRET` | (Opcional) `username:password` para Basic Auth en el webhook de ADO | `azureDevOps:s3cret` |

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

Requiere Qdrant corriendo. Si no lo tienes en otro lado, levántalo solo:

```bash
docker compose up -d qdrant   # http://localhost:6333
```

Asegúrate de que `QDRANT_URL=http://localhost:6333` en `.env` (cuando corres uvicorn fuera del contenedor, `qdrant` como hostname no resuelve). Luego:

```bash
uvicorn src.main:app --reload
```

La API queda disponible en `http://localhost:8000`. Documentación interactiva en `http://localhost:8000/docs`. UI en `http://localhost:8000/ui`.

### Docker

```bash
docker compose up --build
```

Levanta dos servicios: `backend-agent` (publicado en `http://localhost:8080`, mapea al `:8000` interno) y `qdrant` (`:6333`). Monta `./data` como volumen para persistir el índice estructural y los datos de Qdrant.

Cuando se corre dentro de docker-compose, `QDRANT_URL=http://qdrant:6333` (el nombre del servicio).

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
  "project": "maestro",
  "image_base64": null,
  "image_media_type": "image/jpeg"
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `query` | string | sí | Consulta o historia de usuario |
| `project` | string | no | Nombre real del proyecto en ADO: `"maestro"` (Cantera), `"product development"` (Progresol) o `null` (ambos). El servidor fuerza este filtro en todas las herramientas — el LLM no puede saltárselo. |
| `image_base64` | string | no | Imagen adjunta codificada en base64 (ej. screenshot de ticket) |
| `image_media_type` | string | no | MIME type de la imagen; por defecto `image/jpeg` |

**Response `200`**

```json
{
  "answer": "# Historial de cotizaciones rechazadas\n## Objetivo\n...",
  "analysis": {
    "title": "...",
    "summary": "...",
    "repos_impacted": [{"repo": "maestro-bff-api", "project": "maestro", "reason": "...", "touch_type": "MODIFICAR"}],
    "endpoints_affected": ["GET /api/v1/quotations/rejected"],
    "schemas_affected": ["QuotationSummaryResponse"],
    "db_changes": [],
    "external_integrations": [],
    "complexity_signals": ["..."],
    "open_questions": ["..."],
    "flow": [{"label": "...", "detail": "...", "kind": "endpoint"}],
    "markdown": "..."
  },
  "repos_consulted": ["maestro-bff-api"],
  "files_fetched": ["maestro-bff-api:app/api/v1/quotations.py"]
}
```

`analysis` es `null` si Claude no devolvió un JSON válido (en ese caso `answer` contiene la respuesta cruda).

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
  -d '{"query": "Agregar endpoint para crear un postulante en Cantera", "project": "maestro"}' \
  | python -m json.tool
```

---

### `POST /api/v1/webhook/azure-devops`

Recibe el evento `push` de Azure DevOps y reindexa el repo afectado en background (incremental: borra los chunks de Qdrant del repo y los vuelve a crear). Configurar en Azure DevOps → Project Settings → Service Hooks → Web Hooks, apuntando a `https://<host>/api/v1/webhook/azure-devops`.

No requiere `X-API-Key`. Si `WEBHOOK_SECRET=usuario:password` está definido en `.env`, el webhook valida Basic Auth — en Azure DevOps se configura en el campo "Basic authentication username/password" del service hook. Si la variable no está, el endpoint queda abierto.

**Response `200`**

```json
{"status": "accepted"}
```

## Estructura del repositorio

```
src/
├── main.py                    # FastAPI app, routers, /health, /ui
├── core/
│   └── config.py              # Settings via pydantic-settings + .env
├── agent/
│   ├── core.py                # run_agent(): loop de tool use con Claude
│   ├── tools.py               # TOOLS + execute_tool() (search_code, fetch_file, list_endpoints, get_schema)
│   └── prompt.py              # SYSTEM_PROMPT con reglas de cada proyecto y formato JSON
├── api/v1/
│   ├── agent.py               # POST /agent/query (autenticado), GET /agent/report
│   └── webhook.py             # POST /webhook/azure-devops (Basic Auth opcional)
├── indexer/
│   ├── azure_devops.py        # Cliente REST de Azure DevOps (PAT)
│   ├── crawler_python.py      # Extrae endpoints/services/schemas de repos Python
│   ├── crawler_dotnet.py      # Extrae endpoints/services de repos .NET
│   ├── embedder.py            # Trocea archivos y los manda a Qdrant
│   └── index_builder.py       # build_index(), load_index(), reindex_repo()
├── retrieval/
│   └── vector_store.py        # Cliente Qdrant: ensure_collection, upsert_chunks, search()
├── static/
│   └── ui.html                # UI mínima servida en /ui
└── models/
    ├── index.py               # GlobalIndex, RepoIndex, EndpointInfo
    └── query.py               # AgentQuery, AgentResponse, RefinementAnalysis, RepoImpact, FlowNode
scripts/
└── run_indexer.py             # Job offline: python scripts/run_indexer.py
data/
├── index.json                 # Índice estructural generado por el indexer (ignorado por git)
└── qdrant/                    # Storage persistente de Qdrant (ignorado por git)
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
