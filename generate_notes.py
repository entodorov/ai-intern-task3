import os
import json
import time # Използваме го за изчакване (sleep) при грешки
import argparse
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai # Новата официална библиотека на Google
from tqdm import tqdm # Библиотеката за красивия progress bar в терминала

# 1. Зареждане на ключовете от .env файла
load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

# 2. Инициализиране на клиента за достъп до Gemini AI
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
MODEL_ID = 'gemini-2.5-flash' # Използваме най-бързия и евтин модел

def generate_structured_notes(transcript_text):
    """
    Праща текста на Gemini и изисква стриктен JSON отговор.
    Включва логика за повторни опити (retry/backoff) при временно падане на API-то.
    """
    
    # EDGE CASE 1: Проверка за твърде къс текст (под 10 думи)
    if len(transcript_text.split()) < 10:
        tqdm.write("  ⚠️ Текстът е твърде къс за анализ.")
        return None, "Твърде къс текст"

    # Стриктен Prompt (Инструкция), който задължава AI да върне само JSON
    prompt = f"""
    You are an expert AI meeting assistant. Read the following meeting transcript and generate structured notes.
    You MUST respond ONLY with valid JSON. Do not add markdown formatting like ```json or any other text.
    
    The JSON schema MUST exactly match this:
    {{
        "summary": "String (overview of the meeting)",
        "action_items": [
            {{ "text": "Task description", "owner": "Person name or null", "due_date": "Date or null" }}
        ],
        "decisions": ["String", "String"],
        "key_takeaways": ["String", "String"],
        "topics": ["String", "String"],
        "next_steps": [
            {{ "text": "Next step description", "owner": "Person name or null" }}
        ]
    }}
    
    Meeting Transcript:
    {transcript_text}
    """
    
    max_retries = 3 # Максимален брой опити при мрежова грешка
    
    # STRETCH GOAL: Retry/backoff логика (Повтаря заявката при грешка)
    for attempt in range(max_retries):
        try:
            # Пращаме заявката към модела
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
            )
            raw_text = response.text.strip()
            
            # EDGE CASE 2: Изчистване на Markdown тагове (ако AI ги е сложил по погрешка)
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:-3]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:-3]
                
            parsed_json = json.loads(raw_text) 
            
            # EDGE CASE 3: Предпазване от липсващ ключ в JSON-а
            if "action_items" not in parsed_json:
                parsed_json["action_items"] = []
                
            # EDGE CASE 4: Премахване на дублирани задачи (халюцинации на модела)
            unique_actions = []
            seen_texts = set()
            for item in parsed_json["action_items"]:
                # Правим текста с малки букви, за да хванем точното съвпадение
                task_text = item.get("text", "").strip().lower()
                if task_text and task_text not in seen_texts:
                    seen_texts.add(task_text)
                    unique_actions.append(item)
            
            parsed_json["action_items"] = unique_actions # Заместваме с изчистения списък
                
            return parsed_json, response.text
            
        except json.JSONDecodeError:
            tqdm.write("  ❌ ГРЕШКА: Моделът не върна валиден JSON формат.")
            return None, response.text
            
        except Exception as e:
            # Ако сме ударили Rate Limit (Грешка 429) или няма интернет
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt # Изчакваме 1 сек, после 2 сек, после 4...
                tqdm.write(f"  ⚠️ Мрежова грешка. Опит {attempt + 1} неуспешен. Изчакване {wait_time} сек...")
                time.sleep(wait_time)
            else:
                tqdm.write(f"  ❌ ГРЕШКА при връзката с Gemini след {max_retries} опита: {e}")
                return None, str(e)

def process_meeting(meeting):
    """Взима една конкретна среща, праща я на AI и записва резултата в базата."""
    meeting_id = meeting['id']
    title = meeting['title']
    
    # Обединяваме нарязания текст обратно в един голям текст
    try:
        chunks = json.loads(meeting['raw_transcript'])
        full_transcript = " ".join(chunks)
    except:
        full_transcript = meeting['raw_transcript']

    # Викаме функцията за генериране
    notes_dict, raw_response = generate_structured_notes(full_transcript)
    
    if notes_dict:
        # Подготвяме данните за запис в таблицата `notes`
        note_data = {
            "meeting_id": meeting_id,
            "summary": notes_dict.get("summary", "Няма резюме"),
            "action_items": notes_dict.get("action_items", []),
            "decisions": notes_dict.get("decisions", []),
            "key_takeaways": notes_dict.get("key_takeaways", []),
            "topics": notes_dict.get("topics", []),
            "next_steps": notes_dict.get("next_steps", []),
            "llm_raw": raw_response # STRETCH GOAL: Пазим суровия отговор от модела
        }
        
        # Запис в Supabase
        supabase.table("notes").insert(note_data).execute()
        tqdm.write(f"  ✅ Успешно генерирани и записани бележки за: {title}!")
    else:
        tqdm.write(f"  ⏭️ Пропускане на запис поради грешка.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--meeting_id', type=str, help="ID на конкретна среща за обработка.")
    args = parser.parse_args()

    # Извличане на срещите от базата данни
    if args.meeting_id:
        response = supabase.table("meetings").select("*").eq("id", args.meeting_id).execute()
        meetings_to_process = response.data
    else:
        # Взимаме само тези срещи, които все още НЯМАТ генерирани бележки
        notes_response = supabase.table("notes").select("meeting_id").execute()
        processed_ids = [note['meeting_id'] for note in notes_response.data]
        
        meetings_response = supabase.table("meetings").select("*").execute()
        meetings_to_process = [m for m in meetings_response.data if m['id'] not in processed_ids]
        
        if not meetings_to_process:
            print("🎉 Всички срещи вече имат генерирани бележки!")
            return

    print(f"Намерени {len(meetings_to_process)} срещи за обработка.")
    
    # Визуално показване на прогреса чрез tqdm
    for meeting in tqdm(meetings_to_process, desc="🧠 Генериране на бележки", unit="среща"):
        process_meeting(meeting)
        time.sleep(4) # Изкуствено забавяне, за да не претоварим безплатния лимит на Google (Rate Limit 429)

if __name__ == "__main__":
    main()