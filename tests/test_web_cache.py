"""Tests for the completion cache wired into the web application."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from autocomplete.cache import LruCache
from autocomplete.engine import get_best_unique_completions
from autocomplete.web import create_app

from .support import TemporaryCorpusTestCase


_CORPUS_TEXT = (
    "Python documentation is useful.\n"
    "Completely unrelated.\n"
    "Python documentation is useful.\n"
)


class CompletionCacheTests(TemporaryCorpusTestCase):
    def build_app(self, text: str = _CORPUS_TEXT, **app_options):
        index_path = self.build_index_from({"sentences.txt": text})
        self.index_path = index_path
        return create_app(index_path, **app_options)

    def request(self, app, method: str, path: str, **kwargs):
        async def send_request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send_request())

    def search(self, app, query: str):
        return self.request(app, "POST", "/api/completions", json={"query": query})

    def patched_search(self):
        return patch(
            "autocomplete.web.get_best_unique_completions",
            wraps=get_best_unique_completions,
        )

    def test_uses_a_cache_sized_by_the_create_app_argument(self) -> None:
        app = self.build_app(cache_capacity=3)

        self.assertIsInstance(app.state.completion_cache, LruCache)
        for query in ("one", "two", "three", "four"):
            self.assertEqual(self.search(app, query).status_code, 200)
        self.assertEqual(len(app.state.completion_cache), 3)

    def test_rejects_an_invalid_cache_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "capacity must be at least 1"):
            create_app(self.missing_index_path(), cache_capacity=0)

    def test_starts_with_an_empty_cache(self) -> None:
        app = self.build_app()

        self.assertEqual(len(app.state.completion_cache), 0)

    def test_stores_one_entry_per_distinct_normalized_query(self) -> None:
        app = self.build_app()

        self.search(app, "python")
        self.search(app, "python")
        self.search(app, "unrelated")

        self.assertEqual(len(app.state.completion_cache), 2)

    def test_serves_a_repeated_query_without_searching_the_index_again(self) -> None:
        app = self.build_app()

        with self.patched_search() as search:
            first = self.search(app, "python documentation")
            second = self.search(app, "python documentation")

        self.assertEqual(search.call_count, 1)
        self.assertEqual(first.json()["suggestions"], second.json()["suggestions"])

    def test_caches_a_query_that_has_no_suggestions(self) -> None:
        app = self.build_app()

        with self.patched_search() as search:
            first = self.search(app, "nothing here matches at all")
            second = self.search(app, "nothing here matches at all")

        self.assertEqual(search.call_count, 1)
        self.assertEqual(first.json()["suggestions"], [])
        self.assertEqual(second.json()["suggestions"], [])

    def test_keeps_the_echoed_query_of_each_request_out_of_the_cache(self) -> None:
        app = self.build_app()

        first = self.search(app, "Python documentation")
        second = self.search(app, "  PYTHON   DOCUMENTATION!!!  ")

        self.assertEqual(first.json()["query"], "Python documentation")
        self.assertEqual(second.json()["query"], "  PYTHON   DOCUMENTATION!!!  ")
        self.assertEqual(first.json()["normalized_query"], "python documentation")
        self.assertEqual(second.json()["normalized_query"], "python documentation")

    def test_does_not_share_results_between_different_queries(self) -> None:
        app = self.build_app()

        with self.patched_search() as search:
            python_response = self.search(app, "python")
            unrelated_response = self.search(app, "unrelated")

        self.assertEqual(search.call_count, 2)
        self.assertNotEqual(
            python_response.json()["suggestions"],
            unrelated_response.json()["suggestions"],
        )

    def test_rebuilding_the_index_invalidates_cached_results(self) -> None:
        app = self.build_app()
        first = self.search(app, "python")

        rebuilt_corpus = self.temporary_path / "corpus" / "sentences.txt"
        rebuilt_corpus.write_text(
            "Python documentation is useful.\n"
            "Python documentation is excellent.\n",
            encoding="utf-8",
        )
        from autocomplete.index import build_index

        build_index(rebuilt_corpus.parent, self.index_path)
        # Guarantee a different modification time even on coarse clocks.
        rebuilt_stat = self.index_path.stat()
        os.utime(
            self.index_path,
            ns=(rebuilt_stat.st_atime_ns, rebuilt_stat.st_mtime_ns + 1_000_000_000),
        )

        with self.patched_search() as search:
            second = self.search(app, "python")

        self.assertEqual(search.call_count, 1)
        self.assertNotEqual(first.json()["suggestions"], second.json()["suggestions"])

    def test_evicts_the_least_recently_used_query_at_capacity(self) -> None:
        app = self.build_app(cache_capacity=2)

        with self.patched_search() as search:
            for query in ("python", "unrelated", "python", "completely", "unrelated"):
                self.assertEqual(self.search(app, query).status_code, 200)

        # "unrelated" is evicted while "completely" is inserted, so only the
        # repeat of "python" is served from the cache.
        self.assertEqual(search.call_count, 4)
        self.assertEqual(len(app.state.completion_cache), 2)

    def test_does_not_cache_rejected_queries(self) -> None:
        app = self.build_app()

        empty = self.search(app, "!!!")
        unsupported = self.search(app, "Ω")

        self.assertEqual(empty.status_code, 422)
        self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(len(app.state.completion_cache), 0)

    def test_does_not_cache_anything_while_the_index_is_missing(self) -> None:
        app = create_app(self.missing_index_path())

        response = self.search(app, "python")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(app.state.completion_cache), 0)

    def test_serves_a_query_cached_before_the_index_disappeared_as_unavailable(self) -> None:
        app = self.build_app()
        self.search(app, "python")

        Path(self.index_path).unlink()

        self.assertEqual(self.search(app, "python").status_code, 503)

    def test_clearing_the_cache_forces_a_fresh_search(self) -> None:
        app = self.build_app()
        self.search(app, "python")

        app.state.completion_cache.clear()
        with self.patched_search() as search:
            response = self.search(app, "python")

        self.assertEqual(search.call_count, 1)
        self.assertEqual(response.status_code, 200)

    def test_each_application_keeps_its_own_cache(self) -> None:
        first_app = self.build_app()
        second_app = create_app(self.index_path)

        self.search(first_app, "python")

        self.assertEqual(len(first_app.state.completion_cache), 1)
        self.assertEqual(len(second_app.state.completion_cache), 0)

    def test_a_cached_response_still_reports_an_elapsed_time(self) -> None:
        app = self.build_app()
        self.search(app, "python")

        cached = self.search(app, "python")

        self.assertGreaterEqual(cached.json()["elapsed_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
