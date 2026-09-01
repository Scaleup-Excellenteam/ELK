"""HTTP contract of the FastAPI interface: routing, validation and paging."""

import asyncio
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from autocomplete.proto import completions_pb2
from autocomplete.translation import translate_text
from autocomplete.web import create_app
from tests.support import TemporaryCorpusTestCase


class WebApiTestCase(TemporaryCorpusTestCase):
    """Drive the ASGI application in-process, without a network server."""

    CORPUS = {
        "a.txt": (
            "Python documentation is useful.\n"
            "Completely unrelated line.\n"
            "Python documentation is useful.\n"
        ),
        "nested/b.txt": "Python documentation is useful.\n",
    }

    def setUp(self) -> None:
        super().setUp()
        self.index_path = self.build_index_from(self.CORPUS)
        self.app = create_app(self.index_path)

    def request(self, method: str, path: str, app=None, **options):
        target_app = self.app if app is None else app

        async def send_request():
            transport = httpx.ASGITransport(app=target_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **options)

        return asyncio.run(send_request())

    def sentence_id_of(self, sentence: str) -> int:
        with sqlite3.connect(self.index_path) as connection:
            (sentence_id,) = connection.execute(
                "SELECT id FROM sentence_groups WHERE original_sentence = ?",
                (sentence,),
            ).fetchone()
        return sentence_id


class StaticInterfaceTests(WebApiTestCase):
    def test_serves_the_page_stylesheet_and_script(self) -> None:
        for path in ("/", "/static/index.html", "/static/styles.css", "/static/app.js"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path).status_code, 200)

    def test_the_page_loads_its_own_script_and_stylesheet(self) -> None:
        page = self.request("GET", "/").text

        self.assertIn("/static/styles.css", page)
        self.assertIn("/static/app.js", page)

    def test_the_script_calls_the_documented_endpoints(self) -> None:
        script = self.request("GET", "/static/app.js").text

        self.assertIn("/api/completions", script)
        self.assertIn("/locations", script)

    def test_publishes_interactive_api_documentation(self) -> None:
        self.assertEqual(self.request("GET", "/docs").status_code, 200)

        schema = self.request("GET", "/openapi.json").json()

        self.assertEqual(schema["info"]["title"], "Sentence Autocomplete")
        self.assertIn("/api/completions", schema["paths"])
        self.assertIn("/api/health", schema["paths"])


class HealthEndpointTests(WebApiTestCase):
    def test_reports_a_ready_index(self) -> None:
        response = self.request("GET", "/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "index_ready": True})

    def test_reports_a_missing_index(self) -> None:
        application = create_app(self.missing_index_path())

        response = self.request("GET", "/api/health", app=application)

        self.assertEqual(response.json(), {"status": "ok", "index_ready": False})


class CompletionEndpointTests(WebApiTestCase):
    def test_returns_ranked_unique_suggestions(self) -> None:
        response = self.request(
            "POST",
            "/api/completions",
            json={"query": "  PYTHON, documentation!  "},
        )
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["query"], "  PYTHON, documentation!  ")
        self.assertEqual(body["normalized_query"], "python documentation")
        self.assertGreaterEqual(body["elapsed_ms"], 0)
        self.assertEqual(len(body["suggestions"]), 1)

        suggestion = body["suggestions"][0]
        self.assertEqual(suggestion["completed_sentence"], "Python documentation is useful.")
        self.assertEqual(suggestion["occurrence_count"], 3)
        self.assertEqual(suggestion["score"], 2 * len("python documentation"))
        self.assertEqual(
            suggestion["sentence_id"],
            self.sentence_id_of("Python documentation is useful."),
        )

    def test_returns_an_empty_suggestion_list_when_nothing_matches(self) -> None:
        response = self.request("POST", "/api/completions", json={"query": "zzzzzzzzzz"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggestions"], [])

    def test_answers_a_query_containing_a_typo(self) -> None:
        response = self.request("POST", "/api/completions", json={"query": "documentatixn"})
        suggestions = response.json()["suggestions"]

        self.assertEqual(suggestions[0]["completed_sentence"], "Python documentation is useful.")
        self.assertLess(suggestions[0]["score"], 2 * len("documentatixn"))

    def test_rejects_a_query_without_searchable_characters(self) -> None:
        for query in ["   ", "!!! ---"]:
            with self.subTest(query=query):
                response = self.request("POST", "/api/completions", json={"query": query})

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"],
                    "Enter text containing searchable characters.",
                )

    def test_rejects_text_outside_the_indexed_alphabet(self) -> None:
        response = self.request("POST", "/api/completions", json={"query": "שלום עולם"})

        self.assertEqual(response.status_code, 422)
        self.assertIn("English", response.json()["detail"])

    def test_rejects_a_missing_or_oversized_query_field(self) -> None:
        for payload in [{}, {"query": ""}, {"query": "x" * 501}, {"query": 12}]:
            with self.subTest(payload=payload):
                response = self.request("POST", "/api/completions", json=payload)

                self.assertEqual(response.status_code, 422)

    def test_accepts_a_query_of_the_maximum_length(self) -> None:
        response = self.request("POST", "/api/completions", json={"query": "a" * 500})

        self.assertEqual(response.status_code, 200)

    def test_reports_a_missing_index_as_unavailable(self) -> None:
        application = create_app(self.missing_index_path())

        response = self.request(
            "POST",
            "/api/completions",
            app=application,
            json={"query": "python"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("index is not ready", response.json()["detail"])

    def test_rejects_a_get_request(self) -> None:
        self.assertEqual(self.request("GET", "/api/completions").status_code, 405)


class CompletionTranslationTests(WebApiTestCase):
    """Both API shapes translate results; the translation call is mocked."""

    @staticmethod
    def _translate_to_a_fixed_spanish_sentence(results, *_args, **_kwargs):
        return [
            replace(result, completed_sentence="La documentación de Python es útil.")
            for result in results
        ]

    def test_the_json_endpoint_returns_the_translated_sentence(self) -> None:
        with patch(
            "autocomplete.web.translate_results",
            side_effect=self._translate_to_a_fixed_spanish_sentence,
        ) as translate:
            response = self.request(
                "POST", "/api/completions", json={"query": "python documentation"}
            )

        translate.assert_called_once()
        body = response.json()
        self.assertEqual(
            body["suggestions"][0]["completed_sentence"],
            "La documentación de Python es útil.",
        )

    def test_the_binary_endpoint_returns_the_translated_sentence(self) -> None:
        request_proto = completions_pb2.CompletionRequestProto(query="python documentation")

        with patch(
            "autocomplete.web.translate_results",
            side_effect=self._translate_to_a_fixed_spanish_sentence,
        ):
            response = self.request(
                "POST",
                "/api/completions/binary",
                content=request_proto.SerializeToString(),
                headers={"Content-Type": "application/x-protobuf"},
            )

        response_proto = completions_pb2.CompletionResponseProto()
        response_proto.ParseFromString(response.content)
        self.assertEqual(
            response_proto.suggestions[0].completed_sentence,
            "La documentación de Python es útil.",
        )

    def test_a_translation_failure_falls_back_to_the_english_sentence(self) -> None:
        # translate_text itself never raises (see tests/test_translation.py);
        # this confirms the fallback text still reaches the caller
        # end-to-end when the underlying service call fails. setUp stubs
        # translate_text to skip the network entirely, so it is restored to
        # its real implementation here and the service call underneath it
        # is what is made to fail.
        with patch("autocomplete.translation.translate_text", side_effect=translate_text):
            with patch(
                "autocomplete.translation.GoogleTranslator.translate",
                side_effect=ConnectionError("network unreachable"),
            ):
                response = self.request(
                    "POST", "/api/completions", json={"query": "python documentation"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["suggestions"][0]["completed_sentence"],
            "Python documentation is useful.",
        )


class LocationEndpointTests(WebApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sentence_id = self.sentence_id_of("Python documentation is useful.")

    def test_lists_every_location_of_a_sentence(self) -> None:
        response = self.request("GET", f"/api/completions/{self.sentence_id}/locations")
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            body["locations"],
            [
                {"source_text": "a.txt", "offset": 1},
                {"source_text": "a.txt", "offset": 3},
                {"source_text": "nested/b.txt", "offset": 1},
            ],
        )
        self.assertIsNone(body["next_offset"])

    def test_pages_through_the_locations(self) -> None:
        first_page = self.request(
            "GET",
            f"/api/completions/{self.sentence_id}/locations?limit=2",
        ).json()

        self.assertEqual(len(first_page["locations"]), 2)
        self.assertEqual(first_page["next_offset"], 2)

        second_page = self.request(
            "GET",
            f"/api/completions/{self.sentence_id}/locations?limit=2&offset=2",
        ).json()

        self.assertEqual(second_page["locations"], [{"source_text": "nested/b.txt", "offset": 1}])
        self.assertIsNone(second_page["next_offset"])

    def test_a_page_that_exactly_ends_the_list_has_no_next_offset(self) -> None:
        body = self.request(
            "GET",
            f"/api/completions/{self.sentence_id}/locations?limit=3",
        ).json()

        self.assertEqual(len(body["locations"]), 3)
        self.assertIsNone(body["next_offset"])

    def test_returns_an_empty_page_past_the_end(self) -> None:
        body = self.request(
            "GET",
            f"/api/completions/{self.sentence_id}/locations?offset=99",
        ).json()

        self.assertEqual(body["locations"], [])
        self.assertIsNone(body["next_offset"])

    def test_returns_an_empty_page_for_an_unknown_sentence(self) -> None:
        body = self.request("GET", "/api/completions/987654/locations").json()

        self.assertEqual(body["locations"], [])

    def test_rejects_paging_arguments_outside_their_range(self) -> None:
        for query_string in ["?limit=0", "?limit=101", "?offset=-1", "?limit=abc"]:
            with self.subTest(query_string=query_string):
                response = self.request(
                    "GET",
                    f"/api/completions/{self.sentence_id}/locations{query_string}",
                )

                self.assertEqual(response.status_code, 422)

    def test_rejects_a_sentence_id_that_is_not_a_number(self) -> None:
        response = self.request("GET", "/api/completions/not-a-number/locations")

        self.assertEqual(response.status_code, 422)

    def test_reports_a_missing_index_as_unavailable(self) -> None:
        application = create_app(self.missing_index_path())

        response = self.request(
            "GET",
            "/api/completions/1/locations",
            app=application,
        )

        self.assertEqual(response.status_code, 503)


class ApplicationFactoryTests(TemporaryCorpusTestCase):
    def test_each_application_keeps_its_own_index_path(self) -> None:
        first = create_app("first.sqlite3")
        second = create_app(self.temporary_path / "second.sqlite3")

        self.assertEqual(str(first.state.index_path), "first.sqlite3")
        self.assertEqual(second.state.index_path, self.temporary_path / "second.sqlite3")

    def test_the_module_level_application_uses_the_default_index(self) -> None:
        from autocomplete.engine import DEFAULT_INDEX_PATH
        from autocomplete.web import app

        self.assertEqual(app.state.index_path, DEFAULT_INDEX_PATH)


if __name__ == "__main__":
    unittest.main()
