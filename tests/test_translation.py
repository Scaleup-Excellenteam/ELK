"""Unit tests for the Spanish translation helper, with the network call mocked."""

import unittest
from unittest.mock import patch

from deep_translator.exceptions import NotValidPayload

from autocomplete.models import AutoCompleteData, GroupedAutoCompleteData
from autocomplete.translation import translate_results, translate_text


class TranslateTextTests(unittest.TestCase):
    def test_returns_the_translated_text(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.translate",
            return_value="Documentación de Python.",
        ) as translate:
            result = translate_text("Python documentation.")

        self.assertEqual(result, "Documentación de Python.")
        translate.assert_called_once_with("Python documentation.")

    def test_targets_spanish_by_default(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.__init__",
            return_value=None,
        ) as init:
            with patch(
                "autocomplete.translation.GoogleTranslator.translate",
                return_value="translated",
            ):
                translate_text("hello")

        init.assert_called_once_with(source="en", target="es")

    def test_can_target_another_language(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.__init__",
            return_value=None,
        ) as init:
            with patch(
                "autocomplete.translation.GoogleTranslator.translate",
                return_value="translated",
            ):
                translate_text("hello", target_language="fr")

        init.assert_called_once_with(source="en", target="fr")

    def test_returns_the_original_text_on_empty_or_blank_input(self) -> None:
        with patch("autocomplete.translation.GoogleTranslator.translate") as translate:
            self.assertEqual(translate_text(""), "")
            self.assertEqual(translate_text("   "), "   ")

        translate.assert_not_called()

    def test_falls_back_to_the_original_text_on_a_translation_service_error(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.translate",
            side_effect=NotValidPayload("bad payload"),
        ):
            result = translate_text("Python documentation.")

        self.assertEqual(result, "Python documentation.")

    def test_falls_back_to_the_original_text_on_an_unexpected_error(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.translate",
            side_effect=ConnectionError("network unreachable"),
        ):
            result = translate_text("Python documentation.")

        self.assertEqual(result, "Python documentation.")

    def test_falls_back_to_the_original_text_when_the_service_returns_nothing(self) -> None:
        with patch(
            "autocomplete.translation.GoogleTranslator.translate",
            return_value=None,
        ):
            result = translate_text("Python documentation.")

        self.assertEqual(result, "Python documentation.")


class TranslateResultsTests(unittest.TestCase):
    def test_translates_the_completed_sentence_of_each_result(self) -> None:
        results = [
            AutoCompleteData(
                completed_sentence="Python documentation is useful.",
                source_text="a.txt",
                offset=1,
                score=10,
            ),
            AutoCompleteData(
                completed_sentence="Another sentence.",
                source_text="b.txt",
                offset=2,
                score=5,
            ),
        ]
        translations = {
            "Python documentation is useful.": "La documentación de Python es útil.",
            "Another sentence.": "Otra oración.",
        }

        with patch(
            "autocomplete.translation.translate_text",
            side_effect=lambda text, target_language="es": translations[text],
        ):
            translated = translate_results(results)

        self.assertEqual(
            [result.completed_sentence for result in translated],
            ["La documentación de Python es útil.", "Otra oración."],
        )
        # Every other field is preserved untouched.
        self.assertEqual([result.source_text for result in translated], ["a.txt", "b.txt"])
        self.assertEqual([result.offset for result in translated], [1, 2])
        self.assertEqual([result.score for result in translated], [10, 5])

    def test_works_with_grouped_results_too(self) -> None:
        results = [
            GroupedAutoCompleteData(
                sentence_id=1,
                completed_sentence="Python documentation is useful.",
                score=10,
                occurrence_count=3,
            ),
        ]

        with patch(
            "autocomplete.translation.translate_text",
            return_value="La documentación de Python es útil.",
        ):
            translated = translate_results(results)

        self.assertEqual(translated[0].completed_sentence, "La documentación de Python es útil.")
        self.assertEqual(translated[0].sentence_id, 1)
        self.assertEqual(translated[0].occurrence_count, 3)

    def test_returns_an_empty_list_unchanged(self) -> None:
        with patch("autocomplete.translation.translate_text") as translate:
            self.assertEqual(translate_results([]), [])

        translate.assert_not_called()

    def test_does_not_mutate_the_original_results(self) -> None:
        original = AutoCompleteData(
            completed_sentence="Python documentation is useful.",
            source_text="a.txt",
            offset=1,
            score=10,
        )

        with patch(
            "autocomplete.translation.translate_text",
            return_value="La documentación de Python es útil.",
        ):
            translate_results([original])

        self.assertEqual(original.completed_sentence, "Python documentation is useful.")


if __name__ == "__main__":
    unittest.main()
