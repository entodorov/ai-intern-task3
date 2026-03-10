import logging
import time
import json
import io
import docx
from enum import Enum
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from supabase import create_client, Client

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate

from app.settings import settings

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

@app.get("/meetings", response_model=List[MeetingMetadata], summary="Вземане на всички срещи", description="Връща списък с всички срещи и флаг дали имат генерирани бележки.")
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

@app.get("/meetings/{meeting_id}", response_model=MeetingDetail, summary="Детайли за конкретна среща", description="Връща пълния транскрипт и детайлите за една среща по нейното ID.")
def get_meeting(meeting_id: str):
    response = supabase.table("meetings").select("*").eq("id", meeting_id).execute()
    
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Срещата не е намерена.")
    
    return response.data[0]

@app.post("/meetings", summary="Създаване на нова среща", description="Приема .txt или .docx файл, парсва го и го записва в базата данни.")
async def create_meeting(title: str = Form(...), file: UploadFile = File(...)):
    if not (file.filename.endswith(".txt") or file.filename.endswith(".docx")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Позволени са само .txt и .docx файлове.")
    
    try:
        content_bytes = await file.read()
        extracted_text = ""
        
        if file.filename.endswith(".docx"):
            doc = docx.Document(io.BytesIO(content_bytes))
            extracted_text = " ".join([p.text for p in doc.paragraphs if p.text.strip()])
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

@app.post("/meetings/{meeting_id}/process", summary="Генериране на AI бележки", description="Използва избран LLM модел (Gemini или Groq), за да анализира транскрипта и да извади резюме, задачи и решения.")
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

    # Защита от претоварване на Groq (лимит 6000 токена на минута за безплатен акаунт)
    if "groq" in llm_model.value and len(full_text) > 15000:
        logger.warning(f"Текстът е твърде дълъг за Groq ({len(full_text)} символа). Орязваме го до безопасно ниво.")
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
        logger.error(f"Подробна грешка от LLM: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Възникна грешка при връзката с AI модела: {str(e)}")

@app.get("/meetings/{meeting_id}/notes", response_model=List[NoteResponse], summary="Вземане на бележки", description="Връща всички генерирани бележки и решения за конкретна среща.")
def get_meeting_notes(meeting_id: str):
    response = supabase.table("notes").select("*").eq("meeting_id", meeting_id).execute()
    
    if not response.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Няма намерени бележки за тази среща.")
        
    return response.data