import os
import json
import time # Използваме го за предпазване от Rate Limit
import argparse
from dotenv import load_dotenv
from supabase import create_client, Client
from pydantic import BaseModel, Field # Pydantic се ползва за дефиниране на стриктна структура
from typing import List, Optional
from langchain_google_genai import ChatGoogleGenerativeAI # Интеграцията на LangChain с Google
from tqdm import tqdm

load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

# ---------------------------------------------------------
# 1. ДЕФИНИРАНЕ НА СХЕМАТА С PYDANTIC (The LangChain Way)
# Дефинираме класове. LangChain ще ги прочете и сам ще си 
# преведе какво трябва да изисква от AI модела.
# ---------------------------------------------------------
class ActionItem(BaseModel):
    text: str = Field(description="Task description")
    owner: Optional[str] = Field(description="Person name or null", default=None)
    due_date: Optional[str] = Field(description="Date or null", default=None)

class NextStep(BaseModel):
    text: str = Field(description="Next step description")
    owner: Optional[str] = Field(description="Person name or null", default=None)

class MeetingNotes(BaseModel):
    summary: str = Field(description="Overview of the meeting")
    action_items: List[ActionItem] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    key_takeaways: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    next_steps: List[NextStep] = Field(default_factory=list)

# ---------------------------------------------------------
# 2. ИНИЦИАЛИЗИРАНЕ НА LANGCHAIN МОДЕЛА
# ---------------------------------------------------------
# temperature=0 означава, че моделът ще бъде максимално точен и няма да си измисля (халюцинира)
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, api_key=os.environ.get("GEMINI_API_KEY"))
# Тази малка функция автоматично гарантира, че моделът НЯМА да върне счупен JSON!
structured_llm = llm.with_structured_output(MeetingNotes)

def generate_with_langchain(transcript_text):
    """Генерира бележки, използвайки LangChain и Pydantic за структуриран изход."""
    
    # Проверка за къс текст
    if len(transcript_text.split()) < 10:
        tqdm.write("  ⚠️ Текстът е твърде къс за анализ.")
        return None, "Твърде къс текст"

    # Виж колко по-кратък и лесен е prompt-ът тук! 
    prompt = f"Read the following meeting transcript and generate structured meeting notes.\n\nTranscript:\n{transcript_text}"
    
    try:
        # LangChain върши цялата магия тук - праща prompt-а и автоматично парсва JSON-а!
        result = structured_llm.invoke(prompt)
        
        # Превръщаме Pydantic обекта обратно в стандартен Python речник (dict), за да го запишем в базата
        notes_dict = result.model_dump()
        
        # EDGE CASE: Премахване на дублирани задачи
        unique_actions = []
        seen_texts = set()
        for item in notes_dict["action_items"]:
            task_text = item.get("text", "").strip().lower()
            if task_text and task_text not in seen_texts:
                seen_texts.add(task_text)
                unique_actions.append(item)
        notes_dict["action_items"] = unique_actions

        return notes_dict, json.dumps(notes_dict, ensure_ascii=False)
        
    except Exception as e:
        tqdm.write(f"  ❌ ГРЕШКА при обработката с LangChain: {e}")
        return None, str(e)

def process_meeting(meeting):
    meeting_id = meeting['id']
    title = meeting['title']
    
    try:
        chunks = json.loads(meeting['raw_transcript'])
        full_transcript = " ".join(chunks)
    except:
        full_transcript = meeting['raw_transcript']

    notes_dict, raw_response = generate_with_langchain(full_transcript)
    
    if notes_dict:
        note_data = {
            "meeting_id": meeting_id,
            "summary": notes_dict.get("summary", "Няма резюме"),
            "action_items": notes_dict.get("action_items", []),
            "decisions": notes_dict.get("decisions", []),
            "key_takeaways": notes_dict.get("key_takeaways", []),
            "topics": notes_dict.get("topics", []),
            "next_steps": notes_dict.get("next_steps", []),
            "llm_raw": raw_response
        }
        
        supabase.table("notes").insert(note_data).execute()
        tqdm.write(f"  ✅ Успешно генерирани и записани бележки чрез LangChain за: {title}!")
    else:
        tqdm.write(f"  ⏭️ Пропускане на запис поради грешка.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--meeting_id', type=str, help="ID на конкретна среща за обработка.")
    args = parser.parse_args()

    if args.meeting_id:
        response = supabase.table("meetings").select("*").eq("id", args.meeting_id).execute()
        meetings_to_process = response.data
    else:
        notes_response = supabase.table("notes").select("meeting_id").execute()
        processed_ids = [note['meeting_id'] for note in notes_response.data]
        
        meetings_response = supabase.table("meetings").select("*").execute()
        meetings_to_process = [m for m in meetings_response.data if m['id'] not in processed_ids]
        
        if not meetings_to_process:
            print("🎉 Всички срещи вече имат генерирани бележки!")
            return

    print(f"Намерени {len(meetings_to_process)} срещи за обработка.")
    
    # Цикъл с лента за зареждане и предпазване от Rate Limits
    for meeting in tqdm(meetings_to_process, desc="🦜 LangChain AI", unit="среща"):
        process_meeting(meeting)
        time.sleep(4) # Изчакваме 4 секунди между заявките към Google

if __name__ == "__main__":
    main()