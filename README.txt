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
11. A FastAPI interface that groups duplicate sentences and loads their corpus
    locations only when the user opens a result card.

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
During the offline build, identical sentence text is stored once in
sentence_groups. Its file and line occurrences are stored separately in
sentence_locations and indexed by sentence_id. The web search therefore ranks
five unique sentences without grouping a large exact-match result set online.

Build the offline index:

    python -m autocomplete build Archive

Start the online service:

    python -m autocomplete serve

Start the FastAPI web interface:

    python -m pip install -r requirements.txt
    python -m autocomplete ui

Then open http://127.0.0.1:8000 in a browser. The JSON API is available at
POST /api/completions, and FastAPI's interactive API documentation is at /docs.
Locations for a returned sentence are paginated at
GET /api/completions/{sentence_id}/locations.

Run the current tests from the repository root:

    python -m unittest discover -s tests -v
