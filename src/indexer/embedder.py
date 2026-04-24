from src.models.index import RepoIndex
from src.retrieval.vector_store import chunk_id, upsert_chunks

_BATCH_SIZE = 100
_LINES_PER_CHUNK = 80


def index_repo_vectors(repo: RepoIndex) -> None:
    chunks: list[dict] = []

    for ep in repo.endpoints:
        chunks.append({
            "id": chunk_id(repo.name, ep.file, hash(ep.path) & 0xFFFFFFFF),
            "text": f"[{ep.method}] {ep.path} — archivo: {ep.file}",
            "repo": repo.name,
            "project": repo.project,
            "file_path": ep.file,
            "chunk_type": "endpoint",
        })

    for file_path, content in repo.key_files.items():
        lines = content.splitlines()
        for i in range(0, len(lines), _LINES_PER_CHUNK):
            chunk_text = "\n".join(lines[i : i + _LINES_PER_CHUNK]).strip()
            if not chunk_text:
                continue
            chunks.append({
                "id": chunk_id(repo.name, file_path, i),
                "text": chunk_text,
                "repo": repo.name,
                "project": repo.project,
                "file_path": file_path,
                "chunk_type": "code",
            })

    for i in range(0, len(chunks), _BATCH_SIZE):
        upsert_chunks(chunks[i : i + _BATCH_SIZE])
