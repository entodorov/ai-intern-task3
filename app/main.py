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
from langchain_core.prompts import PromptTemplate

# Importirame centraliziranite nastroiki
from app.settings import settings

# 1. SETUP LOGGING
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FastAPI-App")

# 2. SETUP FASTAPI & SUPABASE
app = FastAPI(
    title="AI Meeting Notes API",
    description="API za avtomatizirano izvlichane na belejki s poddrujka na Multi-LLM.",
    version="1.0.0"
)

supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

# 3. SETUP RATE LIMITING (In-memory cache)
RATE_LIMIT_CACHE: Dict[str, float] = {}
RATE_LIMIT_SECONDS = 15.0 

# 4. PYDANTIC MODELS & ENUMS
class LLMChoice(str, Enum):
    """Izbor na LLM model, koito shte se pokazva kato padashto menq v Swagger."""
    gemini_flash = "gemini-2.5-flash"
    gemini_pro = "gemini-1.5-pro"
    # Tuka mojesh da dobavish i 'gpt-4o' v budeshte!

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
    summary: str = Field(description="Kratko rezume na sreshtata (3-4 izrecheniq)")
    action_items: list[str] = Field(description="Spisuk sus zadachi za izpulnenie")
    decisions: list[str] = Field(description="Spisuk s vzetite resheniq")

# 5. ENDPOINTS

@app.get("/meetings", response_model=List[MeetingMetadata])
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
        logger.error(f"Greshka: {str(e)}")
        raise HTTPException(status_code=500, detail="Syrvurna greshka.")

@app.get("/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str):
    response = supabase.table("meetings").select("*").eq("id", meeting_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Sreshtata ne e namerena.")
    return response.data[0]

@app.post("/meetings")
async def create_meeting(title: str = Form(...), file: UploadFile = File(...)):
    if not (file.filename.endswith(".txt") or file.filename.endswith(".docx")):
        raise HTTPException(status_code=400, detail="Samo .txt i .docx.")
    
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
        
        return {"message": "Sreshtata e suzdadena uspeshno", "meeting": res.data[0]}
        
    except Exception as e:
        logger.error(f"Greshka obrabotka: {str(e)}")
        raise HTTPException(status_code=500, detail="Greshka s faila.")

@app.post("/meetings/{meeting_id}/process")
def process_meeting_notes(meeting_id: str, llm_model: LLMChoice = LLMChoice.gemini_flash):
    """
    Generira belejki. Vkluchva Rate Limiting i Multi-LLM Dropdown menu.
    """
    # 1. Rate Limiting
    current_time = time.time()
    if meeting_id in RATE_LIMIT_CACHE:
        time_passed = current_time - RATE_LIMIT_CACHE[meeting_id]
        if time_passed < RATE_LIMIT_SECONDS:
            raise HTTPException(
                status_code=429, 
                detail=f"Izchakaite oshte {int(RATE_LIMIT_SECONDS - time_passed)} sekundi."
            )
    RATE_LIMIT_CACHE[meeting_id] = current_time

    # 2. Vzimane na sreshtata
    meeting_res = supabase.table("meetings").select("raw_transcript").eq("id", meeting_id).execute()
    if not meeting_res.data:
        raise HTTPException(status_code=404, detail="Ne e namerena sreshta.")
    
    raw_data = meeting_res.data[0]["raw_transcript"]
    try:
        chunks = json.loads(raw_data)
        full_text = " ".join(chunks) if isinstance(chunks, list) else raw_data
    except:
        full_text = raw_data

    # 3. LangChain Multi-LLM logika
    model_name_str = llm_model.value # Vzimame stoinostta ot padashtoto menq
    logger.info(f"Start s model: {model_name_str}")
    
    try:
        llm = ChatGoogleGenerativeAI(
            model=model_name_str, 
            temperature=0.1, 
            max_retries=2, 
            api_key=settings.gemini_api_key
        )
        structured_llm = llm.with_structured_output(StructuredNotes)
        prompt = PromptTemplate.from_template("Analizirai tozi transkript i izvadi belejki:\n{text}")
        chain = prompt | structured_llm
        
        result: StructuredNotes = chain.invoke({"text": full_text})
        
        # 4. Zapisvane s imeto na izbraniq model
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
        raise HTTPException(status_code=500, detail="Greshka pri AI generaciqta.")

@app.get("/meetings/{meeting_id}/notes", response_model=List[NoteResponse])
def get_meeting_notes(meeting_id: str):
    response = supabase.table("notes").select("*").eq("meeting_id", meeting_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Nqma belejki.")
    return response.data