import re
import os
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("FastAPI-App")

# Това са правата, които искаме от Google - само за четене на документи
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
CREDENTIALS_FILE = 'google-credentials.json'

def extract_doc_id(url: str) -> str:
    """
    Извлича уникалното ID на документа от пълния Google Docs URL.
    Например от: https://docs.google.com/document/d/12345ABCDE/edit
    Ще върне: 12345ABCDE
    """
    match = re.search(r"/document/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Невалиден Google Docs URL адрес")
    return match.group(1)

def get_google_service():
    """
    Зарежда Service Account ключа и прави удостоверена връзка с Google API.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(f"Липсва файлът {CREDENTIALS_FILE} в главната папка!")
    
    # Създаване на credentials (права) от JSON файла
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    
    # Изграждане на самия service (API клиент)
    service = build('docs', 'v1', credentials=creds)
    return service

# Stretch Goal: Retry логика. Ако Google върне грешка (например при преусложнен трафик),
# ще опита отново до 3 пъти, като изчаква експоненциално повече време между опитите.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_document_data(doc_id: str) -> dict:
    """
    Сваля съдържанието на Google Document и неговото заглавие.
    Връща речник: {"title": "Заглавие", "text": "Съдържание"}
    """
    logger.info(f"Изтегляне на Google Doc с ID: {doc_id}")
    
    service = get_google_service()
    
    # Вземаме документа от Google API
    document = service.documents().get(documentId=doc_id).execute()
    
    # Извличане на истинското заглавие на документа
    real_title = document.get('title', 'Неозаглавен документ')
    
    text_content = ""
    
    # Документите на Google са сложен JSON с каскадни елементи. 
    # Това минава през всички параграфи и събира текста.
    for element in document.get('body', {}).get('content', []):
        if 'paragraph' in element:
            elements = element.get('paragraph').get('elements', [])
            for elem in elements:
                if 'textRun' in elem:
                    text_content += elem.get('textRun').get('content', '')
                    
    return {"title": real_title, "text": text_content.strip()}