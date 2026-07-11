from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routers.api import router as api_router
from app.services.ai_company_monitor import get_project_root, get_results_root
from app.services.session_store import init_session_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    init_session_store()
    yield


app = FastAPI(
    title="Agent OS MVP",
    version="0.1.0",
    description="Simplified internal Agent OS with Goals, Task Wall, fixed Agents, Reviews, and Audit Logs.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return checkout_marker()


app.include_router(api_router)


def checkout_marker():
    dashboard_root = Path(__file__).resolve().parents[2]
    project_root = get_project_root()
    return {
        "status": "ok",
        "app": "agent_os_mvp",
        "app_version": app.version,
        "app_root": str(dashboard_root),
        "project_root": str(project_root),
        "result_root": str(get_results_root()),
    }
