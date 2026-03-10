# --- ИМПОРТИРАНЕ НА БИБЛИОТЕКИ ---
import os              # За работа с папки и файлове (търсене на проекти)
import json            # За да превърнем списъка с парчета текст (chunks) в текст за базата
from datetime import datetime  # За да вземем датата на създаване на файла
from dotenv import load_dotenv # За да скрием паролите си в .env файл
from supabase import create_client, Client # За връзка с базата данни
import docx            # За четене на Word (.docx) файлове

# --- СТЪПКА 1: Връзка с базата данни ---
# load_dotenv() прочита скрития файл .env и зарежда паролите в паметта
load_dotenv()
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
# Създаваме "клиент" - това е обектът, чрез който си говорим със Supabase
supabase: Client = create_client(url, key)

# --- СТЪПКА 2: Функция за нарязване на текста (Transcript Chunking) ---
def chunk_text(text, chunk_size=1500):
    """
    Разделя дълъг текст на парчета. 
    AI моделите имат лимит на паметта (Context Window). Ако им дадем 100 страници наведнъж,
    ще "забият". Затова режем текста на парчета от по 1500 символа, но без да цепим думи.
    """
    words = text.split(' ') # Разделяме целия текст на отделни думи
    chunks = []             # Тук ще пазим готовите парчета
    current_chunk = []      # Тук събираме думите за текущото парче
    current_length = 0      # Брояч на символите в текущото парче
    
    for word in words:
        current_length += len(word) + 1 # +1 е заради интервала след думата
        
        # Ако добавянето на тази дума прескочи лимита от 1500...
        if current_length > chunk_size:
            # ...залепваме събраните думи и ги запазваме като готово парче
            chunks.append(' '.join(current_chunk))
            # Започваме ново парче с текущата дума
            current_chunk = [word]
            current_length = len(word) + 1
        else:
            # Иначе просто добавяме думата към текущото парче
            current_chunk.append(word)
            
    # Ако накрая са останали думи, които не стигат 1500 символа, ги добавяме и тях
    if current_chunk:
        chunks.append(' '.join(current_chunk))
        
    return chunks

# --- СТЪПКА 3: Функция за четене на Word файлове ---
def read_docx(file_path):
    """Отваря .docx файл и изважда целия текст от него."""
    doc = docx.Document(file_path)
    full_text = []
    
    # Минаваме през всеки параграф в документа
    for para in doc.paragraphs:
        # para.text.strip() маха излишните празни пространства (интервали/нови редове)
        if para.text.strip(): 
            full_text.append(para.text.strip())
            
    # Съединяваме всички параграфи с нов ред (\n)
    return '\n'.join(full_text)

# --- СТЪПКА 4: Главната логика ---
def main():
    data_dir = 'data' # Главната папка с данните
    
    # Защита: Ако папката не съществува, спираме програмата
    if not os.path.exists(data_dir):
        print(f"Папката {data_dir} не беше намерена.")
        return

    # os.listdir минава през всички подпапки (edamame, gatekeeper, inspace)
    # Това е решението на задачата "Multiple projects"
    for project_folder in os.listdir(data_dir):
        project_path = os.path.join(data_dir, project_folder)
        
        if os.path.isdir(project_path):
            print(f"\n📁 Проверявам проект: {project_folder}...")
            
            # Сега минаваме през всички файлове вътре в папката на проекта
            for filename in os.listdir(project_path):
                if filename.endswith('.docx'):
                    file_path = os.path.join(project_path, filename)
                    title = filename.replace('.docx', '') # Името на файла става заглавие
                    
                    # --- ЗАЩИТА ОТ ДУБЛИКАТИ (Idempotency) ---
                    # Питам базата: "Има ли вече запис с това заглавие?"
                    existing = supabase.table("meetings").select("id").eq("title", title).execute()
                    if len(existing.data) > 0:
                        print(f"  ⏭️ ПРОПУСКАНЕ: '{title}' вече съществува в базата.")
                        continue # Прескачаме към следващия файл
                    
                    # 1. Четем файла
                    raw_transcript = read_docx(file_path)
                    
                    # 2. Режем текста (Chunking)
                    chunks = chunk_text(raw_transcript, chunk_size=1500)
                    print(f"  ✂️ Текстът е нарязан на {len(chunks)} AI парчета (chunks).")
                    
                    # 3. Превръщаме списъка в JSON формат (Supabase предпочита текст/JSON)
                    transcript_json = json.dumps(chunks, ensure_ascii=False)
                    
                    # 4. Взимаме датата на файла
                    timestamp = os.path.getmtime(file_path)
                    meeting_date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
                    
                    # 5. Подготвяме "пакета" с данни за базата
                    meeting_data = {
                        "title": title,
                        "meeting_date": meeting_date,
                        "source": project_folder, # Тук записваме името на проекта!
                        "raw_transcript": transcript_json
                    }
                    
                    # 6. Изпращаме пакета в таблицата meetings
                    try:
                        supabase.table("meetings").insert(meeting_data).execute()
                        print(f"  ✅ Успешно добавена нова среща: {title}")
                    except Exception as e:
                        print(f"  ❌ Проблем със записването: {e}")

# Това казва на Python: "Ако някой пусне този файл, стартирай функцията main()"
if __name__ == "__main__":
    main()

# # #Менторът: "Защо нарязваш текста на парчета (chunks)?"

# #     #Ти: "Защото LLM моделите имат лимит на паметта, наречен Context Window. Ако подадем прекалено 
# дълъг текст, моделът или ще даде грешка, или ще 'халюцинира' и ще забрави началото на срещата. 
# Чрез chunking подготвям данните за по-лесна и точна обработка от AI."

# # #Менторът: "Как реши проблема с поддръжката на много проекти?"

# #  #   Ти: "Използвам файловата система. Скриптът обхожда динамично всички подпапки в директорията 
# data/. Взима името на подпапката (напр. edamame) и го записва в колоната source в базата. Така утре 
# можем да добавим нови проекти само чрез създаване на нова папка, без да променяме кода."

# # #Менторът: "Какво ще стане, ако пусна скрипта 5 пъти подред?"

# #  #   Ти: "Няма да стане нищо лошо. Имплементирал съм проверка – преди да запише нов ред, 
# #  скриптът прави SELECT заявка към Supabase, за да провери дали това заглавие вече съществува.
# #   Това прави скрипта идемпотентен (idempotent)."
# # 