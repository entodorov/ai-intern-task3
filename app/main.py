import logging
import time
import json
import io
import docx
import datetime # Нови импорти
from pypdf import PdfReader
from enum import Enum
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from supabase import create_client, Client

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate

from app.settings import settings
from app.services.google_docs import extract_doc_id, fetch_document_data # Импорт на нашия нов сървис

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FastAPI-App")

app = FastAPI(
    title="AI Meeting Notes API",
    description="API за автоматизирано извличане на бележки от срещи с поддръжка на Gemini и Groq.",
    version="1.0.0"
)

supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

RATE_LIMIT_CACHE: Dict[str, float] = {}
RATE_LIMIT_SECONDS = 15.0

class LLMChoice(str, Enum):
    gemini_flash = "gemini-2.5-flash"
    gemini_pro = "gemini-1.5-pro"
    groq_llama_8b = "llama-3.1-8b-instant"
    groq_llama_70b = "llama-3.3-70b-versatile"

class MeetingMetadata(BaseModel):
    id: str
    title: str
    created_at: str
    has_notes: bool = False

class MeetingDetail(BaseModel):
    id: str
    title: str
    raw_transcript: str | list
    created_at: str

class NoteResponse(BaseModel):
    id: str
    meeting_id: str
    summary: str
    action_items: list[str]
    decisions: list[str]
    llm: Optional[str] = "gemini-2.5-flash"
    created_at: str

class StructuredNotes(BaseModel):
    summary: str = Field(description="Кратко резюме на срещата (3-4 изречения)")
    action_items: list[str] = Field(description="Списък със задачи за изпълнение")
    decisions: list[str] = Field(description="Списък с взетите решения")

# --- НОВИ МОДЕЛИ ЗА TASK 4 ---
class GoogleDocItem(BaseModel):
    title: str
    google_doc_url: str

class GoogleDocsImportRequest(BaseModel):
    meetings: List[GoogleDocItem]

class ImportResult(BaseModel):
    title: str
    status: str
    message: str
    external_id: Optional[str] = None

class GoogleDocsImportResponse(BaseModel):
    results: List[ImportResult]
# -----------------------------

@app.get("/meetings", response_model=List[MeetingMetadata], summary="Вземане на всички срещи")
def get_meetings():
    try:
        meetings_res = supabase.table("meetings").select("id, title, created_at").execute()
        meetings = meetings_res.data
        
        notes_res = supabase.table("notes").select("meeting_id").execute()
        meetings_with_notes = {note["meeting_id"] for note in notes_res.data}
        
        result = []
        for m in meetings:
            result.append(MeetingMetadata(
                id=m["id"],
                title=m["title"],
                created_at=m["created_at"],
                has_notes=(m["id"] in meetings_with_notes)
            ))
        
        return result
    except Exception as e:
        logger.error(f"Грешка при взимане на срещите: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Възникна сървърна грешка.")

@app.get("/meetings/{meeting_id}", response_model=MeetingDetail, summary="Детайли за конкретна среща")
def get_meeting(meeting_id: str):
    response = supabase.table("meetings").select("*").eq("id", meeting_id).execute()
    
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Срещата не е намерена.")
    
    return response.data[0]

@app.post("/meetings", summary="Създаване на нова среща")
async def create_meeting(title: str = Form(...), file: UploadFile = File(...)):
    allowed_ext = [".txt", ".docx", ".pdf"]
    if not any(file.filename.endswith(ext) for ext in allowed_ext):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Позволени са само .txt, .docx и .pdf файлове.")
    
    try:
        content_bytes = await file.read()
        extracted_text = ""
        
        if file.filename.endswith(".docx"):
            doc = docx.Document(io.BytesIO(content_bytes))
            extracted_text = " ".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif file.filename.endswith(".pdf"):
            pdf_reader = PdfReader(io.BytesIO(content_bytes))
            extracted_text = " ".join([page.extract_text() for page in pdf_reader.pages if page.extract_text()])
        else:
            extracted_text = content_bytes.decode("utf-8")
        
        chunk_size = 2000
        chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]
        transcript_json = json.dumps(chunks, ensure_ascii=False)
        
        data = {"title": title, "raw_transcript": transcript_json}
        res = supabase.table("meetings").insert(data).execute()
        
        return {"message": "Срещата е създадена успешно", "meeting": res.data[0]}
        
    except Exception as e:
        logger.error(f"Грешка при обработка на файла: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Грешка при обработка на файла.")

# --- НОВ ЕНДПОЙНТ ЗА ТАСК 4 ---
def process_single_google_doc(item: GoogleDocItem) -> ImportResult:
    """Хелпър функция, която обработва един единичен документ."""
    try:
        # 1. Валидация и екстрактване на ID
        doc_id = extract_doc_id(item.google_doc_url)
        
        # 2. Теглене на данните чрез сървис модула
        doc_data = fetch_document_data(doc_id)
        text_content = doc_data["text"]
        real_title = doc_data["title"]
        
        # УМНА ЛОГИКА ЗА ЗАГЛАВИЕТО:
        # Ако потребителят е оставил Swagger дефолтното "string", празно е, или е "1" -> ползваме истинското от Google
        final_title = real_title if item.title in ["string", "", "1"] else item.title
        
        # 3. Записване в базата
        chunk_size = 2000
        chunks = [text_content[i:i+chunk_size] for i in range(0, len(text_content), chunk_size)]
        transcript_json = json.dumps(chunks, ensure_ascii=False)
        
        data = {
            "title": final_title,  # <-- Използваме умното заглавие
            "raw_transcript": transcript_json,
            "source": "google_docs",
            "source_url": item.google_doc_url,
            "external_id": doc_id,
            "meeting_date": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        supabase.table("meetings").insert(data).execute()
        
        return ImportResult(title=final_title, status="success", message="Успешно импортирана", external_id=doc_id)
    except Exception as e:
        logger.error(f"Грешка при импорт: {str(e)}")
        return ImportResult(title=item.title, status="error", message=str(e), external_id=None)

@app.post("/meetings/import/google-docs", response_model=GoogleDocsImportResponse, summary="Масов импорт от Google Docs")
def import_google_docs(request: GoogleDocsImportRequest, background_tasks: BackgroundTasks, background: bool = False):
    """
    Импортира списък от Google Docs. 
    Ако background=True, задачите се пускат във фонов режим (Stretch Goal).
    """
    results = []
    
    if background:
        # Stretch Goal: Background Processing
        for item in request.meetings:
            try:
                # Валидираме бързо URL-а
                doc_id = extract_doc_id(item.google_doc_url)
                # Пускаме тегленето да се случва във фонов режим, без да бави потребителя
                background_tasks.add_task(process_single_google_doc, item)
                results.append(ImportResult(title=item.title, status="queued", message="Добавена във фоновата опашка", external_id=doc_id))
            except Exception as e:
                # Ако URL-ът е невалиден, гърмим веднага
                results.append(ImportResult(title=item.title, status="error", message=str(e)))
    else:
        # Стандартно синхронно изпълнение
        for item in request.meetings:
            res = process_single_google_doc(item)
            results.append(res)
            
    return GoogleDocsImportResponse(results=results)
# -----------------------------

@app.post("/meetings/{meeting_id}/process", summary="Генериране на AI бележки")
def process_meeting_notes(meeting_id: str, llm_model: LLMChoice = LLMChoice.gemini_flash):
    current_time = time.time()
    if meeting_id in RATE_LIMIT_CACHE:
        time_passed = current_time - RATE_LIMIT_CACHE[meeting_id]
        if time_passed < RATE_LIMIT_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                detail=f"Моля, изчакайте още {int(RATE_LIMIT_SECONDS - time_passed)} секунди преди ново генериране."
            )
    RATE_LIMIT_CACHE[meeting_id] = current_time

    meeting_res = supabase.table("meetings").select("raw_transcript").eq("id", meeting_id).execute()
    if not meeting_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Срещата не е намерена.")
    
    raw_data = meeting_res.data[0]["raw_transcript"]
    
    try:
        chunks = json.loads(raw_data)
        full_text = " ".join(chunks) if isinstance(chunks, list) else raw_data
    except:
        full_text = raw_data

    if "groq" in llm_model.value and len(full_text) > 15000:
        full_text = full_text[:15000] + "\n\n... [Текстът е съкратен поради безплатните лимити на Groq API]"

    model_name_str = llm_model.value
    
    try:
        if "gemini" in model_name_str:
            llm = ChatGoogleGenerativeAI(
                model=model_name_str, 
                temperature=0.1, 
                max_retries=2, 
                api_key=settings.gemini_api_key
            )
        else:
            llm = ChatGroq(
                model=model_name_str,
                temperature=0.1,
                max_retries=2,
                api_key=settings.groq_api_key
            )

        structured_llm = llm.with_structured_output(StructuredNotes)
        prompt = PromptTemplate.from_template("Анализирай следния транскрипт от среща и извади бележки:\n{text}")
        chain = prompt | structured_llm
        
        result: StructuredNotes = chain.invoke({"text": full_text})
        
        notes_data = {
            "meeting_id": meeting_id,
            "summary": result.summary,
            "action_items": result.action_items,
            "decisions": result.decisions,
            "llm": model_name_str
        }
        
        inserted = supabase.table("notes").insert(notes_data).execute()
        
        return inserted.data[0]
        
    except Exception as e:
        del RATE_LIMIT_CACHE[meeting_id]
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Грешка при AI модела: {str(e)}")

@app.get("/meetings/{meeting_id}/notes", response_model=List[NoteResponse], summary="Вземане на бележки")
def get_meeting_notes(meeting_id: str):
    response = supabase.table("notes").select("*").eq("meeting_id", meeting_id).execute()
    
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Няма намерени бележки за тази среща.")
        f
    return response.data