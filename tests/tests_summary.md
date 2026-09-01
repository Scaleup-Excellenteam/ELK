# Test Suite Summary

File: support.py
- What it tests: Not a test file — shared test infrastructure for creating throwaway corpora and SQLite indexes.
- Expected outcomes: A base TestCase giving each test a clean temporary directory, corpus writer, and built index path.
- Methodology: tempfile plus addCleanup, with helpers to write files, build an index from lines, and return a missing index path.

File: test_cache.py
- What it tests: The bounded LRU cache class backing the online search service.
- Expected outcomes: Correct store/retrieve, a stored None or empty value distinguished from a miss, LRU eviction, capacity never exceeded.
- Methodology: Direct unit tests on the API, edge cases (capacity 0/-1/1), and concurrent writers/readers using Threads and a Barrier.

File: test_cli.py
- What it tests: Basic behavior of the interactive command-line loop (run_interactive).
- Expected outcomes: Typed fragments accumulate into one growing text, '#' resets it, and the engine is called with the right arguments.
- Methodology: Injected fake input/output functions replaying keystrokes, a mocked engine, and one real index in a temp directory.

File: test_cli_commands.py
- What it tests: Argument parsing, command dispatch (build/serve/ui), and the full interactive session loop.
- Expected outcomes: Correct prompts and messages, numbered suggestion lists, EOF/Ctrl+C handling, rejection of missing or unknown commands.
- Methodology: Scripted keystroke replay, output captured via redirect_stdout/stderr, patched components, and running the package as a module in a subprocess.

File: test_corpus.py
- What it tests: Core corpus reading — walking text files, normalizing sentences, and recording source locations.
- Expected outcomes: CorpusEntry objects with original sentence, normalized form, file name, and original line number, in stable order.
- Methodology: Text files and ZIP archives written into temp directories, including PDF page-break control characters and a missing corpus root.

File: test_corpus_edge_cases.py
- What it tests: Corpus reading rules for unusual files and lines.
- Expected outcomes: Only .txt files read, blank/unsearchable lines skipped while line numbers stay correct, ValueError on invalid roots.
- Methodology: Small synthetic corpora (empty directory, empty file, nested paths, string vs Path roots) plus a check that reading stays lazy.

File: test_corpus_zip.py
- What it tests: Reading a corpus straight out of a ZIP archive without extracting to disk.
- Expected outcomes: Only text members read in name order, wrapper directory stripped, separators normalized, UTF-8 decoded correctly.
- Methodology: Synthetic archives with odd members (directories, upper-case extensions, backslash names) and error checks on non-ZIP files.

File: test_engine.py
- What it tests: The main completion engine — get_best_k_completions and get_best_unique_completions.
- Expected outcomes: At most five results sorted by score then alphabetically, typo tolerance, and duplicate corpus locations grouped.
- Methodology: A real index built from a temp corpus, queries of varying length, and checks of optimization paths like early exit and branch-and-bound.

File: test_engine_differential.py
- What it tests: Differential testing — the index-backed engine against an exhaustive scan of the same corpus.
- Expected outcomes: The engine's shortcuts (wildcard pattern groups, anchored retrieval, early exits) never silently lose a better result.
- Methodology: Every corpus line scored by hand and re-ranked, then compared to engine output over random corpora and multi-file corpora.

File: test_engine_ranking.py
- What it tests: Ranking, tie-breaking, and result limits of both the ranked and grouped completion engines.
- Expected outcomes: Order by score, then case-insensitively, then by location; frequent sentences win ties; a missing index raises a database error.
- Methodology: Temp corpora crafted to force ties, plus patches on the index layer to verify early exit and full pattern-group scans.

File: test_index.py
- What it tests: Index construction and core queries — exact matches, candidate retrieval, and one-edit wildcard patterns.
- Expected outcomes: Sentences and locations stored correctly, sorting and limits honored, candidates found even with a typo in an anchor.
- Methodology: Indexes built from both a directory and a ZIP in temp dirs, varying batch sizes, and direct tests of the balanced-split helper.

File: test_index_queries.py
- What it tests: Full coverage of the index layer — stored schema, rebuilds, and every candidate retrieval branch.
- Expected outcomes: Consistent schema and identical data at any batch size, correct location paging, and no scoreable sentence ever dropped.
- Methodology: Direct SQL queries against the built SQLite file, glob patterns cross-checked against scoring, and randomized input tests.

File: test_integration.py
- What it tests: End-to-end behavior across the offline build and both online interfaces — the CLI and the web app.
- Expected outcomes: Index, engine, public scoring helper, interactive session, and HTTP API all agree on the same results.
- Methodology: A multi-file corpus, the real build command, in-process ASGI requests via httpx, and verifying each location quotes a real corpus line.

File: test_models.py
- What it tests: The AutoCompleteData data model carrying a completion result.
- Expected outcomes: The object faithfully keeps the original sentence, source file, line offset, and score.
- Methodology: A single short unit test constructing the object with known values and asserting each field.

File: test_normalization.py
- What it tests: Baseline text normalization and whether text falls inside the supported query domain.
- Expected outcomes: Case, punctuation, and repeated whitespace ignored; empty text when nothing is searchable; the original string untouched.
- Methodology: Direct assertions on short input/output pairs, including characters outside the supported English alphabet.

File: test_normalization_rules.py
- What it tests: The exact character-domain rules applied to user input and corpus text.
- Expected outcomes: All ASCII punctuation removed, digits and letters kept, tabs/newlines as separators, normalization idempotent.
- Methodology: Sweeping all of string.punctuation, whitespace-only and empty edge cases, and asserting normalized English always stays in the domain.

File: test_scoring.py
- What it tests: The best_match_score function against the official worked examples from the assignment appendix.
- Expected outcomes: 2 points per character on an exact match, position-based penalties for substitution/insertion/deletion, more than one edit rejected.
- Methodology: Fixed numeric scores compared against one reference sentence, plus edge cases for empty queries and multiple candidate matches.

File: test_scoring_rules.py
- What it tests: Penalty tables, internal single-edit helpers, and every one-edit scoring branch.
- Expected outcomes: Penalties decrease to a floor, an edit costs twice a substitution early on, transpositions and two-edit cases rejected.
- Methodology: Each edit position tested separately, plus an independent oracle generating random one-edit variants and comparing computed scores.

File: test_web.py
- What it tests: The baseline web application — serving the interface, the completion endpoint, and cache integration.
- Expected outcomes: Valid JSON with ranked suggestions, cached reuse for equivalent queries, rejection of unsupported input and missing index.
- Methodology: The app driven in-process through httpx.ASGITransport with asyncio, over an index built in a temporary directory.

File: test_web_api.py
- What it tests: The HTTP contract of the FastAPI interface — routing, validation, paging, and static assets.
- Expected outcomes: Correct status codes and JSON shapes, oversized/invalid queries rejected, 503 reported when the index is missing.
- Methodology: In-process ASGI requests with no network server, covering every endpoint (health, completions, locations, docs, static files).

File: test_web_cache.py
- What it tests: The completion cache wired into the web application and how it interacts with requests.
- Expected outcomes: One entry per distinct normalized query, repeated queries served without re-searching, rejected queries never cached.
- Methodology: In-process HTTP requests with patched/call-counted engine, eviction tested at small capacity, and index rebuilds to check invalidation.
