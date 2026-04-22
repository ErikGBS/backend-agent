"""Script para construir el índice inicial desde Azure DevOps."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.indexer.index_builder import build_index


async def main() -> None:
    print("Iniciando indexado de repositorios...")
    index = await build_index()
    print(f"Indexado completo: {len(index.repos)} repos")
    for name, repo in index.repos.items():
        print(f"  [{repo.project}/{repo.repo_type}] {name} — {len(repo.endpoints)} endpoints, {len(repo.tree)} archivos")


if __name__ == "__main__":
    asyncio.run(main())
