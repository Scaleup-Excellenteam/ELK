# ELK Sentence Autocomplete

A typo-tolerant sentence autocomplete engine for a large English text corpus.
The project builds an offline SQLite index and uses it to return the five
highest-ranked sentence completions through a command-line interface, a
FastAPI API, and a browser UI.

## Features

- Exact substring completion and matching with one missing, extra, or replaced character.
- SQLite FTS5 trigram index for efficient candidate retrieval.
- Bounded min-heap for selecting the best five results.
- Duplicate sentence grouping with all corpus locations preserved.
- Popularity-aware tie-breaking for the browser UI.
- Live browser suggestions that update while the user types.
- Recursive corpus loading from nested `.txt` files.
- Windows, Linux, and macOS support.

## Requirements

- Python 3.10 or newer.
- A local copy of the corpus as an `Archive` directory or `Archive.zip` file.
- SQLite with FTS5 support, included in standard modern Python distributions.

Run all commands from the repository root—the directory containing
`requirements.txt` and the `autocomplete` package.

## Quick start

After Python is installed and the corpus is available:

```console
python -m pip install -r requirements.txt
python -m autocomplete build Archive
python -m autocomplete ui
```

If the corpus is supplied as a ZIP, replace the build command with:

```console
python -m autocomplete build Archive.zip
```

Open <http://127.0.0.1:8000> in a browser.

The `build` command is required only the first time, or after the corpus has
changed. Normal application startup requires only:

```console
python -m autocomplete ui
```

## 1. Get the project

Clone the repository, or pull the latest `main` branch if it already exists:

```console
git clone https://github.com/Scaleup-Excellenteam/ELK.git
cd ELK
```

For an existing clone:

```console
git switch main
git pull --ff-only origin main
```

## 2. Create a virtual environment

Using a virtual environment keeps the project dependencies separate from the
rest of the computer.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, allow scripts only for the current terminal
session and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

After activation, the remaining commands use `python` on every operating
system.

## 3. Prepare the corpus

The application reads the corpus in either of these forms:

1. An extracted directory named `Archive`.
2. A ZIP file such as `Archive.zip`, read directly without extraction.

Directory example:

```text
ELK/
├── Archive/
│   ├── example.txt
│   ├── another-file.txt
│   └── nested-directory/
│       └── more-sentences.txt
├── autocomplete/
├── tests/
└── requirements.txt
```

Only `.txt` files are indexed. Subdirectories are scanned recursively.

ZIP example:

```text
ELK/
├── Archive.zip
├── autocomplete/
├── tests/
└── requirements.txt
```

The ZIP may contain an `Archive/` wrapper directory or contain the `.txt`
files directly. Both layouts are supported. The files are streamed from the
ZIP during the offline build, so the workflow is identical on Windows, Linux,
and macOS. No extraction command is needed.

The `Archive` directory, `Archive.zip`, and generated SQLite files are
intentionally excluded from Git. Every developer must receive the corpus
separately or receive a prebuilt index file.

## 4. Build the offline index

Build `autocomplete.sqlite3` from the corpus:

```console
python -m autocomplete build Archive
```

Or build directly from the ZIP:

```console
python -m autocomplete build Archive.zip
```

Successful output looks similar to:

```text
Index ready: 2397608 sentences stored in 65.24 seconds.
```

This is the offline phase. It:

1. Reads all `.txt` files recursively.
2. Cleans edge whitespace and invisible control characters.
3. Normalizes sentences for searching.
4. Groups identical original sentences.
5. Stores every file and line location separately.
6. Builds the SQLite FTS5 trigram search index.

Rebuild the index when the corpus changes. It is not necessary to rebuild it
after changes limited to the UI or ranking code.

To use a different index filename:

```console
python -m autocomplete build Archive --index my-index.sqlite3
```

## 5. Run the application

### Browser UI

```console
python -m autocomplete ui
```

Open <http://127.0.0.1:8000>. Suggestions appear below the input and refresh
after every character that is added or removed. Use the arrow keys and Enter,
or click a suggestion, to select it and inspect its corpus locations.

Stop the server with `Ctrl+C` in the terminal where it is running.

If port 8000 is already in use:

```console
python -m autocomplete ui --port 8001
```

Then open <http://127.0.0.1:8001>.

To run with a custom index:

```console
python -m autocomplete ui --index my-index.sqlite3
```

### Interactive command line

```console
python -m autocomplete serve
```

Text entered on each prompt is appended to the current query. Enter `#` to
reset the query. Press `Ctrl+C` to exit.

To use a custom index:

```console
python -m autocomplete serve --index my-index.sqlite3
```

## Ranking and duplicate grouping

Search comparisons ignore case and punctuation, but the original sentence is
preserved for display.

The assignment-compatible engine ranks by:

1. Match score.
2. Alphabetical and source-location tie-breaking.

The browser UI returns unique sentences and ranks them by:

1. Match score, highest first.
2. Number of corpus occurrences, highest first.
3. Alphabetical order.

Identical complete sentences share one result card. Their file and line
locations are loaded in pages only when the user opens that card. Sentences
that merely contain the same query remain separate results.

For example, these are different complete sentences:

```text
Table of Contents
12 Table Of Contents
Create a table of contents file.
```

However, 1,000 identical occurrences of `Table of Contents` are represented by
one result with 1,000 locations.

## API

FastAPI documentation is available while the server is running:

- Swagger UI: <http://127.0.0.1:8000/docs>
- Health check: `GET /api/health`
- Search: `POST /api/completions`
- Locations: `GET /api/completions/{sentence_id}/locations`

### Search from PowerShell

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/completions `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"query":"python documentatjon"}'
```

### Search from Linux or macOS

```bash
curl -X POST http://127.0.0.1:8000/api/completions \
  -H 'Content-Type: application/json' \
  -d '{"query":"python documentatjon"}'
```

## Run the tests

From the repository root:

```console
python -m unittest discover -s tests -v
```

The test suite covers corpus loading, normalization, index construction,
candidate retrieval, typo scoring, Top-5 ranking, duplicate grouping, CLI
behavior, bounded LRU caching, and the FastAPI endpoints.

## Architecture

```text
Offline phase
Archive/**/*.txt or Archive.zip
        │
        ▼
Corpus reader and normalization
        │
        ▼
Unique sentences + occurrence locations
        │
        ▼
SQLite FTS5 trigram index

Online phase
User input
        │
        ▼
Bounded LRU cache lookup
        │ cache miss
        ▼
Exact lookup → indexed one-edit candidates
        │
        ▼
Scoring → bounded Top-5 heap
        │
        ▼
Store result in cache
        │
        ▼
CLI / JSON API / live browser suggestions
```

The FastAPI service keeps up to 1,000 recent normalized queries in memory.
Repeated equivalent queries bypass SQLite, and the least-recently-used result
is removed when the cache is full. The index file timestamp and size are part
of each cache key, so rebuilding the index does not return stale suggestions.

Important modules:

| Module | Responsibility |
|---|---|
| `autocomplete/corpus.py` | Recursive corpus reading and source locations |
| `autocomplete/normalization.py` | Search normalization and input validation |
| `autocomplete/index.py` | SQLite index construction and candidate queries |
| `autocomplete/scoring.py` | Exact and one-character-edit scoring |
| `autocomplete/engine.py` | Ranking and Top-5 result selection |
| `autocomplete/cache.py` | Thread-safe bounded LRU cache |
| `autocomplete/cli.py` | Build, CLI, and UI commands |
| `autocomplete/web.py` | FastAPI routes and response models |
| `autocomplete/static/` | Browser interface |

## Troubleshooting

### `Corpus source must be an existing directory or ZIP archive`

Confirm that `Archive`, `Archive.zip`, or the path passed to `build` exists in
the repository root. ZIP files do not need to be extracted.

### `The search index is not ready`

Run the offline build before starting the UI:

```console
python -m autocomplete build Archive
```

### Port 8000 is already in use

Stop the existing server with `Ctrl+C`, or choose another port:

```console
python -m autocomplete ui --port 8001
```

### Unsupported input

The current corpus and query pipeline support English letters, numbers,
spaces, and punctuation. Other writing systems are rejected before SQLite is
queried.

## Generated and local-only files

The following are excluded by `.gitignore`:

```text
Archive/
Archive.zip
*.sqlite3
.venv/
__pycache__/
.pytest_cache/
```

Do not commit the corpus or generated index.
