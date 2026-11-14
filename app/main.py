from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.db import engine, Base
from app.rag.ollama import ollama_health
from app.core.config import settings
from pathlib import Path
app=FastAPI(title="EvidenceIQ API",version="0.1.0",description="Local-first evidence intelligence; AI output requires human verification and is not a legal conclusion.")
app.add_middleware(CORSMiddleware,allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
app.include_router(router,prefix="/api/v1")
@app.on_event("startup")
async def startup():
    Path(settings.storage_root).mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite") and "///" in settings.database_url:
        database_path = Path(settings.database_url.split("///", 1)[1])
        if str(database_path.parent) != ".":
            database_path.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
@app.get("/api/v1/health")
async def health(): return {"status":"ok","ollama":await ollama_health(),"disclaimer":"AI output requires human verification and is not a legal conclusion."}
