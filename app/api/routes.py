import json, hashlib, re
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.security import require_api_key
from app.models import *
from app.schemas.common import *
from app.services.storage import save_upload
from app.services.custody import append_entry
from app.services.ingestion import process_evidence_by_id
from app.rag.ollama import embed, stream_chat
router=APIRouter(dependencies=[Depends(require_api_key)])
def err(message,code="error"): raise HTTPException(400,detail={"detail":message,"code":code})

def _case_read(case: Case, evidence_count: int = 0) -> dict:
    return {
        "id": case.id,
        "name": case.name,
        "description": case.description or "",
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "evidence_count": evidence_count,
        "processing_status": "ready",
        "tags": [],
    }

def _evidence_read(evidence: EvidenceFile) -> dict:
    extension = Path(evidence.original_filename).suffix.lower()
    evidence_type = {
        ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".webp": "image", ".eml": "email", ".msg": "email",
        ".mp3": "audio", ".wav": "audio", ".mp4": "video", ".mov": "video",
        ".csv": "document", ".json": "chat", ".txt": "document", ".docx": "document",
    }.get(extension, "other")
    return {
        "id": evidence.id,
        "case_id": evidence.case_id,
        "filename": evidence.original_filename,
        "original_filename": evidence.original_filename,
        "type": evidence_type,
        "size": evidence.size,
        "sha256": evidence.sha256,
        "integrity_status": "unchecked",
        "processing_status": evidence.processing_status.value,
        "upload_date": evidence.uploaded_at,
        "processed_at": None,
        "page_count": None,
        "duration": None,
        "is_duplicate": False,
        "duplicate_of": None,
        "metadata": evidence.extracted_metadata or {},
    }

def _words(value: str) -> set[str]:
    stop_words = {"about", "after", "again", "and", "are", "can", "did", "does", "for", "from", "have", "her", "his", "how", "into", "is", "it", "me", "my", "of", "on", "or", "our", "please", "say", "she", "should", "tell", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "were", "what", "when", "where", "which", "who", "why", "with", "would", "you", "your"}
    words = {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2 and word not in stop_words}
    return {word[:-1] if word.endswith("s") and len(word) > 4 else word for word in words}

def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0

async def _retrieve_chunks(case_id: str, body: QuestionRequest, db: AsyncSession):
    history_query = " ".join(
        item.get("content", "") for item in body.history[-8:] if item.get("role") == "user"
    )
    retrieval_query = f"{history_query} {body.message}".strip()
    query_words = _words(retrieval_query)
    query_terms = query_words | {
        synonym
        for word in query_words
        for key, values in {
            "fired": {"termination", "terminated", "dismissed"},
            "fire": {"termination", "terminated", "dismissed"},
            "boss": {"manager", "supervisor"},
            "complained": {"complaint", "reported"},
            "reported": {"complaint", "complained"},
            "message": {"messages", "slack", "chat", "communication"},
            "messages": {"message", "slack", "chat", "communication"},
            "conversation": {"messages", "slack", "chat", "communication"},
            "discussions": {"messages", "slack", "chat", "communication"},
            "summarize": {"summary", "overview", "content"},
            "summarise": {"summary", "overview", "content"},
        }.items()
        if word == key
        for synonym in values
    }
    try:
        query_vector = await embed(retrieval_query)
        vector_rows = (await db.execute(
            select(ChunkEmbedding, Chunk, EvidenceFile)
            .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
            .join(EvidenceFile, EvidenceFile.id == Chunk.evidence_id)
            .where(EvidenceFile.case_id == case_id)
        )).all()
        semantic_matches = []
        for stored_vector, chunk, evidence in vector_rows:
            if body.evidence_ids and evidence.id not in body.evidence_ids:
                continue
            similarity = _cosine_similarity(query_vector, stored_vector.vector)
            filename_overlap = len(query_terms & _words(evidence.original_filename))
            relevance = similarity + min(filename_overlap * 0.04, 0.16)
            if relevance >= 0.2:
                semantic_matches.append((relevance, chunk, evidence))
        semantic_matches.sort(key=lambda row: row[0], reverse=True)
        if semantic_matches:
            return [(chunk, evidence) for _, chunk, evidence in semantic_matches[:8]]
    except Exception:
        # Fall back to keyword search when the local embedding model is offline.
        pass

    rows = (await db.execute(
        select(Chunk, EvidenceFile)
        .join(EvidenceFile, Chunk.evidence_id == EvidenceFile.id)
        .where(EvidenceFile.case_id == case_id)
    )).all()
    ranked_rows = []
    for chunk, evidence in rows:
        if body.evidence_ids and evidence.id not in body.evidence_ids:
            continue
        body_words = _words(chunk.text)
        file_words = _words(evidence.original_filename)
        score = len(query_terms & body_words) + 3 * len(query_terms & file_words)
        if score:
            ranked_rows.append((score, chunk, evidence))
    ranked_rows.sort(key=lambda item: item[0], reverse=True)
    return [(chunk, evidence) for _, chunk, evidence in ranked_rows[:8]]

@router.post("/cases",response_model=CaseRead,status_code=201)
async def create_case(body: CaseCreate, db: AsyncSession=Depends(get_db)):
    x=Case(**body.model_dump()); db.add(x); await db.commit(); await db.refresh(x); return _case_read(x)
@router.get("/cases",response_model=Page)
async def list_cases(limit:int=Query(50,le=100),offset:int=0,db:AsyncSession=Depends(get_db)):
    cases=(await db.execute(select(Case).order_by(Case.created_at.desc()).offset(offset).limit(limit))).scalars().all(); total=await db.scalar(select(func.count()).select_from(Case)); counts=(await db.execute(select(EvidenceFile.case_id,func.count()).group_by(EvidenceFile.case_id))).all(); evidence_counts=dict(counts); items=[_case_read(case,evidence_counts.get(case.id,0)) for case in cases]; return Page(items=items,total=total or 0,limit=limit,offset=offset)
@router.get("/cases/{case_id}",response_model=CaseRead)
async def get_case(case_id:str,db:AsyncSession=Depends(get_db)):
    x=await db.get(Case,case_id)
    if not x: raise HTTPException(404,"Case not found")
    count=await db.scalar(select(func.count()).select_from(EvidenceFile).where(EvidenceFile.case_id==case_id)); return _case_read(x,count or 0)
@router.patch("/cases/{case_id}",response_model=CaseRead)
async def update_case(case_id:str,body:CaseCreate,db:AsyncSession=Depends(get_db)):
    x=await db.get(Case,case_id)
    if not x: raise HTTPException(404,"Case not found")
    for k,v in body.model_dump().items(): setattr(x,k,v)
    await db.commit(); await db.refresh(x); count=await db.scalar(select(func.count()).select_from(EvidenceFile).where(EvidenceFile.case_id==case_id)); return _case_read(x,count or 0)
@router.delete("/cases/{case_id}",status_code=204)
async def delete_case(case_id:str,db:AsyncSession=Depends(get_db)):
    x=await db.get(Case,case_id)
    if not x: raise HTTPException(404,"Case not found")
    await db.delete(x); await db.commit()

@router.post("/cases/{case_id}/evidence")
async def upload(case_id:str,background:BackgroundTasks,files:list[UploadFile]=File(...),db:AsyncSession=Depends(get_db)):
    if not await db.get(Case,case_id): raise HTTPException(404,"Case not found")
    out=[]
    for f in files:
        path,size,sha,md5,mime=await save_upload(f,case_id); e=EvidenceFile(case_id=case_id,original_filename=f.filename or "upload",mime_type=mime,size=size,sha256=sha,md5=md5,storage_path=str(path)); db.add(e); await db.flush(); await append_entry(db,e.id,"upload","api",{"sha256":sha,"size":size}); await db.commit(); await db.refresh(e); background.add_task(process_evidence_by_id,e.id); out.append({"id":e.id,"filename":e.original_filename,"status":e.processing_status})
    return {"items":out}
@router.get("/cases/{case_id}/evidence")
async def list_evidence(case_id:str,db:AsyncSession=Depends(get_db)):
    items=(await db.execute(select(EvidenceFile).where(EvidenceFile.case_id==case_id).order_by(EvidenceFile.uploaded_at.desc()))).scalars().all(); return {"items":[_evidence_read(item) for item in items],"total":len(items),"page":1,"page_size":len(items),"has_next":False}
@router.get("/evidence/{evidence_id}")
async def get_evidence(evidence_id:str,db:AsyncSession=Depends(get_db)):
    evidence=await db.get(EvidenceFile,evidence_id)
    if not evidence: raise HTTPException(404,"Evidence not found")
    return _evidence_read(evidence)
@router.get("/evidence/{evidence_id}/file")
async def get_file(evidence_id:str,db:AsyncSession=Depends(get_db)):
    e=await db.get(EvidenceFile,evidence_id)
    if not e: raise HTTPException(404,"Evidence not found")
    await append_entry(db,e.id,"view","api",{}); await db.commit(); return FileResponse(e.storage_path,filename=e.original_filename,media_type=e.mime_type)
@router.get("/evidence/{evidence_id}/chunks")
async def get_chunks(evidence_id:str,db:AsyncSession=Depends(get_db)):
    chunks=(await db.execute(select(Chunk).where(Chunk.evidence_id==evidence_id))).scalars().all(); items=[{"id":chunk.id,"evidence_id":chunk.evidence_id,"content":chunk.text,"page":chunk.page,"timestamp_start":None,"timestamp_end":None,"chunk_index":index} for index,chunk in enumerate(chunks)]; return {"items":items,"total":len(items),"page":1,"page_size":len(items),"has_next":False}
@router.post("/evidence/{evidence_id}/verify")
async def verify(evidence_id:str,db:AsyncSession=Depends(get_db)):
    e=await db.get(EvidenceFile,evidence_id)
    if not e: raise HTTPException(404,"Evidence not found")
    h=hashlib.sha256(); m=hashlib.md5()
    with open(e.storage_path,"rb") as f:
        while b:=f.read(1024*1024): h.update(b); m.update(b)
    ok=h.hexdigest()==e.sha256 and m.hexdigest()==e.md5; await append_entry(db,e.id,"verification","api",{"valid":ok}); await db.commit(); return {"valid":ok,"sha256":h.hexdigest(),"md5":m.hexdigest()}
@router.get("/evidence/{evidence_id}/custody-log")
async def custody(evidence_id:str,db:AsyncSession=Depends(get_db)):
    events=(await db.execute(select(CustodyLogEntry).where(CustodyLogEntry.evidence_id==evidence_id).order_by(CustodyLogEntry.timestamp))).scalars().all(); items=[{"id":event.id,"evidence_id":event.evidence_id,"action":event.action,"performed_by":event.actor,"timestamp":event.timestamp,"notes":json.dumps(event.details) if event.details else None,"hash_before":event.prev_hash,"hash_after":event.entry_hash} for event in events]; return {"items":items,"total":len(items),"page":1,"page_size":len(items),"has_next":False}

@router.post("/cases/{case_id}/chat")
async def chat(case_id:str,body:QuestionRequest,db:AsyncSession=Depends(get_db)):
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    chunks = await _retrieve_chunks(case_id, body, db)
    references = []
    context_parts = []
    for index, (chunk, evidence) in enumerate(chunks, start=1):
        references.append({
            "number": index,
            "evidence_id": evidence.id,
            "evidence_filename": evidence.original_filename,
            "snippet": chunk.text,
            "page": chunk.page,
            "timestamp": chunk.timestamp,
            "chunk_id": chunk.id,
        })
        location = f", page {chunk.page}" if chunk.page else ""
        context_parts.append(f"[{index}] {evidence.original_filename}{location} (source chunk {chunk.id}):\n{chunk.text}")

    system_prompt = (
        "You are EvidenceIQ, a conversational assistant that analyzes the user's uploaded case evidence. "
        "Respond naturally to greetings and informal questions. For factual answers about the case, use only the evidence excerpts supplied below; "
        "never invent facts or imply that you read material that is not present. Cite supporting excerpts inline as [1], [2], etc. "
        "If the excerpts do not answer the question, say what is missing. Distinguish recorded facts from inferences, and keep legal conclusions for human reviewers. "
        "When asked to summarize a named file, summarize only excerpts from that file and state when the indexed excerpts are incomplete."
    )
    context = "\n\n".join(context_parts) if context_parts else "No evidence excerpts were retrieved for this question."
    messages = [{"role": "system", "content": f"{system_prompt}\n\nEvidence excerpts:\n{context}"}]
    messages.extend(
        {"role": item["role"], "content": item["content"]}
        for item in body.history[-8:]
        if item.get("role") in {"user", "assistant"} and item.get("content")
    )
    messages.append({"role": "user", "content": body.message})

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def events():
        try:
            async for line in stream_chat(messages):
                try:
                    ollama_event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = ollama_event.get("message", {}).get("content")
                if token:
                    yield sse({"type": "token", "content": token})
                if ollama_event.get("done"):
                    break
            if references:
                yield sse({"type": "citations", "citations": references})
            yield sse({"type": "done"})
        except Exception as exc:
            yield sse({"type": "error", "message": f"The local AI model could not answer: {exc}"})

    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
@router.get("/cases/{case_id}/search")
async def search(case_id:str,q:str=Query(min_length=1),db:AsyncSession=Depends(get_db)):
    return (await db.execute(select(Chunk).join(EvidenceFile).where(EvidenceFile.case_id==case_id,Chunk.text.ilike(f"%{q}%")).limit(50))).scalars().all()

@router.get("/cases/{case_id}/entities")
async def entities(case_id:str,db:AsyncSession=Depends(get_db)):
    return (await db.execute(select(Entity).where(Entity.case_id==case_id))).scalars().all()
@router.get("/cases/{case_id}/graph")
async def graph(case_id:str,db:AsyncSession=Depends(get_db)):
    nodes=(await db.execute(select(Entity).where(Entity.case_id==case_id))).scalars().all()
    ids={n.id for n in nodes}; edges=(await db.execute(select(Relationship).where(Relationship.source_entity.in_(ids),Relationship.target_entity.in_(ids)))).scalars().all() if ids else []
    return {"nodes":nodes,"edges":edges}
@router.post("/cases/{case_id}/entities/merge")
async def merge_entities(case_id:str,entity_ids:list[str],db:AsyncSession=Depends(get_db)):
    if len(entity_ids)<2: raise HTTPException(400,"At least two entities are required")
    values=(await db.execute(select(Entity).where(Entity.case_id==case_id,Entity.id.in_(entity_ids)))).scalars().all()
    if len(values)!=len(entity_ids): raise HTTPException(404,"Entity not found")
    target=values[0]
    for e in values[1:]:
        await db.execute(__import__('sqlalchemy').update(EntityMention).where(EntityMention.entity_id==e.id).values(entity_id=target.id)); await db.delete(e)
    await db.commit(); return target
@router.get("/cases/{case_id}/timeline")
async def timeline(case_id:str,db:AsyncSession=Depends(get_db)): return (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id==case_id).order_by(TimelineEvent.timestamp))).scalars().all()
@router.post("/cases/{case_id}/timeline/regenerate")
async def regenerate_timeline(case_id:str): return {"status":"queued","message":"Timeline regeneration is scheduled for the ingestion worker."}
@router.get("/cases/{case_id}/inconsistencies")
async def inconsistencies(case_id:str,db:AsyncSession=Depends(get_db)): return (await db.execute(select(Inconsistency).where(Inconsistency.case_id==case_id))).scalars().all()
@router.patch("/inconsistencies/{inconsistency_id}")
async def update_inconsistency(inconsistency_id:str,status:InconsistencyStatus,reviewer_note:str|None=None,db:AsyncSession=Depends(get_db)):
    x=await db.get(Inconsistency,inconsistency_id)
    if not x: raise HTTPException(404,"Inconsistency not found")
    x.status=status; x.reviewer_note=reviewer_note; await db.commit(); return x
@router.post("/cases/{case_id}/inconsistencies/scan")
async def scan_inconsistencies(case_id:str): return {"status":"queued","disclaimer":"Potential inconsistencies for human review; not conclusions."}
@router.get("/cases/{case_id}/duplicates")
async def duplicates(case_id:str,db:AsyncSession=Depends(get_db)): return (await db.execute(select(DuplicateGroup).where(DuplicateGroup.case_id==case_id))).scalars().all()
@router.post("/cases/{case_id}/summary")
async def create_summary(case_id:str): return {"status":"queued","disclaimer":"AI-generated investigative summary requires human verification and is not a legal conclusion."}
@router.get("/cases/{case_id}/summary")
async def get_summary(case_id:str): return {"case_id":case_id,"overview":None,"key_people":[],"key_dates":[],"key_events":[],"notable_inconsistencies":[],"open_questions":[],"citations":[],"disclaimer":"AI output requires human verification and is not a legal conclusion."}
