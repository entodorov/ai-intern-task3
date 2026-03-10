AI Meeting Notes API (FastAPI)

Това е REST API услуга, изградена с FastAPI, която позволява качване на транскрипции от срещи (като .txt или .docx файлове) и автоматично извличане на структурирани бележки (резюме, задачи, решения) с помощта на LLM.

Архитектура и функционалности (Stretch Goals)

Чиста архитектура (Clean Code): Разделени модули за конфигурация (settings.py) и основна логика (main.py).

Pydantic Validation: Всички Request и Response обекти са строго типизирани и валидирани с Pydantic.

File Upload: Директно качване и парсване на Word (.docx) и .txt документи чрез ендпойнта за създаване на срещи.

Multi-LLM поддръжка: Възможност за избор на AI модел при генериране на бележки чрез падащо меню (Dropdown) в Swagger интерфейса (gemini-2.5-flash или gemini-1.5-pro).

Rate Limiting: Вграден In-Memory кеш, предпазващ /process ендпойнта от спам и претоварване на AI API-то (15 секунди cooldown).

Logging & Error Handling: Консистентни HTTP грешки (400, 404, 429, 500) и детайлно логване на процесите в конзолата.

Инсталация и стартиране на нов компютър (Local Setup)

Тъй като проектът следва най-добрите практики за сигурност, чувствителните данни и виртуалната среда не се качват в GitHub. Следвайте тези стъпки, за да стартирате проекта локално:

1. Клониране на хранилището

git clone <линк_към_вашето_хранилище>
cd <име_на_папката>


2. Създаване и активиране на виртуална среда

За Windows:

python -m venv venv
.\venv\Scripts\activate


За Linux/Mac:

python3 -m venv venv
source venv/bin/activate


3. Инсталиране на зависимостите

Всички нужни библиотеки са запазени предварително:

pip install -r requirements.txt


4. Конфигурация на ключовете (.env)

Създайте файл с име .env в главната директория (до requirements.txt) и добавете вашите ключове:

SUPABASE_URL=вашият_supabase_url
SUPABASE_KEY=вашият_supabase_anon_key
GEMINI_API_KEY=вашият_gemini_api_key


5. Стартиране на сървъра

uvicorn app.main:app --reload


API-то ще бъде достъпно на http://localhost:8000.
Автоматично генерираната Swagger документация е на http://localhost:8000/docs.

📡 Примерни cURL заявки (Тестване)

Забележка: Заменете {MEETING_ID} с реално UUID, върнато от POST заявката за създаване на среща.

1. Вземане на всички срещи

Връща списък със срещи и булев флаг has_notes, показващ дали за тях вече има генерирани бележки.

curl -X 'GET' \
  'http://localhost:8000/meetings' \
  -H 'accept: application/json'


2. Създаване на нова среща (чрез качване на файл)

Позволява качване на .docx или .txt файл. Системата автоматично го парсва и "нарязва" (chunking) преди да го запише в базата данни.

curl -X 'POST' \
  'http://localhost:8000/meetings' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'title=Седмична среща на екипа' \
  -F 'file=@/път/до/вашия/файл.docx'


3. Генериране на бележки (Multi-LLM & Rate Limited)

Извиква AI модела за обработка. Има In-memory cooldown от 15 секунди между извикванията за една и съща среща.

curl -X 'POST' \
  'http://localhost:8000/meetings/{MEETING_ID}/process?llm_model=gemini-2.5-flash' \
  -H 'accept: application/json' \
  -d ''


4. Вземане на бележките за конкретна среща

Връща вече генерираните JSON бележки (задачи, резюме, решения) от базата данни.

curl -X 'GET' \
  'http://localhost:8000/meetings/{MEETING_ID}/notes' \
  -H 'accept: application/json'
