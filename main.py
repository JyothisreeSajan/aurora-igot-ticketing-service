"""
main.py
-------
FastAPI application entry point for the iGOT Karmayogi Aurora Agent.

Registers all API routers and starts the uvicorn server.
Run with: uvicorn main:app --host 0.0.0.0 --port 4020 --reload
"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

from app.api.health.router import router as health_router
from app.core.graph.graph_router import router as ticket_router


# ── Startup / shutdown lifecycle ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler.

    Startup checks (executed before the first request is accepted):
      1. PII masking guard — ensures Presidio is installed and the masker
         singleton is ready.  If not, the service refuses to start rather than
         silently processing tickets without masking.

    Any RuntimeError raised here aborts uvicorn startup with a non-zero exit
    code, making the failure immediately visible in logs and process monitors.
    """
    # ── PII masking guard ─────────────────────────────────────────────────────
    try:
        from app.core.utils.helpers import _PIIMasker
        _PIIMasker.get().assert_ready()
        logger.info("[startup] PII masking guard: Presidio ready ✓")
    except RuntimeError as exc:
        logger.critical(f"[startup] PII masking guard FAILED — aborting startup: {exc}")
        raise  # abort server boot

    yield  # application is running

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("[shutdown] Aurora Agent shutting down.")


app = FastAPI(
    title="Aurora Agent API",
    description="iGOT Karmayogi agentic ticket resolution service powered by LangGraph and Google Gemini.",
    version="1.0.0",
    lifespan=lifespan,
)


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router, prefix="/api/v1/health")
app.include_router(ticket_router)


@app.get("/")
async def root():
    return {"message": "AuroraX Agent API is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=4020)
