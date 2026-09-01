"""FastAPI application serving the autocomplete web interface."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import ai_health
from .cache import DEFAULT_CACHE_CAPACITY, LruCache
from .engine import DEFAULT_INDEX_PATH, get_best_unique_completions
from .index import get_index_stats, get_sentence_group, get_sentence_locations
from .logging_setup import (
    get_activity_summary,
    get_log_file_size,
    get_recent_entries,
    log_event,
)
from .models import GroupedAutoCompleteData
from .normalization import is_supported_normalized_query, normalize_text
from .proto import completions_pb2


_STATIC_DIRECTORY = Path(__file__).with_name("static")
_START_TIME = datetime.now(timezone.utc)
CompletionCacheKey = tuple[str, int, int]
CompletionCacheValue = tuple[GroupedAutoCompleteData, ...]

# The admin dashboard polls these endpoints every couple of seconds; keeping
# them out of the uvicorn access log avoids drowning out real activity when
# the dashboard is left open (e.g. during a live demo).
_POLLED_PATHS = ("/api/admin/stats", "/api/admin/logs")


class _SkipPolledEndpoints(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in _POLLED_PATHS)


logging.getLogger("uvicorn.access").addFilter(_SkipPolledEndpoints())


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


class CompletionSelectionRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    sentence_id: int = Field(gt=0)
    rank: int = Field(ge=1, le=5)
    # Keep this optional for browser tabs that still run an older app.js.
    elapsed_ms: float = Field(default=0.0, ge=0)


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


class AdminStatsResponse(BaseModel):
    index_ready: bool
    index_path: str
    index_size_bytes: int
    sentence_count: int
    source_count: int
    location_count: int
    log_size_bytes: int
    server_started_at: str
    search_count: int
    average_latency_ms: float
    p95_latency_ms: float
    cache_hits: int
    cache_hit_rate: float
    selected_completions: int
    characters_saved: int
    slow_searches: int
    error_count: int
    latency_samples: list[float]


class AdminLogEntry(BaseModel):
    timestamp: str
    event: str
    details: dict[str, object]


class AdminLogsResponse(BaseModel):
    entries: list[AdminLogEntry]


class AdminMissionBriefingResponse(BaseModel):
    available: bool
    summary: str
    generated_at: str


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

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(_STATIC_DIRECTORY / "admin.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "index_ready": app.state.index_path.is_file(),
        }

    def resolve_completions(
        raw_query: str,
        client_host: str | None,
    ) -> tuple[str, tuple[GroupedAutoCompleteData, ...], float]:
        """Run one completion lookup, sharing cache/log behaviour across endpoints."""

        normalized_query = normalize_text(raw_query)
        if not normalized_query:
            log_event(
                "completion_rejected",
                reason="empty_query",
                query=raw_query,
                client=client_host,
            )
            raise HTTPException(
                status_code=422,
                detail="Enter text containing searchable characters.",
            )

        if not is_supported_normalized_query(normalized_query):
            log_event(
                "completion_rejected",
                reason="unsupported_characters",
                query=raw_query,
                client=client_host,
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "Only English letters, numbers, spaces and punctuation "
                    "are supported."
                ),
            )

        if not app.state.index_path.is_file():
            log_event(
                "completion_rejected",
                reason="index_not_ready",
                query=raw_query,
                client=client_host,
            )
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
            try:
                results = tuple(
                    get_best_unique_completions(
                        normalized_query,
                        app.state.index_path,
                    )
                )
            except Exception as error:
                log_event(
                    "completion_error",
                    query=raw_query,
                    normalized_query=normalized_query,
                    client=client_host,
                    error=str(error),
                )
                raise
            app.state.completion_cache.put(cache_key, results)
        elapsed_ms = (perf_counter() - started_at) * 1_000

        log_event(
            "completion",
            query=raw_query,
            normalized_query=normalized_query,
            elapsed_ms=round(elapsed_ms, 2),
            suggestion_count=len(results),
            cache_hit=cache_hit,
            client=client_host,
        )

        return normalized_query, results, round(elapsed_ms, 2)

    @app.post("/api/completions", response_model=CompletionResponse)
    def completions(request: CompletionRequest, http_request: Request) -> CompletionResponse:
        client_host = http_request.client.host if http_request.client else None
        normalized_query, results, elapsed_ms = resolve_completions(
            request.query, client_host
        )

        return CompletionResponse(
            query=request.query,
            normalized_query=normalized_query,
            elapsed_ms=elapsed_ms,
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

    @app.post(
        "/api/completions/selection",
        status_code=204,
        response_class=Response,
    )
    def record_completion_selection(
        selection: CompletionSelectionRequest,
        http_request: Request,
    ) -> Response:
        """Record one suggestion the user explicitly accepted."""

        if not app.state.index_path.is_file():
            raise HTTPException(
                status_code=503,
                detail="The search index is not ready.",
            )

        sentence_group = get_sentence_group(
            app.state.index_path,
            selection.sentence_id,
        )
        if sentence_group is None:
            raise HTTPException(status_code=404, detail="Sentence was not found.")

        completed_sentence, occurrence_count = sentence_group
        client_host = http_request.client.host if http_request.client else None
        log_event(
            "completion_selected",
            query=selection.query,
            sentence_id=selection.sentence_id,
            completed_sentence=completed_sentence,
            rank=selection.rank,
            search_elapsed_ms=round(selection.elapsed_ms, 2),
            characters_saved=max(0, len(completed_sentence) - len(selection.query)),
            occurrence_count=occurrence_count,
            client=client_host,
        )
        return Response(status_code=204)

    @app.websocket("/ws/completions")
    async def websocket_completions(websocket: WebSocket) -> None:
        await websocket.accept()
        current_query = ""
        client_host = websocket.client.host if websocket.client else None

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

                normalized_query, results, elapsed_ms = await run_in_threadpool(
                    resolve_completions,
                    current_query,
                    client_host,
                )
                response = CompletionResponse(
                    query=current_query,
                    normalized_query=normalized_query,
                    elapsed_ms=elapsed_ms,
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

    @app.post(
        "/api/completions/binary",
        include_in_schema=False,
        response_class=Response,
    )
    async def completions_binary(http_request: Request) -> Response:
        """Same lookup as /api/completions, wire-encoded as Protobuf.

        Every byte sent over a constrained uplink (e.g. to a satellite)
        costs time and power, so this trades the JSON endpoint's
        human-readable envelope for a compact binary one.
        """

        client_host = http_request.client.host if http_request.client else None
        body = await http_request.body()
        request_proto = completions_pb2.CompletionRequestProto()
        try:
            request_proto.ParseFromString(body)
        except Exception as error:
            raise HTTPException(
                status_code=400,
                detail=f"Malformed Protobuf request body: {error}",
            ) from error

        normalized_query, results, elapsed_ms = resolve_completions(
            request_proto.query, client_host
        )

        response_proto = completions_pb2.CompletionResponseProto(
            query=request_proto.query,
            normalized_query=normalized_query,
            elapsed_ms=elapsed_ms,
            suggestions=[
                completions_pb2.CompletionItemProto(
                    sentence_id=result.sentence_id,
                    completed_sentence=result.completed_sentence,
                    score=result.score,
                    occurrence_count=result.occurrence_count,
                )
                for result in results
            ],
        )
        return Response(
            content=response_proto.SerializeToString(),
            media_type="application/x-protobuf",
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

    @app.get("/api/admin/stats", response_model=AdminStatsResponse)
    def admin_stats() -> AdminStatsResponse:
        index_path = app.state.index_path
        index_ready = index_path.is_file()
        stats = (
            get_index_stats(index_path)
            if index_ready
            else {"sentence_count": 0, "source_count": 0, "location_count": 0}
        )
        activity = get_activity_summary()
        return AdminStatsResponse(
            index_ready=index_ready,
            index_path=str(index_path),
            index_size_bytes=index_path.stat().st_size if index_ready else 0,
            sentence_count=stats["sentence_count"],
            source_count=stats["source_count"],
            location_count=stats["location_count"],
            log_size_bytes=get_log_file_size(),
            server_started_at=_START_TIME.isoformat(timespec="seconds"),
            **activity,
        )

    @app.get("/api/admin/logs", response_model=AdminLogsResponse)
    def admin_logs(
        limit: int = Query(default=50, ge=1, le=500),
        event: str | None = Query(default=None),
    ) -> AdminLogsResponse:
        entries = get_recent_entries(limit=limit, event=event)
        return AdminLogsResponse(
            entries=[
                AdminLogEntry(
                    timestamp=entry["timestamp"],
                    event=entry["event"],
                    details={
                        key: value
                        for key, value in entry.items()
                        if key not in {"timestamp", "event"}
                    },
                )
                for entry in entries
            ]
        )

    @app.post(
        "/api/admin/mission-briefing",
        response_model=AdminMissionBriefingResponse,
    )
    def admin_mission_briefing() -> AdminMissionBriefingResponse:
        metrics = get_activity_summary()
        result = ai_health.generate_mission_briefing(metrics)
        return AdminMissionBriefingResponse(
            available=result.available,
            summary=result.summary,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    @app.post(
        "/api/admin/health-check",
        response_model=AdminMissionBriefingResponse,
        include_in_schema=False,
    )
    def legacy_admin_health_check() -> AdminMissionBriefingResponse:
        """Keep older clients working while the dashboard uses mission briefing."""

        return admin_mission_briefing()

    return app


app = create_app()
