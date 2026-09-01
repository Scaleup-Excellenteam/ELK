"""Combine candidate retrieval and scoring into final autocomplete results."""

import heapq
from dataclasses import dataclass
from pathlib import Path

from .index import (
    find_exact_matches,
    find_exact_sentence_groups,
    iter_candidate_entries,
    iter_candidate_sentence_groups,
    iter_glob_candidate_entries,
    iter_glob_sentence_groups,
    ranked_one_edit_glob_groups,
)
from .models import AutoCompleteData, GroupedAutoCompleteData
from .normalization import is_supported_normalized_query, normalize_text
from .scoring import best_normalized_match_score


DEFAULT_INDEX_PATH = Path("autocomplete.sqlite3")
_MAX_COMPLETIONS = 5


def _result_from_entry(entry, score: int) -> AutoCompleteData:
    return AutoCompleteData(
        completed_sentence=entry.original_sentence,
        source_text=entry.source_text,
        offset=entry.offset,
        score=score,
    )


def _ranking_key(result: AutoCompleteData):
    return (
        -result.score,
        result.completed_sentence.casefold(),
        result.completed_sentence,
        result.source_text,
        result.offset,
    )


@dataclass
class _WorstResultFirst:
    """Reverse result ranking so the worst retained result is the heap root."""

    result: AutoCompleteData

    def __lt__(self, other: "_WorstResultFirst") -> bool:
        return _ranking_key(self.result) > _ranking_key(other.result)


def get_best_k_completions(
    prefix: str,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> list[AutoCompleteData]:
    """Return the five highest-scoring completions for ``prefix``."""

    normalized_prefix = normalize_text(prefix)
    if not normalized_prefix or not is_supported_normalized_query(normalized_prefix):
        return []

    exact_matches = find_exact_matches(
        index_path,
        normalized_prefix,
        limit=_MAX_COMPLETIONS,
    )

    if len(exact_matches) == _MAX_COMPLETIONS:
        exact_score = 2 * len(normalized_prefix)
        return [_result_from_entry(entry, exact_score) for entry in exact_matches]

    best_results_heap: list[_WorstResultFirst] = []
    seen_locations: set[tuple[str, int]] = set()

    def consider_candidate(candidate) -> None:
        location = (candidate.source_text, candidate.offset)
        if location in seen_locations:
            return
        seen_locations.add(location)

        score = best_normalized_match_score(
            normalized_prefix,
            candidate.normalized_sentence,
        )
        if score is None:
            return

        result = _result_from_entry(candidate, score)
        heap_item = _WorstResultFirst(result)

        if len(best_results_heap) < _MAX_COMPLETIONS:
            heapq.heappush(best_results_heap, heap_item)
        elif _ranking_key(result) < _ranking_key(best_results_heap[0].result):
            heapq.heapreplace(best_results_heap, heap_item)

    for exact_match in exact_matches:
        consider_candidate(exact_match)

    if len(normalized_prefix) <= 5:
        pattern_groups = ranked_one_edit_glob_groups(normalized_prefix)

        for group_number, (_maximum_score, patterns) in enumerate(pattern_groups):
            for candidate in iter_glob_candidate_entries(index_path, patterns):
                consider_candidate(candidate)

            if len(best_results_heap) < _MAX_COMPLETIONS:
                continue

            next_group_number = group_number + 1
            if next_group_number == len(pattern_groups):
                break

            next_maximum_score = pattern_groups[next_group_number][0]
            worst_retained_score = best_results_heap[0].result.score
            if worst_retained_score > next_maximum_score:
                break
    else:
        for candidate in iter_candidate_entries(index_path, normalized_prefix):
            consider_candidate(candidate)

    return sorted(
        (heap_item.result for heap_item in best_results_heap),
        key=_ranking_key,
    )


def _group_ranking_key(result: GroupedAutoCompleteData):
    return (
        -result.score,
        -result.occurrence_count,
        result.completed_sentence.casefold(),
        result.completed_sentence,
        result.sentence_id,
    )


@dataclass
class _WorstGroupFirst:
    """Reverse unique-result ranking so the worst result is the heap root."""

    result: GroupedAutoCompleteData

    def __lt__(self, other: "_WorstGroupFirst") -> bool:
        return _group_ranking_key(self.result) > _group_ranking_key(other.result)


def get_best_unique_completions(
    prefix: str,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> list[GroupedAutoCompleteData]:
    """Return five ranked sentences, grouping all duplicate locations."""

    normalized_prefix = normalize_text(prefix)
    if not normalized_prefix or not is_supported_normalized_query(normalized_prefix):
        return []

    exact_groups = find_exact_sentence_groups(
        index_path,
        normalized_prefix,
        limit=_MAX_COMPLETIONS,
    )
    exact_score = 2 * len(normalized_prefix)
    if len(exact_groups) == _MAX_COMPLETIONS:
        return [
            GroupedAutoCompleteData(
                sentence_id=sentence_id,
                completed_sentence=original_sentence,
                score=exact_score,
                occurrence_count=occurrence_count,
            )
            for sentence_id, original_sentence, _normalized, occurrence_count in exact_groups
        ]

    best_results_heap: list[_WorstGroupFirst] = []
    seen_sentence_ids: set[int] = set()

    def consider_group(group) -> None:
        sentence_id, original_sentence, normalized_sentence, occurrence_count = group
        if sentence_id in seen_sentence_ids:
            return
        seen_sentence_ids.add(sentence_id)

        score = best_normalized_match_score(normalized_prefix, normalized_sentence)
        if score is None:
            return

        result = GroupedAutoCompleteData(
            sentence_id=sentence_id,
            completed_sentence=original_sentence,
            score=score,
            occurrence_count=occurrence_count,
        )
        heap_item = _WorstGroupFirst(result)
        if len(best_results_heap) < _MAX_COMPLETIONS:
            heapq.heappush(best_results_heap, heap_item)
        elif _group_ranking_key(result) < _group_ranking_key(
            best_results_heap[0].result
        ):
            heapq.heapreplace(best_results_heap, heap_item)

    for exact_group in exact_groups:
        consider_group(exact_group)

    if len(normalized_prefix) <= 5:
        pattern_groups = ranked_one_edit_glob_groups(normalized_prefix)
        for group_number, (_maximum_score, patterns) in enumerate(pattern_groups):
            for candidate_group in iter_glob_sentence_groups(index_path, patterns):
                consider_group(candidate_group)

            if len(best_results_heap) < _MAX_COMPLETIONS:
                continue
            next_group_number = group_number + 1
            if next_group_number == len(pattern_groups):
                break
            if (
                best_results_heap[0].result.score
                > pattern_groups[next_group_number][0]
            ):
                break
    else:
        for candidate_group in iter_candidate_sentence_groups(
            index_path,
            normalized_prefix,
        ):
            consider_group(candidate_group)

    return sorted(
        (heap_item.result for heap_item in best_results_heap),
        key=_group_ranking_key,
    )
