"""FastAPI application serving the autocomplete web interface."""

from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import DEFAULT_INDEX_PATH, get_best_unique_completions
from .index import get_sentence_locations
from .normalization import is_supported_normalized_query, normalize_text


_STATIC_DIRECTORY = Path(__file__).with_name("static")


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


def create_app(index_path: str | Path = DEFAULT_INDEX_PATH) -> FastAPI:
    """Create an application bound to one on-disk search index."""

    app = FastAPI(
        title="Sentence Autocomplete",
        description="Five ranked sentence completions from an indexed corpus.",
        version="1.0.0",
    )
    app.state.index_path = Path(index_path)
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
        normalized_query = normalize_text(request.query)
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
        results = get_best_unique_completions(normalized_query, app.state.index_path)
        elapsed_ms = (perf_counter() - started_at) * 1_000

        return CompletionResponse(
            query=request.query,
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
