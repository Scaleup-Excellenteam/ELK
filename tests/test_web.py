import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from autocomplete.engine import get_best_unique_completions
from autocomplete.index import build_index
from autocomplete.proto import completions_pb2
from autocomplete.web import _apply_query_message, create_app


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
        self.assertIn('role="combobox"', response.text)
        self.assertIn('placeholder="Search for a sentence…"', response.text)
        self.assertNotIn("python documentatjon", response.text)
        self.assertNotIn("Index ready", response.text)

    def test_interface_searches_as_the_input_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(app, "GET", "/static/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn('queryInput.addEventListener("input", queueSuggestions)', response.text)
        self.assertIn(
            'queryInput.addEventListener("click", showSuggestionsForCurrentInput)',
            response.text,
        )
        self.assertIn("setTimeout(() => requestSuggestions(query), 150)", response.text)
        self.assertIn("new WebSocket", response.text)
        self.assertIn("createEditMessage(socketQuery, query)", response.text)
        self.assertIn('fetch("/api/completions"', response.text)

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

    def test_reuses_cached_results_for_equivalent_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            with patch(
                "autocomplete.web.get_best_unique_completions",
                wraps=get_best_unique_completions,
            ) as search:
                first = self._request(
                    app,
                    "POST",
                    "/api/completions",
                    json={"query": "Python documentation"},
                )
                second = self._request(
                    app,
                    "POST",
                    "/api/completions",
                    json={"query": "  PYTHON   DOCUMENTATION!!!  "},
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["suggestions"], second.json()["suggestions"])
        self.assertEqual(search.call_count, 1)

    def test_completion_logs_report_cache_hits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            with patch("autocomplete.web.log_event") as log_event:
                for query in ("python documentation", "PYTHON DOCUMENTATION!!!"):
                    response = self._request(
                        app,
                        "POST",
                        "/api/completions",
                        json={"query": query},
                    )
                    self.assertEqual(response.status_code, 200)

        completion_calls = [
            call for call in log_event.call_args_list if call.args == ("completion",)
        ]
        self.assertEqual(len(completion_calls), 2)
        self.assertFalse(completion_calls[0].kwargs["cache_hit"])
        self.assertTrue(completion_calls[1].kwargs["cache_hit"])

    def test_cache_evicts_the_least_recently_used_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            app = self._build_test_app(temporary_path)
            app.state.completion_cache = type(app.state.completion_cache)(2)

            with patch(
                "autocomplete.web.get_best_unique_completions",
                wraps=get_best_unique_completions,
            ) as search:
                for query in ("python", "unrelated", "python", "completely", "unrelated"):
                    response = self._request(
                        app,
                        "POST",
                        "/api/completions",
                        json={"query": query},
                    )
                    self.assertEqual(response.status_code, 200)

        self.assertEqual(search.call_count, 4)

    def test_records_only_an_explicitly_selected_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))
            search_response = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "python doc"},
            )
            suggestion = search_response.json()["suggestions"][0]

            with patch("autocomplete.web.log_event") as log_event:
                selection_response = self._request(
                    app,
                    "POST",
                    "/api/completions/selection",
                    json={
                        "query": "python doc",
                        "sentence_id": suggestion["sentence_id"],
                        "rank": 1,
                        "elapsed_ms": 12.345,
                    },
                )

        self.assertEqual(selection_response.status_code, 204)
        log_event.assert_called_once()
        event, = log_event.call_args.args
        fields = log_event.call_args.kwargs
        self.assertEqual(event, "completion_selected")
        self.assertEqual(fields["query"], "python doc")
        self.assertEqual(
            fields["completed_sentence"],
            "Python documentation is useful.",
        )
        self.assertEqual(fields["rank"], 1)
        self.assertEqual(fields["search_elapsed_ms"], 12.35)
        self.assertEqual(fields["occurrence_count"], 2)
        self.assertEqual(
            fields["characters_saved"],
            len("Python documentation is useful.") - len("python doc"),
        )

    def test_rejects_a_selection_for_an_unknown_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(
                app,
                "POST",
                "/api/completions/selection",
                json={
                    "query": "python",
                    "sentence_id": 999_999,
                    "rank": 1,
                    "elapsed_ms": 10.0,
                },
            )

        self.assertEqual(response.status_code, 404)

    def test_admin_feed_requests_only_selected_completions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(app, "GET", "/static/admin.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn("event=completion_selected", response.text)
        self.assertIn("details.completed_sentence", response.text)

    def test_admin_stats_include_recent_performance_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))
            activity = {
                "search_count": 4,
                "average_latency_ms": 12.5,
                "p95_latency_ms": 30.0,
                "cache_hits": 3,
                "cache_hit_rate": 75.0,
                "selected_completions": 2,
                "characters_saved": 18,
                "slow_searches": 0,
                "error_count": 1,
                "latency_samples": [10.0, 30.0, 5.0, 5.0],
            }

            with patch("autocomplete.web.get_activity_summary", return_value=activity):
                response = self._request(app, "GET", "/api/admin/stats")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key, value in activity.items():
            self.assertEqual(body[key], value)

    def test_admin_interface_uses_mission_briefing_and_latency_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            page = self._request(app, "GET", "/admin")
            script = self._request(app, "GET", "/static/admin.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Mission briefing", page.text)
        self.assertIn("Cache hit rate", page.text)
        self.assertIn("Characters saved", page.text)
        self.assertIn("latency-chart", page.text)
        self.assertIn("/api/admin/mission-briefing", script.text)

    def test_websocket_applies_edits_and_returns_completions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            with TestClient(app) as client:
                with client.websocket_connect("/ws/completions") as websocket:
                    websocket.send_json({"type": "set", "query": "python"})
                    first = websocket.receive_json()
                    websocket.send_json(
                        {
                            "type": "edit",
                            "keep": 6,
                            "delete": 0,
                            "insert": " documentation",
                        }
                    )
                    second = websocket.receive_json()

        self.assertEqual(first["type"], "suggestions")
        self.assertEqual(first["query"], "python")
        self.assertEqual(second["query"], "python documentation")
        self.assertEqual(
            second["suggestions"][0]["completed_sentence"],
            "Python documentation is useful.",
        )

    def test_websocket_and_http_share_the_completion_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            with patch(
                "autocomplete.web.get_best_unique_completions",
                wraps=get_best_unique_completions,
            ) as search:
                with TestClient(app) as client:
                    with client.websocket_connect("/ws/completions") as websocket:
                        websocket.send_json(
                            {"type": "set", "query": "Python documentation"}
                        )
                        websocket.receive_json()

                    response = client.post(
                        "/api/completions",
                        json={"query": "PYTHON DOCUMENTATION!!!"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(search.call_count, 1)

    def test_invalid_websocket_edit_does_not_change_query_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            with TestClient(app) as client:
                with client.websocket_connect("/ws/completions") as websocket:
                    websocket.send_json({"type": "set", "query": "python"})
                    websocket.receive_json()
                    websocket.send_json(
                        {"type": "edit", "keep": 99, "delete": 0, "insert": "x"}
                    )
                    error = websocket.receive_json()
                    websocket.send_json(
                        {"type": "edit", "keep": 6, "delete": 0, "insert": " docs"}
                    )
                    recovered = websocket.receive_json()

        self.assertEqual(error["type"], "error")
        self.assertEqual(error["query"], "python")
        self.assertEqual(recovered["query"], "python docs")

    def test_query_edit_can_replace_text_in_the_middle(self) -> None:
        updated = _apply_query_message(
            "documantation",
            {"type": "edit", "keep": 5, "delete": 2, "insert": "en"},
        )

        self.assertEqual(updated, "documentation")

    def test_rejects_unsupported_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "Ω"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Only English", response.json()["detail"])

    def test_binary_completions_match_json_and_use_fewer_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            json_response = self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "python documentatjon"},
            )

            request_proto = completions_pb2.CompletionRequestProto(
                query="python documentatjon"
            )
            binary_response = self._request(
                app,
                "POST",
                "/api/completions/binary",
                content=request_proto.SerializeToString(),
                headers={"Content-Type": "application/x-protobuf"},
            )

        self.assertEqual(binary_response.status_code, 200)
        response_proto = completions_pb2.CompletionResponseProto()
        response_proto.ParseFromString(binary_response.content)

        self.assertEqual(response_proto.normalized_query, "python documentatjon")
        self.assertEqual(len(response_proto.suggestions), 1)
        self.assertEqual(
            response_proto.suggestions[0].completed_sentence,
            "Python documentation is useful.",
        )
        self.assertEqual(response_proto.suggestions[0].occurrence_count, 2)

        # The whole point of the binary endpoint: same data, fewer bytes on
        # the wire than the human-readable JSON envelope.
        self.assertLess(len(binary_response.content), len(json_response.content))

    def test_binary_completions_rejects_malformed_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            response = self._request(
                app,
                "POST",
                "/api/completions/binary",
                content=b"\xff\xff\xff not a valid protobuf message",
                headers={"Content-Type": "application/x-protobuf"},
            )

        self.assertEqual(response.status_code, 400)

    def test_health_check_reports_unavailable_without_an_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app = self._build_test_app(Path(temporary_directory))

            self._request(
                app,
                "POST",
                "/api/completions",
                json={"query": "python"},
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                response = self._request(app, "POST", "/api/admin/health-check")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertIn("GEMINI_API_KEY", body["summary"])

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
