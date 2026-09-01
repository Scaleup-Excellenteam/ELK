"""FastAPI application serving the autocomplete web interface."""

from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .cache import DEFAULT_CACHE_CAPACITY, LruCache
from .engine import DEFAULT_INDEX_PATH, get_best_unique_completions
from .index import get_sentence_locations
from .models import GroupedAutoCompleteData
from .normalization import is_supported_normalized_query, normalize_text


_STATIC_DIRECTORY = Path(__file__).with_name("static")
CompletionCacheKey = tuple[str, int, int]
CompletionCacheValue = tuple[GroupedAutoCompleteData, ...]


class CompletionRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class CompletionItem(BaseModel):
    sentence_id: int
    completed_sentence: str
    score: int
    occurrence_count: int


class CompletionResponse(BaseModel):
    query: str
    normalized_query: str
    elapsed_ms: float
    suggestions: list[CompletionItem]


class LocationItem(BaseModel):
    source_text: str
    offset: int


class LocationResponse(BaseModel):
    locations: list[LocationItem]
    next_offset: int | None


def _apply_query_message(current_query: str, message: object) -> str:
    """Apply one WebSocket set/edit message to a connection's query state."""

    if not isinstance(message, dict):
        raise ValueError("A WebSocket message must be a JSON object.")

    message_type = message.get("type")
    if message_type == "set":
        query = message.get("query")
        if not isinstance(query, str):
            raise ValueError("A set message requires a string query.")
        updated_query = query
    elif message_type == "edit":
        keep = message.get("keep")
        delete = message.get("delete")
        inserted_text = message.get("insert")
        if type(keep) is not int or keep < 0:
            raise ValueError("An edit message requires a non-negative keep value.")
        if type(delete) is not int or delete < 0:
            raise ValueError("An edit message requires a non-negative delete value.")
        if not isinstance(inserted_text, str):
            raise ValueError("An edit message requires string insert text.")
        if keep > len(current_query) or keep + delete > len(current_query):
            raise ValueError("The edit does not match the current query state.")
        updated_query = (
            current_query[:keep]
            + inserted_text
            + current_query[keep + delete :]
        )
    else:
        raise ValueError("Message type must be either set or edit.")

    if len(updated_query) > 500:
        raise ValueError("The query cannot contain more than 500 characters.")
    return updated_query


def _completion_response(app: FastAPI, query: str) -> CompletionResponse:
    """Search once through the shared validation, cache, and ranking path."""

    normalized_query = normalize_text(query)
    if not normalized_query:
        raise HTTPException(
            status_code=422,
            detail="Enter text containing searchable characters.",
        )

    if not is_supported_normalized_query(normalized_query):
        raise HTTPException(
            status_code=422,
            detail=(
                "Only English letters, numbers, spaces and punctuation "
                "are supported."
            ),
        )

    if not app.state.index_path.is_file():
        raise HTTPException(
            status_code=503,
            detail="The search index is not ready. Build it before searching.",
        )

    started_at = perf_counter()
    index_stat = app.state.index_path.stat()
    cache_key = (
        normalized_query,
        index_stat.st_mtime_ns,
        index_stat.st_size,
    )
    cache_hit, cached_results = app.state.completion_cache.get(cache_key)
    if cache_hit:
        results = cached_results or ()
    else:
        results = tuple(
            get_best_unique_completions(
                normalized_query,
                app.state.index_path,
            )
        )
        app.state.completion_cache.put(cache_key, results)
    elapsed_ms = (perf_counter() - started_at) * 1_000

    return CompletionResponse(
        query=query,
        normalized_query=normalized_query,
        elapsed_ms=round(elapsed_ms, 2),
        suggestions=[
            CompletionItem(
                sentence_id=result.sentence_id,
                completed_sentence=result.completed_sentence,
                score=result.score,
                occurrence_count=result.occurrence_count,
            )
            for result in results
        ],
    )


def create_app(
    index_path: str | Path = DEFAULT_INDEX_PATH,
    cache_capacity: int = DEFAULT_CACHE_CAPACITY,
) -> FastAPI:
    """Create an application bound to one on-disk search index."""

    app = FastAPI(
        title="Sentence Autocomplete",
        description="Five ranked sentence completions from an indexed corpus.",
        version="1.0.0",
    )
    app.state.index_path = Path(index_path)
    app.state.completion_cache = LruCache[
        CompletionCacheKey,
        CompletionCacheValue,
    ](cache_capacity)
    app.mount("/static", StaticFiles(directory=_STATIC_DIRECTORY), name="static")

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(_STATIC_DIRECTORY / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "index_ready": app.state.index_path.is_file(),
        }

    @app.post("/api/completions", response_model=CompletionResponse)
    def completions(request: CompletionRequest) -> CompletionResponse:
        return _completion_response(app, request.query)

    @app.websocket("/ws/completions")
    async def websocket_completions(websocket: WebSocket) -> None:
        await websocket.accept()
        current_query = ""

        while True:
            try:
                message = await websocket.receive_json()
                current_query = _apply_query_message(current_query, message)

                if not current_query:
                    await websocket.send_json(
                        {
                            "type": "suggestions",
                            "query": "",
                            "normalized_query": "",
                            "elapsed_ms": 0.0,
                            "suggestions": [],
                        }
                    )
                    continue

                response = await run_in_threadpool(
                    _completion_response,
                    app,
                    current_query,
                )
                await websocket.send_json(
                    {"type": "suggestions", **response.model_dump()}
                )
            except WebSocketDisconnect:
                break
            except HTTPException as error:
                await websocket.send_json(
                    {
                        "type": "error",
                        "query": current_query,
                        "status": error.status_code,
                        "detail": str(error.detail),
                    }
                )
            except ValueError as error:
                await websocket.send_json(
                    {
                        "type": "error",
                        "query": current_query,
                        "status": 422,
                        "detail": str(error),
                    }
                )

    @app.get(
        "/api/completions/{sentence_id}/locations",
        response_model=LocationResponse,
    )
    def completion_locations(
        sentence_id: int,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> LocationResponse:
        if not app.state.index_path.is_file():
            raise HTTPException(status_code=503, detail="The search index is not ready.")

        rows = get_sentence_locations(
            app.state.index_path,
            sentence_id,
            limit=limit + 1,
            offset=offset,
        )
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        return LocationResponse(
            locations=[
                LocationItem(source_text=source_text, offset=line_offset)
                for source_text, line_offset in visible_rows
            ],
            next_offset=offset + len(visible_rows) if has_more else None,
        )

    return app


app = create_app()
