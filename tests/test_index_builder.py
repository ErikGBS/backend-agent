import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.indexer.index_builder import _clear_cache, _persist, _set_cache, load_index
from src.models.index import GlobalIndex, RepoIndex


def make_index(repo_name: str = "maestro-bff-api") -> GlobalIndex:
    repo = RepoIndex(
        name=repo_name, repo_id="r1", project="Cantera",
        repo_type="python", tree=[], endpoints=[], services=[], schemas=[], key_files={},
    )
    return GlobalIndex(repos={repo_name: repo}, last_updated="2026-01-01T00:00:00")


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_cache()
    yield
    _clear_cache()


# --- caché en memoria ---

class TestLoadIndexCache:
    def test_first_call_reads_from_disk(self, tmp_path):
        index = make_index()
        index_file = tmp_path / "index.json"
        index_file.write_text(index.model_dump_json())

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(index_file)
            result = load_index()

        assert result is not None
        assert "maestro-bff-api" in result.repos

    def test_second_call_returns_cache_without_reading_disk(self, tmp_path):
        index = make_index()
        index_file = tmp_path / "index.json"
        index_file.write_text(index.model_dump_json())

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(index_file)
            load_index()
            index_file.unlink()  # delete the file
            result = load_index()  # should still work from cache

        assert result is not None

    def test_returns_none_when_no_file_and_no_cache(self, tmp_path):
        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(tmp_path / "nonexistent.json")
            result = load_index()

        assert result is None

    def test_set_cache_makes_load_skip_disk(self, tmp_path):
        index = make_index("cached-repo")
        _set_cache(index)

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(tmp_path / "nonexistent.json")
            result = load_index()

        assert result is not None
        assert "cached-repo" in result.repos

    def test_clear_cache_forces_next_load_from_disk(self, tmp_path):
        index = make_index()
        _set_cache(index)
        _clear_cache()

        index_file = tmp_path / "index.json"
        fresh = make_index("fresh-repo")
        index_file.write_text(fresh.model_dump_json())

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(index_file)
            result = load_index()

        assert result is not None
        assert "fresh-repo" in result.repos


# --- escritura atómica ---

class TestPersistAtomic:
    def test_writes_final_file(self, tmp_path):
        index = make_index()
        index_file = tmp_path / "index.json"

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(index_file)
            _persist(index)

        assert index_file.exists()
        data = json.loads(index_file.read_text())
        assert "maestro-bff-api" in data["repos"]

    def test_tmp_file_removed_after_write(self, tmp_path):
        index = make_index()
        index_file = tmp_path / "index.json"
        tmp_file = tmp_path / "index.json.tmp"

        with patch("src.indexer.index_builder.settings") as mock_settings:
            mock_settings.index_path = str(index_file)
            _persist(index)

        assert not tmp_file.exists()

    def test_uses_os_replace_for_atomicity(self, tmp_path):
        index = make_index()
        index_file = tmp_path / "index.json"

        with patch("src.indexer.index_builder.settings") as mock_settings, \
             patch("src.indexer.index_builder.os.replace") as mock_replace:
            mock_settings.index_path = str(index_file)
            _persist(index)

        mock_replace.assert_called_once_with(
            str(index_file) + ".tmp",
            str(index_file),
        )


# --- lock de concurrencia ---

class TestIndexLock:
    async def test_concurrent_writes_are_serialized(self, tmp_path):
        """Two concurrent _reindex_repo calls must not interleave their writes."""
        from src.indexer.index_builder import index_lock

        write_order = []

        async def simulate_write(label: str) -> None:
            async with index_lock:
                write_order.append(f"{label}_start")
                await asyncio.sleep(0)  # yield to let other coroutine try
                write_order.append(f"{label}_end")

        await asyncio.gather(
            simulate_write("A"),
            simulate_write("B"),
        )

        # One must fully complete before the other starts
        assert write_order.index("A_end") < write_order.index("B_start") or \
               write_order.index("B_end") < write_order.index("A_start")

    async def test_lock_released_on_exception(self):
        from src.indexer.index_builder import index_lock

        try:
            async with index_lock:
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass

        # Lock must be released — a second acquire should not hang
        acquired = False
        async with index_lock:
            acquired = True
        assert acquired
