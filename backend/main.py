import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
