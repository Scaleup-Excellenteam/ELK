"""Command-line interface for building and using the autocomplete engine."""

import argparse
from pathlib import Path
from time import perf_counter
from typing import Callable

from .cache import DEFAULT_CACHE_CAPACITY, LruCache
from .engine import DEFAULT_INDEX_PATH, get_best_k_completions
from .index import build_index
from .models import AutoCompleteData
from .normalization import is_supported_normalized_query, normalize_text


_WELCOME_TITLE = "Sentence Autocomplete\n---------------------"
_WELCOME_HELP = "Type text to search | # reset | Ctrl+C exit"
_INITIAL_PROMPT = "The system is ready. Enter your text:\n"

CliCacheKey = tuple[str, int, int]
CliCacheValue = tuple[AutoCompleteData, ...]


def _get_cached_cli_completions(
    query: str,
    index_path: str | Path,
    cache: LruCache[CliCacheKey, CliCacheValue],
) -> CliCacheValue:
    """Return CLI completions, caching supported queries by index version."""

    normalized_query = normalize_text(query)
    if not normalized_query or not is_supported_normalized_query(normalized_query):
        return tuple(get_best_k_completions(query, index_path))

    try:
        index_stat = Path(index_path).stat()
    except OSError:
        return tuple(get_best_k_completions(query, index_path))

    cache_key = (
        normalized_query,
        index_stat.st_mtime_ns,
        index_stat.st_size,
    )
    cache_hit, cached_results = cache.get(cache_key)
    if cache_hit:
        return cached_results if cached_results is not None else ()

    results = tuple(get_best_k_completions(query, index_path))
    cache.put(cache_key, results)
    return results


def run_interactive(
    index_path: str | Path = DEFAULT_INDEX_PATH,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    cache_capacity: int = DEFAULT_CACHE_CAPACITY,
    pretty: bool | None = None,
) -> None:
    """Read text fragments and display completions until input ends."""

    current_text = ""
    completion_cache = LruCache[CliCacheKey, CliCacheValue](cache_capacity)
    if pretty is None:
        pretty = input_fn is input and output_fn is print
    if pretty:
        output_fn(_WELCOME_TITLE)
        output_fn(_WELCOME_HELP)

    while True:
        if pretty:
            prompt = f"\nContinue [{current_text}] > " if current_text else "\nSearch > "
        else:
            prompt = current_text if current_text else _INITIAL_PROMPT

        try:
            typed_text = input_fn(prompt)
        except (EOFError, KeyboardInterrupt):
            output_fn("\nGoodbye.")
            return

        if "#" in typed_text:
            current_text = ""
            if pretty:
                output_fn("\nQuery reset.")
            continue

        current_text += typed_text
        results = _get_cached_cli_completions(
            current_text,
            index_path,
            completion_cache,
        )

        if pretty:
            if not results:
                output_fn("\nNo suggestions found.")
                continue

            output_fn(f"\nSuggestions ({len(results)})")
            output_fn("----------------")
            for position, result in enumerate(results, start=1):
                output_fn(f"{position}) {result.completed_sentence}")
                output_fn(
                    f"   Source: {result.source_text} | "
                    f"line {result.offset} | score {result.score}"
                )
        else:
            output_fn(f"Here are {len(results)} suggestions:")
            for position, result in enumerate(results, start=1):
                output_fn(
                    f"{position}. {result.completed_sentence} "
                    f"({result.source_text}:{result.offset}, score={result.score})"
                )


def _build_command(source_root: str, index_path: str) -> None:
    start = perf_counter()
    stored_sentences = build_index(source_root, index_path)
    elapsed = perf_counter() - start
    print(
        f"Index ready: {stored_sentences} sentences stored in "
        f"{elapsed:.2f} seconds."
    )


def _ui_command(index_path: str, host: str, port: int) -> None:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(index_path), host=host, port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentence autocomplete")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="build the offline index")
    build_parser.add_argument(
        "source_root",
        help="directory or ZIP archive containing corpus text files",
    )
    build_parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="SQLite index path",
    )

    serve_parser = subparsers.add_parser("serve", help="start interactive autocomplete")
    serve_parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="SQLite index path",
    )

    ui_parser = subparsers.add_parser("ui", help="start the FastAPI web interface")
    ui_parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="SQLite index path",
    )
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host address for the web interface",
    )
    ui_parser.add_argument(
        "--port",
        default=8000,
        type=int,
        help="port for the web interface",
    )

    arguments = parser.parse_args()

    if arguments.command == "build":
        _build_command(arguments.source_root, arguments.index)
    elif arguments.command == "serve":
        run_interactive(arguments.index)
    else:
        _ui_command(arguments.index, arguments.host, arguments.port)
