# EvidenceIQ

EvidenceIQ is a local-first evidence intelligence API. Files remain in immutable, read-only storage; derived text, embeddings, and AI outputs are stored separately. AI output is investigative assistance only, requires human verification, and is not a legal conclusion.

## Run locally

1. Install Python 3.11+, Tesseract OCR, and ffmpeg.
2. Copy `.env.example` to `.env` and install: `pip install -e .[test]`.
3. Start Ollama, then run `ollama pull llama3.1:8b` and `ollama pull nomic-embed-text`.
4. Start the API: `uvicorn app.main:app --reload`.
5. Open `/docs` for the OpenAPI contract. Set `API_KEY` to enable API-key middleware.

## Docker

`docker compose up --build`, then pull the models in the Ollama container with `docker compose exec ollama ollama pull llama3.1:8b` and `docker compose exec ollama ollama pull nomic-embed-text`.

## Layout

`app/models` contains the case, immutable evidence, chunk, entity, graph, timeline, inconsistency, duplicate, chat, and custody schema. `app/parsers` and `app/ingestion` are swappable pipeline components. `app/rag` owns local Ollama calls.

## API coverage

Implemented initial case CRUD, streamed multi-file upload with SHA-256/MD5, append-only custody events, file verification, chunk retrieval, local ingestion, SSE chat, keyword search, health, CORS, and OpenAPI. Remaining staged work includes Chroma retrieval, entity/timeline extraction, inconsistency and duplicate analysis, summary exports, and full migrations.
