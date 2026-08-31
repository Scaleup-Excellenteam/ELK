ELK - sentence autocomplete

Current milestone
-----------------
The repository currently contains only the shared foundations:

1. AutoCompleteData - the result format required by the assignment.
2. normalize_text - the rules used to compare corpus text with user input.
3. iter_corpus_entries - recursive reading of text files and their line numbers.
4. build_index - offline storage in a SQLite trigram search index.
5. find_exact_matches - the first online search operation.
6. best_match_score - exact and one-character-edit assignment scoring.
7. iter_candidate_entries - indexed candidate retrieval for typo checking.
8. get_best_k_completions - ranked final AutoCompleteData results.
9. A build/serve command-line interface with continuation and # reset.
10. Unit tests for normalization, corpus reading, search, scoring, ranking, and CLI.

The completion engine combines indexed candidate retrieval, one-edit scoring,
stable top-five ranking, and an interactive command-line interface.
Top-k approximate results are maintained in a bounded heap, using O(k) memory
and O(M log k) selection time for M scored candidates.
Queries of up to five characters use a small set of wildcard patterns instead
of alphabet-generated variants. Those patterns are searched in
best-possible-score groups, and branch-and-bound stops once no remaining group
can enter the top five.
Queries of length 6 or more use two balanced anchors and require one match.
With at most one edit, at least one of the two halves must remain unchanged.

Build the offline index:

    python -m autocomplete build Archive

Start the online service:

    python -m autocomplete serve

Run the current tests from the repository root:

    python -m unittest discover -s tests -v
