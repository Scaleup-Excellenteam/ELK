"""Zero-downtime hand-off at the web layer: the app resolves the current
snapshot pointer fresh on every request, so a snapshot published by a
concurrent offline build is served without restarting the process.
"""

import asyncio
import unittest

import httpx

from autocomplete.snapshot import build_snapshot
from autocomplete.web import create_app
from tests.support import TemporaryCorpusTestCase


class WebSnapshotTestCase(TemporaryCorpusTestCase):
    def request(self, app, method: str, path: str, **options):
        async def send_request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **options)

        return asyncio.run(send_request())

    def search(self, app, query: str):
        return self.request(app, "POST", "/api/completions", json={"query": query})


class PointerModeServingTests(WebSnapshotTestCase):
    def test_serves_completions_from_the_pointed_snapshot(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        build_snapshot(
            self.write_corpus({"a.txt": "Python documentation is useful.\n"}),
            base_index_path,
        )
        app = create_app(base_index_path)

        response = self.search(app, "python")

        self.assertEqual(response.status_code, 200)
        suggestions = response.json()["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(
            suggestions[0]["completed_sentence"], "Python documentation is useful."
        )

    def test_reports_a_ready_index_from_the_pointed_snapshot(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        build_snapshot(self.write_corpus({"a.txt": "One.\n"}), base_index_path)
        app = create_app(base_index_path)

        response = self.request(app, "GET", "/api/health")

        self.assertEqual(response.json(), {"status": "ok", "index_ready": True})

    def test_admin_stats_reports_the_resolved_snapshot_path(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        snapshot_path, _count = build_snapshot(
            self.write_corpus({"a.txt": "One.\nTwo.\n"}), base_index_path
        )
        app = create_app(base_index_path)

        stats = self.request(app, "GET", "/api/admin/stats").json()

        self.assertTrue(stats["index_ready"])
        self.assertEqual(stats["index_path"], str(snapshot_path))
        self.assertEqual(stats["sentence_count"], 2)

    def test_locations_endpoint_reads_the_pointed_snapshot(self) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        build_snapshot(
            self.write_corpus({"a.txt": "Python documentation is useful.\n"}),
            base_index_path,
        )
        app = create_app(base_index_path)
        sentence_id = self.search(app, "python").json()["suggestions"][0]["sentence_id"]

        response = self.request(
            app, "GET", f"/api/completions/{sentence_id}/locations"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["locations"],
            [{"source_text": "a.txt", "offset": 1}],
        )

    def test_reports_index_not_ready_when_no_pointer_and_no_direct_file_exist(
        self,
    ) -> None:
        app = create_app(self.temporary_path / "autocomplete.sqlite3")

        response = self.search(app, "python")

        self.assertEqual(response.status_code, 503)


class HotSwapTests(WebSnapshotTestCase):
    def test_a_pointer_flip_is_served_on_the_next_request_without_recreating_the_app(
        self,
    ) -> None:
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        build_snapshot(
            self.write_corpus({"a.txt": "Old snapshot sentence.\n"}),
            base_index_path,
        )
        app = create_app(base_index_path)

        before = self.search(app, "old snapshot")
        self.assertEqual(len(before.json()["suggestions"]), 1)
        before_missing = self.search(app, "new snapshot")
        self.assertEqual(before_missing.json()["suggestions"], [])

        second_corpus_dir = self.temporary_path / "corpus2"
        second_corpus_dir.mkdir()
        (second_corpus_dir / "b.txt").write_text(
            "New snapshot sentence.\n", encoding="utf-8"
        )
        build_snapshot(second_corpus_dir, base_index_path)

        after = self.search(app, "new snapshot")
        self.assertEqual(len(after.json()["suggestions"]), 1)
        self.assertEqual(
            after.json()["suggestions"][0]["completed_sentence"],
            "New snapshot sentence.",
        )

    def test_in_flight_style_reads_of_the_old_snapshot_stay_valid_after_a_flip(
        self,
    ) -> None:
        # The old snapshot file is never modified or deleted when a new one
        # is published, so anything that already resolved it keeps working.
        base_index_path = self.temporary_path / "autocomplete.sqlite3"
        old_snapshot_path, _count = build_snapshot(
            self.write_corpus({"a.txt": "Old snapshot sentence.\n"}),
            base_index_path,
        )
        app = create_app(base_index_path)

        second_corpus_dir = self.temporary_path / "corpus2"
        second_corpus_dir.mkdir()
        (second_corpus_dir / "b.txt").write_text(
            "New snapshot sentence.\n", encoding="utf-8"
        )
        build_snapshot(second_corpus_dir, base_index_path)

        old_app = create_app(old_snapshot_path)
        still_old = self.search(old_app, "old snapshot")
        self.assertEqual(len(still_old.json()["suggestions"]), 1)

        current = self.search(app, "new snapshot")
        self.assertEqual(len(current.json()["suggestions"]), 1)


if __name__ == "__main__":
    unittest.main()
