import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.redis_client import get_redis
from app.routes import extract, jobs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_redis().ping()
    yield
    get_redis().close()


app = FastAPI(title="PDF extraction API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(extract.router)
app.include_router(jobs.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "pdf-extract-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": str(exc) or "Unexpected server error"})
