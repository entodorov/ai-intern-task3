import unittest
from unittest.mock import patch, MagicMock
from generate_notes import generate_structured_notes

class TestGenerateNotes(unittest.TestCase):

    # --- ТЕСТ 1: Edge Case за твърде къс текст ---
    def test_transcript_too_short(self):
        """Проверява дали скриптът спира автоматично, ако текстът е под 10 думи."""
        
        short_text = "Здравейте, това е много кратка среща. Довиждане!"
        parsed_json, raw_text = generate_structured_notes(short_text)
        
        # Очакваме JSON да е None, а съобщението за грешка да е "Твърде къс текст"
        self.assertIsNone(parsed_json)
        self.assertEqual(raw_text, "Твърде къс текст")

    # --- ТЕСТ 2: Успешно парсване и премахване на дубликати ---
    # @patch е декоратор, който "прихваща" заявката към Google и я спира.
    # Вместо нея, подава нашия mock_generate обект.
    @patch('generate_notes.client.models.generate_content')
    def test_generate_structured_notes_success_and_deduplication(self, mock_generate):
        """Проверява дали парсва JSON-а правилно и дали трие дублираните задачи."""
        
        # Създаваме "фалшив" отговор, все едно моделът на Gemini го е върнал
        mock_response = MagicMock()
        mock_response.text = '''
        ```json
        {
            "summary": "Тестово резюме",
            "action_items": [
                { "text": "Направи кафе", "owner": "Емо", "due_date": null },
                { "text": "Направи кафе", "owner": "Емо", "due_date": null },
                { "text": "Пиши код", "owner": "Емо", "due_date": "Утре" }
            ],
            "decisions": [],
            "key_takeaways": [],
            "topics": [],
            "next_steps": []
        }
        ```
        '''
        mock_generate.return_value = mock_response

        # Подаваме достатъчно дълъг фиктивен текст, за да мине първата проверка
        dummy_transcript = "Това е тестови транскрипт, който е достатъчно дълъг, за да премине първоначалната проверка на скрипта ни без проблем и да стигне до същината."
        parsed_json, raw_text = generate_structured_notes(dummy_transcript)

        # ПРОВЕРКА 1: Успешно ли е прочел резюмето въпреки Markdown таговете (```json)?
        self.assertIsNotNone(parsed_json)
        self.assertEqual(parsed_json["summary"], "Тестово резюме")
        
        # ПРОВЕРКА 2: Edge case - Премахване на дубликати
        # Във фалшивия JSON сложихме 3 задачи, но 2 са абсолютно еднакви ("Направи кафе").
        # Нашият код трябва да е премахнал дубликата и да са останали само 2!
        self.assertEqual(len(parsed_json["action_items"]), 2)
        self.assertEqual(parsed_json["action_items"][0]["text"], "Направи кафе")
        self.assertEqual(parsed_json["action_items"][1]["text"], "Пиши код")
        
        print("\n✅ Всички тестове за генерацията на бележки минаха успешно!")

if __name__ == '__main__':
    unittest.main()