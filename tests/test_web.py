import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from autocomplete.index import build_index
from autocomplete.web import create_app


class AutocompleteWebTests(unittest.TestCase):
    def _build_test_app(self, temporary_path: Path):
        corpus_path = temporary_path / "corpus"
        corpus_path.mkdir()
        (corpus_path / "sentences.txt").write_text(
            "Python documentation is useful.\n"
            "Completely unrelated.\n"
            "Python documentation is useful.\n",
            encoding="utf-8",
        )
        index_path = temporary_path / "autocomplete.sqlite3"
        build_index(corpus_path, index_path)
        return create_app(index_path)

    def _request(self, app, method: str, path: str, **kwargs):
        async def send_request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send_request())

    def test_serves_the_web_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(app, "GET", "/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sentence Autocomplete", response.text)

    def test_static_styles_respect_hidden_interface_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(app, "GET", "/static/styles.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("[hidden]", response.text)
        self.assertIn("display: none !important", response.text)

    def test_returns_completion_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "python documentatjon"},
            )

            body = response.json()
            locations = self._request(
                app,
                "GET",
                f'/api/completions/{body["suggestions"][0]["sentence_id"]}/locations',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["normalized_query"], "python documentatjon")
        self.assertEqual(len(body["suggestions"]), 1)
        self.assertEqual(
            body["suggestions"][0]["completed_sentence"],
            "Python documentation is useful.",
        )
        self.assertEqual(body["suggestions"][0]["occurrence_count"], 2)
        self.assertEqual(locations.status_code, 200)
        self.assertEqual(
            locations.json()["locations"],
            [
                {"source_text": "sentences.txt", "offset": 1},
                {"source_text": "sentences.txt", "offset": 3},
            ],
        )

    def test_rejects_unsupported_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "א"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Only English", response.json()["detail"])

    def test_reports_a_missing_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_index = Path(temporary_directory) / "missing.sqlite3"
            app = create_app(missing_index)

            health = self._request(app, "GET", "/api/health")
            search = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "python"},
            )

        self.assertFalse(health.json()["index_ready"])
        self.assertEqual(search.status_code, 503)


if __name__ == "__main__":
    unittest.main()
