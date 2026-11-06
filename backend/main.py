import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from routers.circle_search import router as circle_search_router


def allowed_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "")
    if configured.strip():
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


app = FastAPI(title="Circle to Search 테스트 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(circle_search_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


frontend_dist = (Path(__file__).resolve().parent / "dist").resolve()


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str):
    if not frontend_dist.is_dir() or full_path == "api" or full_path.startswith("api/") or full_path.startswith("health/"):
        raise HTTPException(status_code=404)
    asset = (frontend_dist / full_path).resolve()
    if asset.is_relative_to(frontend_dist) and asset.is_file():
        return FileResponse(asset)
    if Path(full_path).suffix:
        raise HTTPException(status_code=404)
    return FileResponse(frontend_dist / "index.html")
