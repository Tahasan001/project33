#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examassist.settings')
django.setup()

from documents.models import Question

print("Checking questions in database...")
questions = Question.objects.all()
print(f"Total questions: {questions.count()}")

for i, q in enumerate(questions[:5]):
    print(f"\nQuestion {i+1}:")
    print(f"Text: {q.question_text[:100]}...")
    print(f"Type: {q.qtype}")
    print(f"Options: {q.options}")
    print(f"Answer: {q.answer}")
    print("-" * 50) 