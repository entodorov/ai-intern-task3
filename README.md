AI Meeting Notes API (FastAPI)

Това е REST API услуга, изградена с FastAPI, която позволява качване на транскрипции от срещи (като .txt, .docx или .pdf файлове) или директен импорт от Google Docs, и автоматично извличане на структурирани бележки (резюме, задачи, решения) с помощта на AI модели (LLM).

🚀 Архитектура и функционалности (Stretch Goals)

Чиста архитектура (Clean Code): Разделени модули за конфигурация (settings.py), основна логика (main.py) и външни интеграции (app/services/google_docs.py).

Pydantic Validation: Всички Request и Response обекти са строго типизирани и валидирани с Pydantic.

File Upload: Директно качване и парсване на Word (.docx), PDF (.pdf) и .txt документи чрез ендпойнта за създаване на срещи.

Масов Импорт (Bulk Import): Извличане на съдържание от множество Google Docs линкове едновременно с поддръжка на Partial Success (счупен линк не спира цялата операция).

Четене на Private документи: Системата е автентикирана чрез Google Service Account, което позволява достъп дори до частни документи, ако са споделени с бота.

Retry Logic & Background Processing: Комуникацията с Google е подсигурена с tenacity (retry при мрежови грешки). Възможност за пускане на импорта като Background Task (фонов режим).

Multi-LLM поддръжка: Възможност за избор на AI модел при генериране на бележки чрез падащо меню (Dropdown) в Swagger интерфейса. Поддържат се Google Gemini (Gemini 2.5 Flash / 1.5 Pro) и светкавично бързият Groq (Llama 3.1 / 3.3).

Автоматична защита (Safety Truncation): Системата автоматично засича, ако текстът е твърде дълъг за безплатните лимити на Groq, и го съкращава безопасно, за да предотврати HTTP 413 грешки.

Rate Limiting: Вграден In-Memory кеш, предпазващ /process ендпойнта от спам и претоварване на AI API-то (15 секунди cooldown).

Logging & Error Handling: Консистентни HTTP грешки (400, 404, 429, 500) и детайлно логване на процесите в конзолата.

⚙️ Инсталация и стартиране (Local Setup)

Тъй като проектът следва най-добрите практики за сигурност, чувствителните данни и виртуалната среда не се качват в GitHub. Следвайте тези стъпки, за да стартирате проекта локално:

1. Клониране на хранилището

git clone <линк_към_вашето_хранилище>
cd <име_на_папката>


2. Създаване и активиране на виртуална среда

За Windows:

py -m venv venv
.\venv\Scripts\activate


За Linux/Mac:

python3 -m venv venv
source venv/bin/activate


3. Инсталиране на зависимостите

Всички нужни библиотеки (вкл. LangChain, Supabase, PyPDF, Groq, Google API Client) са запазени предварително:

pip install -r requirements.txt


4. Конфигурация на ключовете (.env)

Създайте файл с име .env в главната директория (до requirements.txt) и добавете вашите ключове:

SUPABASE_URL=вашият_supabase_url
SUPABASE_KEY=вашият_supabase_anon_key
GEMINI_API_KEY=вашият_gemini_api_key
GROQ_API_KEY=вашият_groq_api_key


5. Google Service Account (За Task 4)

За да работи импортът от Google Docs, трябва да поставите вашия Service Account JSON ключ в главната директория под името google-credentials.json. Уверете се, че файлът е добавен във вашия .gitignore!

6. Стартиране на сървъра

uvicorn app.main:app --reload


API-то ще бъде достъпно на http://localhost:8000. Автоматично генерираната Swagger документация е на http://localhost:8000/docs.

⚠️ Known Limitations (Известни ограничения за Google Docs)

Достъп до файловете: API-то може да чете само публични документи ИЛИ частни документи, които са изрично споделени с имейла на вашия Service Account (с права Viewer). Ако подадете линк към напълно частен документ, който не е споделен с бота, API-то ще върне грешка за съответния файл (но няма да крашне).

Форматиране: API-то извлича само чист текст (plain text). Таблици, изображения и специално форматиране се игнорират.

📡 Примерни cURL заявки (Тестване)

Забележка: Заменете {MEETING_ID} с реално UUID, върнато от POST заявката за създаване на среща.

1. Вземане на всички срещи

Връща списък със срещи и булев флаг has_notes, показващ дали за тях вече има генерирани бележки.

curl -X 'GET' \
  'http://localhost:8000/meetings' \
  -H 'accept: application/json'


2. Създаване на нова среща (чрез качване на файл)

Позволява качване на .docx, .pdf или .txt файл. Системата автоматично го парсва и "нарязва" (chunking) преди да го запише в базата данни.

curl -X 'POST' \
  'http://localhost:8000/meetings' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'title=Седмична среща на екипа' \
  -F 'file=@/път/до/вашия/файл.pdf'


3. Масов импорт от Google Docs (TASK 4)

За да тествате Background обработката (Stretch Goal), добавете ?background=true към URL-а.

curl -X 'POST' \
  'http://localhost:8000/meetings/import/google-docs?background=false' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "meetings": [
    {
      "title": "Успешна среща 1",
      "google_doc_url": "[https://docs.google.com/document/d/ВАЛИДНО_ID_ТУК/edit]"
    },
    {
      "title": "Счупен линк",
      "google_doc_url": "[https://docs.google.com/document/d/invalid-id/edit]"
    }
  ]
}'


4. Генериране на бележки (Multi-LLM & Rate Limited)

Извиква AI модела за обработка. Има In-memory cooldown от 15 секунди между извикванията за една и съща среща.

curl -X 'POST' \
  'http://localhost:8000/meetings/{MEETING_ID}/process?llm_model=llama-3.1-8b-instant' \
  -H 'accept: application/json' \
  -d ''


5. Вземане на бележките за конкретна среща

Връща вече генерираните JSON бележки (задачи, резюме, решения) от базата данни.

curl -X 'GET' \
  'http://localhost:8000/meetings/{MEETING_ID}/notes' \
  -H 'accept: application/json'
