import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examassist.settings')
django.setup()

from documents.models import Question
import json

print("=== CHECKING QUESTIONS IN DATABASE ===")
questions = Question.objects.all()
print(f"Total questions: {questions.count()}")

for i, q in enumerate(questions[:3]):
    print(f"\n--- Question {i+1} ---")
    print(f"Text: {q.question_text}")
    print(f"Type: {q.qtype}")
    print(f"Answer: {q.answer}")
    print(f"Options (raw): {q.options}")
    if q.options:
        try:
            options_list = json.loads(q.options) if isinstance(q.options, str) else q.options
            print(f"Options (parsed): {options_list}")
        except:
            print("Failed to parse options")
    print("-" * 50) 