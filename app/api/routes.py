import json, hashlib, re
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_db
from app.core.config import settings
from app.core.security import require_api_key
from app.models import *
from app.schemas.common import *
from app.services.storage import save_upload
from app.services.custody import append_entry
from app.services.ingestion import process_evidence_by_id
from app.rag.ollama import complete_chat, embed, stream_chat
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

def _evidence_read(evidence: EvidenceFile, integrity_status: str = "unchecked") -> dict:
    extension = Path(evidence.original_filename).suffix.lower()
    evidence_type = {
        ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".webp": "image", ".eml": "email", ".msg": "email",
        ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".aac": "audio",
        ".ogg": "audio", ".oga": "audio", ".flac": "audio",
        ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
        ".webm": "video", ".mpeg": "video", ".mpg": "video", ".3gp": "video",
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
        "integrity_status": integrity_status,
        "processing_status": evidence.processing_status.value,
        "upload_date": evidence.uploaded_at,
        "processed_at": None,
        "page_count": None,
        "duration": None,
        "is_duplicate": False,
        "duplicate_of": None,
        "metadata": evidence.extracted_metadata or {},
    }

async def _evidence_integrity_statuses(db: AsyncSession, evidence_ids: list[str]) -> dict[str, str]:
    if not evidence_ids:
        return {}
    events = (await db.execute(
        select(CustodyLogEntry)
        .where(
            CustodyLogEntry.evidence_id.in_(evidence_ids),
            CustodyLogEntry.action == "verification",
        )
        .order_by(CustodyLogEntry.timestamp.desc())
    )).scalars().all()
    statuses = {}
    for event in events:
        if event.evidence_id not in statuses:
            statuses[event.evidence_id] = (
                "verified" if (event.details or {}).get("valid") is True else "mismatch"
            )
    return statuses

def _words(value: str) -> set[str]:
    stop_words = {"about", "after", "again", "and", "are", "can", "did", "does", "for", "from", "have", "her", "his", "how", "into", "is", "it", "me", "my", "of", "on", "or", "our", "please", "say", "she", "should", "tell", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "were", "what", "when", "where", "which", "who", "why", "with", "would", "you", "your"}
    words = {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2 and word not in stop_words}
    return {word[:-1] if word.endswith("s") and len(word) > 4 else word for word in words}

def _evidence_next_steps(rows) -> list[str]:
    evidence_text=" ".join(f"{evidence.original_filename} {chunk.text}" for chunk,evidence in rows).lower()
    steps=[]
    if all(term in evidence_text for term in ("purchase order","invoice")) and any(term in evidence_text for term in ("receipt","physical count","warehouse")):
        steps.append("Reconcile the purchase order, vendor invoice, and goods receipt; document the quantity and amount differences shown in those records.")
    if "bank change" in evidence_text or "account change" in evidence_text:
        steps.append("Verify the bank account change using a previously verified vendor contact, then compare it with the payment ledger.")
    if "payment" in evidence_text and any(term in evidence_text for term in ("email","chat","approval")):
        steps.append("Compare the approval messages with the payment record and preserve the original message exports for review.")
    if not steps and rows:
        steps.append("Identify and obtain the source records needed to verify the dates, amounts, and parties referenced in the uploaded exhibits.")
    return steps

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
    items=(await db.execute(select(EvidenceFile).where(EvidenceFile.case_id==case_id).order_by(EvidenceFile.uploaded_at.desc()))).scalars().all()
    statuses=await _evidence_integrity_statuses(db,[item.id for item in items])
    return {"items":[_evidence_read(item,statuses.get(item.id,"unchecked")) for item in items],"total":len(items),"page":1,"page_size":len(items),"has_next":False}
@router.get("/evidence/{evidence_id}")
async def get_evidence(evidence_id:str,db:AsyncSession=Depends(get_db)):
    evidence=await db.get(EvidenceFile,evidence_id)
    if not evidence: raise HTTPException(404,"Evidence not found")
    statuses=await _evidence_integrity_statuses(db,[evidence.id])
    return _evidence_read(evidence,statuses.get(evidence.id,"unchecked"))
@router.get("/evidence/{evidence_id}/file")
async def get_file(evidence_id:str,db:AsyncSession=Depends(get_db)):
    e=await db.get(EvidenceFile,evidence_id)
    if not e: raise HTTPException(404,"Evidence not found")
    await append_entry(db,e.id,"view","api",{}); await db.commit(); return FileResponse(e.storage_path,filename=e.original_filename,media_type=e.mime_type,content_disposition_type="inline")
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
    ok=h.hexdigest()==e.sha256 and m.hexdigest()==e.md5
    await append_entry(db,e.id,"verification","api",{"valid":ok})
    await db.commit()
    return {"evidence_id":e.id,"valid":ok,"integrity_status":"verified" if ok else "mismatch","sha256":h.hexdigest(),"md5":m.hexdigest(),"verified_at":datetime.now(timezone.utc).isoformat()}
@router.get("/evidence/{evidence_id}/custody-log")
async def custody(evidence_id:str,db:AsyncSession=Depends(get_db)):
    events=(await db.execute(select(CustodyLogEntry).where(CustodyLogEntry.evidence_id==evidence_id).order_by(CustodyLogEntry.timestamp))).scalars().all(); items=[{"id":event.id,"evidence_id":event.evidence_id,"action":event.action,"performed_by":event.actor,"timestamp":event.timestamp,"notes":json.dumps(event.details) if event.details else None,"hash_before":event.prev_hash,"hash_after":event.entry_hash} for event in events]; return {"items":items,"total":len(items),"page":1,"page_size":len(items),"has_next":False}

@router.post("/cases/{case_id}/chat")
async def chat(case_id:str,body:QuestionRequest,db:AsyncSession=Depends(get_db)):
    case = await db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    chunks = await _retrieve_chunks(case_id, body, db)
    evidence_files = (await db.execute(
        select(EvidenceFile)
        .where(EvidenceFile.case_id == case_id)
        .order_by(EvidenceFile.uploaded_at.desc())
    )).scalars().all()
    media_files = [
        evidence for evidence in evidence_files
        if (not body.evidence_ids or evidence.id in body.evidence_ids)
        and _evidence_read(evidence)["type"] in {"audio", "video"}
    ]
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

    for evidence in media_files:
        index = len(references) + 1
        media_type = _evidence_read(evidence)["type"]
        media_label = "video" if media_type == "video" else "audio"
        limitation = (
            "The file is uploaded, but its frames and soundtrack have not been analyzed."
            if media_type == "video"
            else "The file is uploaded, but its audio has not been transcribed."
        )
        references.append({
            "number": index,
            "evidence_id": evidence.id,
            "evidence_filename": evidence.original_filename,
            "snippet": f"Uploaded {media_label} evidence. {limitation}",
            "page": None,
            "timestamp": None,
            "chunk_id": f"media-{evidence.id}",
        })
        context_parts.append(
            f"[{index}] Uploaded {media_label} file: {evidence.original_filename}. "
            f"This record confirms the file is present; {limitation}"
        )

    system_prompt = (
        "You are EvidenceIQ, a conversational assistant that analyzes the user's uploaded case evidence. "
        "Respond naturally to greetings and informal questions. For factual answers about the case, use only the evidence excerpts supplied below; "
        "never invent facts or imply that you read material that is not present. Cite supporting excerpts inline as [1], [2], etc. "
        "If the excerpts do not answer the question, say what is missing. Distinguish recorded facts from inferences, and keep legal conclusions for human reviewers. "
        "An uploaded media record confirms that a file exists, but it does not reveal what the audio or video contains. "
        "Do not say that an uploaded media file is missing when it appears in the media records. Explain that its audio has not been transcribed or its video frames and soundtrack have not been analyzed, and do not guess at its contents. "
        "When asked to summarize a named file, summarize only excerpts from that file and state when the indexed excerpts are incomplete."
    )
    context = "\n\n".join(context_parts) if context_parts else "No evidence excerpts or uploaded media records were found for this case."
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
    values=(await db.execute(select(Entity).where(Entity.case_id==case_id).order_by(Entity.value))).scalars().all()
    items=[]
    for entity in values:
        mentions=(await db.execute(select(EntityMention.chunk_id).where(EntityMention.entity_id==entity.id))).scalars().all()
        chunks=(await db.execute(select(Chunk.evidence_id).where(Chunk.id.in_(mentions)))).scalars().all() if mentions else []
        items.append({"id":entity.id,"case_id":entity.case_id,"name":entity.value,"type":"organization" if entity.type==EntityType.org else entity.type.value,"aliases":[],"description":None,"evidence_count":len(set(chunks)),"mention_count":len(mentions),"metadata":{}})
    return {"items":items,"total":len(items)}
@router.get("/cases/{case_id}/graph")
async def graph(case_id:str,db:AsyncSession=Depends(get_db)):
    nodes=(await db.execute(select(Entity).where(Entity.case_id==case_id))).scalars().all()
    ids={n.id for n in nodes}; edges=(await db.execute(select(Relationship).where(Relationship.source_entity.in_(ids),Relationship.target_entity.in_(ids)))).scalars().all() if ids else []
    node_items=[]
    for node in nodes:
        mentions=(await db.execute(select(EntityMention.chunk_id).where(EntityMention.entity_id==node.id))).scalars().all()
        node_items.append({"id":node.id,"label":node.value,"type":"organization" if node.type==EntityType.org else node.type.value,"size":max(1,len(mentions)),"color":"#64748b","metadata":{}})
    edge_items=[]
    for edge in edges:
        source_chunks=edge.evidence_chunk_ids or []
        evidence_ids=(await db.execute(select(Chunk.evidence_id).where(Chunk.id.in_(source_chunks)))).scalars().all() if source_chunks else []
        edge_items.append({"id":edge.id,"source":edge.source_entity,"target":edge.target_entity,"label":edge.type,"weight":max(1,len(source_chunks)),"evidence_ids":list(set(evidence_ids))})
    return {"nodes":node_items,"edges":edge_items}
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
async def timeline(case_id:str,db:AsyncSession=Depends(get_db)):
    events=(await db.execute(select(TimelineEvent).where(TimelineEvent.case_id==case_id).order_by(TimelineEvent.timestamp))).scalars().all(); items=[]
    for event in events:
        chunk_ids=event.source_chunk_ids or []
        evidence_ids=(await db.execute(select(Chunk.evidence_id).where(Chunk.id.in_(chunk_ids)))).scalars().all() if chunk_ids else []
        items.append({"id":event.id,"case_id":event.case_id,"timestamp":event.timestamp.isoformat(),"timestamp_end":None,"precision":event.timestamp_precision,"title":event.description[:100],"description":event.description,"evidence_ids":list(set(evidence_ids)),"entity_ids":[],"tags":[],"is_inferred":event.confidence<0.7})
    return {"items":items,"total":len(items)}
@router.post("/cases/{case_id}/timeline/regenerate")
async def regenerate_timeline(case_id:str): return {"status":"use_summary_generation","message":"Regenerate the case summary to rebuild the timeline from processed evidence."}
@router.get("/cases/{case_id}/inconsistencies")
async def inconsistencies(case_id:str,db:AsyncSession=Depends(get_db)):
    values=(await db.execute(select(Inconsistency).where(Inconsistency.case_id==case_id).order_by(Inconsistency.severity,Inconsistency.id))).scalars().all(); items=[]
    for value in values:
        chunk_ids=list(dict.fromkeys((value.chunk_ids_a or [])+(value.chunk_ids_b or [])))
        chunks=(await db.execute(select(Chunk,EvidenceFile).join(EvidenceFile,EvidenceFile.id==Chunk.evidence_id).where(Chunk.id.in_(chunk_ids)))).all() if chunk_ids else []
        snippets=[{"evidence_id":chunk.evidence_id,"evidence_filename":evidence.original_filename,"text":chunk.text,"page":chunk.page} for chunk,evidence in chunks]
        items.append({"id":value.id,"case_id":value.case_id,"title":value.description[:100],"description":value.description,"severity":value.severity,"status":value.status.value,"evidence_ids":list(dict.fromkeys(evidence.id for _,evidence in chunks)),"chunk_ids":chunk_ids,"created_at":datetime.now(timezone.utc).isoformat(),"reviewed_at":None,"reviewer_notes":value.reviewer_note,"snippets":snippets})
    return {"items":items,"total":len(items)}
@router.patch("/inconsistencies/{inconsistency_id}")
async def update_inconsistency(inconsistency_id:str,status:InconsistencyStatus,reviewer_note:str|None=None,db:AsyncSession=Depends(get_db)):
    x=await db.get(Inconsistency,inconsistency_id)
    if not x: raise HTTPException(404,"Inconsistency not found")
    x.status=status; x.reviewer_note=reviewer_note; await db.commit(); return x
@router.post("/cases/{case_id}/inconsistencies/scan")
async def scan_inconsistencies(case_id:str): return {"status":"queued","disclaimer":"Potential inconsistencies for human review; not conclusions."}
@router.get("/cases/{case_id}/duplicates")
async def duplicates(case_id:str,db:AsyncSession=Depends(get_db)):
    groups=(await db.execute(select(DuplicateGroup).where(DuplicateGroup.case_id==case_id))).scalars().all(); items=[]
    for group in groups:
        ids=group.evidence_ids or []
        if len(ids)<2: continue
        items.append({"id":group.id,"case_id":group.case_id,"primary_evidence_id":ids[0],"duplicate_evidence_ids":ids[1:],"similarity_score":group.similarity,"detection_method":group.type,"created_at":datetime.now(timezone.utc).isoformat()})
    return {"items":items,"total":len(items)}
@router.post("/cases/{case_id}/summary")
async def create_summary(case_id:str,db:AsyncSession=Depends(get_db)):
    case=await db.get(Case,case_id)
    if not case: raise HTTPException(404,"Case not found")
    rows=(await db.execute(
        select(Chunk,EvidenceFile)
        .join(EvidenceFile,EvidenceFile.id==Chunk.evidence_id)
        .where(EvidenceFile.case_id==case_id)
        .order_by(EvidenceFile.uploaded_at,Chunk.embedding_id)
        .limit(16)
    )).all()
    if not rows: raise HTTPException(409,"No processed evidence is available yet. Upload documents and wait for processing to finish.")
    evidence_context="\n\n".join(
        f"[source_chunk_id={chunk.id}] {evidence.original_filename}" + (f", page {chunk.page}" if chunk.page else "") + f"\n{chunk.text[:1800]}"
        for index,(chunk,evidence) in enumerate(rows,1)
    )
    messages=[
        {"role":"system","content":"Analyze the supplied case evidence and return exactly one JSON object, not JSON inside a string. Be concise, factual, and do not invent facts. Use this key order and schema: executive_summary (string, under 60 words), entities (array of objects with name,type,description,source_chunk_ids), inconsistencies_summary (string), inconsistencies (array of objects with title,description,severity,source_chunk_ids), chronology (array of objects with date,event,source_chunk_ids), relationships (array of objects with source,target,relation,source_chunk_ids), key_facts (array of strings), key_persons (array of objects with name,role,significance,source_chunk_ids), key_organizations (array of objects with name,role,source_chunk_ids), evidence_gaps (array of strings), recommendations (array of strings). Include at most 2 short items per list. source_chunk_ids must be the exact UUIDs shown in the excerpts, never filenames. Keep recommendations to evidence gathering."},
        {"role":"user","content":f"Case: {case.name}\nDescription: {case.description or 'Not provided'}\n\nEvidence excerpts:\n{evidence_context}"},
    ]
    raw=await complete_chat(messages)
    try:
        json_start=raw.find("{"); json_end=raw.rfind("}")
        if json_start<0 or json_end<json_start: raise ValueError("Model did not return JSON")
        generated=json.loads(raw[json_start:json_end+1])
        # Some local models wrap the requested report JSON inside the summary
        # string. Unwrap it so the brief and derived analysis use real fields.
        for _ in range(4):
            nested=generated.get("executive_summary") if isinstance(generated,dict) else None
            if not isinstance(nested,str) or not nested.lstrip().startswith("{"): break
            try: nested_data=json.loads(nested)
            except json.JSONDecodeError:
                # Recover complete fields if a small local model hits its output
                # limit while wrapping the report in the summary string.
                recovered={}
                summary_match=re.search(r'"executive_summary"\s*:\s*"((?:\\.|[^"\\])*)"',nested)
                if summary_match:
                    try: recovered["executive_summary"]=json.loads('"'+summary_match.group(1)+'"')
                    except json.JSONDecodeError: pass
                for field in ("key_facts","key_persons","key_organizations","chronology","inconsistencies","entities","relationships","evidence_gaps","recommendations"):
                    match=re.search(r'"'+field+r'"\s*:\s*',nested)
                    if not match: continue
                    try: value,_=json.JSONDecoder().raw_decode(nested[match.end():])
                    except json.JSONDecodeError: continue
                    if isinstance(value,list): recovered[field]=value
                match=re.search(r'"inconsistencies_summary"\s*:\s*',nested)
                if match:
                    try:
                        value,_=json.JSONDecoder().raw_decode(nested[match.end():])
                        if isinstance(value,str): recovered["inconsistencies_summary"]=value
                    except json.JSONDecodeError: pass
                if recovered.get("executive_summary"):
                    generated={**generated,**recovered}
                break
            if not isinstance(nested_data,dict) or "executive_summary" not in nested_data: break
            generated={**generated,**nested_data}
    except (ValueError,json.JSONDecodeError):
        generated={"executive_summary":raw.strip(),"key_facts":[],"key_persons":[],"key_organizations":[],"chronology":[],"inconsistencies_summary":"","evidence_gaps":[],"recommendations":[]}
        # If Ollama truncates its JSON wrapper, unwrap any complete inner report
        # fields from the response text instead of saving raw JSON as the brief.
        candidate=raw.strip()
        for _ in range(4):
            try:
                parsed=json.loads(candidate)
                if isinstance(parsed,dict) and isinstance(parsed.get("executive_summary"),str):
                    inner=parsed["executive_summary"]
                    if inner.lstrip().startswith("{"):
                        candidate=inner
                        continue
                    generated={**generated,**parsed}
                    break
            except json.JSONDecodeError:
                match=re.search(r'"executive_summary"\s*:\s*"((?:\\.|[^"\\])*)"',candidate)
                if match:
                    try: generated["executive_summary"]=json.loads('"'+match.group(1)+'"')
                    except json.JSONDecodeError: pass
            for field in ("key_facts","key_persons","key_organizations","chronology","inconsistencies","entities","relationships","evidence_gaps","recommendations"):
                match=re.search(r'"'+field+r'"\s*:\s*',candidate)
                if match:
                    try:
                        value,_=json.JSONDecoder().raw_decode(candidate[match.end():])
                        if isinstance(value,list): generated[field]=value
                    except json.JSONDecodeError: pass
            match=re.search(r'"inconsistencies_summary"\s*:\s*',candidate)
            if match:
                try:
                    value,_=json.JSONDecoder().raw_decode(candidate[match.end():])
                    if isinstance(value,str): generated["inconsistencies_summary"]=value
                except json.JSONDecodeError: pass
            outer_match=re.search(r'"executive_summary"\s*:\s*"((?:\\.|[^"\\])*)"',candidate)
            if outer_match:
                try:
                    unwrapped=json.loads('"'+outer_match.group(1)+'"')
                    if isinstance(unwrapped,str) and unwrapped.lstrip().startswith("{"):
                        candidate=unwrapped
                        continue
                except json.JSONDecodeError: pass
            break
    if not isinstance(generated.get("chronology"),list) or not generated.get("chronology"):
        # Create a dated event for each excerpt that explicitly records a date
        # when the model output is truncated before its chronology section.
        fallback_events=[]
        for chunk,evidence_file in rows:
            for date_text in re.findall(r"\b20\d{2}-\d{1,2}-\d{1,2}\b",chunk.text):
                try: datetime.fromisoformat(date_text)
                except ValueError: continue
                fallback_events.append({"date":date_text,"event":f"{evidence_file.original_filename}: {chunk.text[:220]}","source_chunk_ids":[chunk.id]})
        if fallback_events: generated["chronology"]=fallback_events
    def string_list(name:str)->list[str]:
        values=generated.get(name,[])
        return [value.strip() for value in values if isinstance(value,str) and value.strip()] if isinstance(values,list) else []
    def object_list(name:str,fields:tuple[str,...])->list[dict]:
        values=generated.get(name,[])
        if not isinstance(values,list): return []
        return [{field:str(value.get(field,"")).strip() for field in fields} for value in values if isinstance(value,dict)]
    summary_text=str(generated.get("executive_summary","")).strip()
    if not summary_text: summary_text="The model did not produce a summary. Please try again."
    result={
        "id":str(__import__("uuid").uuid4()),"case_id":case_id,"generated_at":datetime.now(timezone.utc).isoformat(),"model":settings.chat_model,
        "executive_summary":summary_text,"key_facts":string_list("key_facts"),
        "key_persons":object_list("key_persons",("name","role","significance")),
        "key_organizations":object_list("key_organizations",("name","role")),
        "chronology":object_list("chronology",("date","event")),
        "inconsistencies_summary":str(generated.get("inconsistencies_summary", "")),
        "evidence_gaps":string_list("evidence_gaps"),"recommendations":string_list("recommendations"),
        "word_count":len(summary_text.split()),
    }
    if not result["recommendations"]:
        result["recommendations"]=_evidence_next_steps(rows)
    # Rebuild derived case analysis from this generation so every analysis tab
    # reflects the same indexed evidence and model response as the report.
    valid_chunks={chunk.id:chunk for chunk,_ in rows}
    chunks_by_filename={}
    for chunk,evidence_file in rows:
        chunks_by_filename.setdefault(evidence_file.original_filename.casefold(),[]).append(chunk.id)
    def resolve_source_ids(values):
        resolved=[]
        if isinstance(values,str): values=[values]
        for value in values if isinstance(values,list) else []:
            if value in valid_chunks: resolved.append(value)
            else: resolved.extend(chunks_by_filename.get(str(value).casefold(),[]))
        return list(dict.fromkeys(resolved))
    entity_candidates=generated.get("entities",[])
    if not isinstance(entity_candidates,list): entity_candidates=[]
    if not entity_candidates:
        entity_candidates=[
            {**item,"type":"person"} for item in generated.get("key_persons",[]) if isinstance(item,dict)
        ]+[ {**item,"type":"organization"} for item in generated.get("key_organizations",[]) if isinstance(item,dict) ]
    # If the model hits its output cap before its entity section, retain
    # explicitly named people and organizations found in the indexed excerpts.
    if not entity_candidates:
        ignored_names={"purchase order","vendor invoice","goods receipt","warehouse receipt","bank change notice","finance officer","warehouse supervisor","office systems","health supplies"}
        seen_names=set()
        for chunk,evidence_file in rows:
            for match in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}(?:\s+(?:Ltd\.?|Limited|Pvt\.?\s*Ltd\.?|Inc\.?))?",chunk.text):
                name=match.group(0).strip()
                key=name.casefold()
                if key in ignored_names or key in seen_names or name.split()[0] in {"The","This","That","On","For","After"}: continue
                seen_names.add(key)
                kind="organization" if re.search(r"\b(?:Ltd\.?|Limited|Pvt\.?|Inc\.?)\b",name,re.I) else "person"
                entity_candidates.append({"name":name,"type":kind,"source_chunk_ids":[chunk.id]})
    if not result["key_facts"]:
        candidates=[]
        for chunk,evidence_file in rows:
            for sentence in re.split(r"(?<=[.!?])\s+",chunk.text):
                sentence=sentence.strip()
                if len(sentence)<35 or len(sentence)>260 or "SYNTHETIC TEST" in sentence: continue
                priority=sum(term in sentence.lower() for term in ("invoice","purchase order","physical count","payment","quantity","account","discrepancy","received"))
                if priority: candidates.append((priority,f"{sentence} [{evidence_file.original_filename}]."))
        result["key_facts"]=[fact for _,fact in sorted(candidates,key=lambda item:item[0],reverse=True)[:5]]
    if not result["key_persons"]:
        result["key_persons"]=[{"name":str(item.get("name","")),"role":"Person named in evidence","significance":"Mentioned in an indexed evidence excerpt."} for item in entity_candidates if str(item.get("type","")).lower() in {"person","individual"}]
    if not result["key_organizations"]:
        result["key_organizations"]=[{"name":str(item.get("name","")),"role":"Organization named in evidence"} for item in entity_candidates if str(item.get("type","")).lower() in {"organization","organisation","company","org","vendor","buyer"}]
    entity_by_name={}
    entity_query=select(Entity.id).where(Entity.case_id==case_id)
    await db.execute(delete(EntityMention).where(EntityMention.entity_id.in_(entity_query)))
    await db.execute(delete(Relationship).where(or_(Relationship.source_entity.in_(entity_query),Relationship.target_entity.in_(entity_query))))
    await db.execute(delete(Entity).where(Entity.case_id==case_id))
    await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id==case_id))
    await db.execute(delete(Inconsistency).where(Inconsistency.case_id==case_id,Inconsistency.status==InconsistencyStatus.open))
    for item in entity_candidates:
        if not isinstance(item,dict): continue
        name=str(item.get("name","")).strip()[:500]
        if not name: continue
        raw_type=str(item.get("type","other")).lower().strip()
        if raw_type in {"organization","organisation","company","org"}: raw_type="org"
        elif any(term in raw_type for term in ("company","organization","organisation","vendor","buyer","business")): raw_type="org"
        elif any(term in raw_type for term in ("person","individual","employee","manager","witness","officer","clerk","supervisor")): raw_type="person"
        try: entity_type=EntityType(raw_type)
        except ValueError: entity_type=EntityType.other
        source_ids=resolve_source_ids(item.get("source_chunk_ids",[]))
        if not source_ids:
            source_ids=[chunk.id for chunk,_ in rows if name.lower() in chunk.text.lower()]
        key=name.casefold()
        if key in entity_by_name: continue
        entity=Entity(case_id=case_id,type=entity_type,value=name,normalized_value=name.casefold())
        db.add(entity); await db.flush(); entity_by_name[key]=entity.id
        for chunk_id in source_ids: db.add(EntityMention(entity_id=entity.id,chunk_id=chunk_id))
    for item in generated.get("relationships",[]) if isinstance(generated.get("relationships",[]),list) else []:
        if not isinstance(item,dict): continue
        source=entity_by_name.get(str(item.get("source","")).casefold()); target=entity_by_name.get(str(item.get("target","")).casefold())
        if not source or not target or source==target: continue
        chunk_ids=resolve_source_ids(item.get("source_chunk_ids",[]))
        db.add(Relationship(source_entity=source,target_entity=target,type=str(item.get("relation","related to"))[:100],evidence_chunk_ids=chunk_ids))
    chronology=generated.get("chronology",[])
    if isinstance(chronology,list):
        for item in chronology:
            if not isinstance(item,dict): continue
            date_text=str(item.get("date","")).strip(); description=str(item.get("event","")).strip()
            if not date_text or not description: continue
            try:
                timestamp=datetime.fromisoformat(date_text.replace("Z","+00:00"))
                precision="datetime" if "T" in date_text else "date"
                if timestamp.tzinfo is None: timestamp=timestamp.replace(tzinfo=timezone.utc)
            except ValueError: continue
            chunk_ids=resolve_source_ids(item.get("source_chunk_ids",[]))
            db.add(TimelineEvent(case_id=case_id,timestamp=timestamp,timestamp_precision=precision,description=description,source_chunk_ids=chunk_ids,confidence=0.85 if chunk_ids else 0.5))
    inconsistencies=generated.get("inconsistencies",[])
    if not isinstance(inconsistencies,list): inconsistencies=[]
    if not inconsistencies and any(term in str(generated.get("inconsistencies_summary","")).lower() for term in ("discrepanc","inconsisten","conflict","mismatch")):
        inconsistencies=[{"description":generated["inconsistencies_summary"],"severity":"medium","source_chunk_ids":[]}]
    if inconsistencies:
        for item in inconsistencies:
            if not isinstance(item,dict): continue
            description=str(item.get("description") or item.get("title") or "").strip()
            if not description: continue
            severity=str(item.get("severity","medium")).lower()
            if severity not in {"low","medium","high","critical"}: severity="medium"
            chunk_ids=resolve_source_ids(item.get("source_chunk_ids",[]))
            midpoint=(len(chunk_ids)+1)//2
            db.add(Inconsistency(case_id=case_id,description=description,severity=severity,chunk_ids_a=chunk_ids[:midpoint],chunk_ids_b=chunk_ids[midpoint:],status=InconsistencyStatus.open))
    # Exact hashes are a deterministic duplicate signal; do not infer duplicates
    # from similar filenames or model output.
    await db.execute(delete(DuplicateGroup).where(DuplicateGroup.case_id==case_id,DuplicateGroup.type=="exact_sha256"))
    evidence=(await db.execute(select(EvidenceFile).where(EvidenceFile.case_id==case_id))).scalars().all()
    hashes={}
    for item in evidence:
        if item.sha256: hashes.setdefault(item.sha256,[]).append(item.id)
    for ids in hashes.values():
        if len(ids)>1: db.add(DuplicateGroup(case_id=case_id,evidence_ids=ids,type="exact_sha256",similarity=1.0))
    saved=await db.get(CaseSummary,case_id)
    if saved: saved.data=result
    else: db.add(CaseSummary(case_id=case_id,data=result))
    await db.commit()
    return result
@router.get("/cases/{case_id}/summary")
async def get_summary(case_id:str,db:AsyncSession=Depends(get_db)):
    case=await db.get(Case,case_id)
    if not case: raise HTTPException(404,"Case not found")
    summary=await db.get(CaseSummary,case_id)
    if not summary: return None
    data=dict(summary.data or {})
    rows=(await db.execute(select(Chunk,EvidenceFile).join(EvidenceFile,EvidenceFile.id==Chunk.evidence_id).where(EvidenceFile.case_id==case_id).order_by(EvidenceFile.uploaded_at,Chunk.embedding_id).limit(32))).all()
    if not data.get("key_facts"):
        facts=[]
        for chunk,evidence in rows:
            for sentence in re.split(r"(?<=[.!?])\s+",chunk.text):
                sentence=sentence.strip()
                if len(sentence)<35 or len(sentence)>260 or "SYNTHETIC TEST" in sentence: continue
                score=sum(term in sentence.lower() for term in ("invoice","purchase order","physical count","payment","quantity","account","discrepancy","received"))
                if score: facts.append((score,f"{sentence} [{evidence.original_filename}]."))
        data["key_facts"]=[fact for _,fact in sorted(facts,key=lambda item:item[0],reverse=True)[:5]]
    if not data.get("recommendations"):
        data["recommendations"]=_evidence_next_steps(rows)
    if not data.get("key_persons") or not data.get("key_organizations"):
        entities=(await db.execute(select(Entity).where(Entity.case_id==case_id))).scalars().all()
        if not data.get("key_persons"):
            data["key_persons"]=[{"name":item.value,"role":"Person named in evidence","significance":"Mentioned in an indexed evidence excerpt."} for item in entities if item.type==EntityType.person]
        if not data.get("key_organizations"):
            data["key_organizations"]=[{"name":item.value,"role":"Organization named in evidence"} for item in entities if item.type==EntityType.org]
    if data!=summary.data:
        summary.data=data
        await db.commit()
    return data
