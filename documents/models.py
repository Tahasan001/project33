from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class Document(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to='documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=255)
    doc_type = models.CharField(max_length=10, choices=[('pdf', 'PDF'), ('docx', 'DOCX'), ('txt', 'TXT'), ('img', 'Image')])
    processed = models.BooleanField(default=False)
    exam_preparation = models.ForeignKey('ExamPreparation', on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    # Add more fields as needed (e.g., summary, extracted_text, etc.)

    def __str__(self):
        return self.name

class Summary(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='summaries')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

class Question(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='questions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    question_text = models.TextField()
    answer = models.TextField()
    qtype = models.CharField(max_length=10, choices=[('mcq', 'MCQ'), ('fill', 'Fill'), ('open', 'Open')])
    difficulty = models.CharField(max_length=10, default='medium')
    options = models.JSONField(blank=True, null=True)  # For MCQ options
    created_at = models.DateTimeField(auto_now_add=True)
    # For MCQ: store options as JSON if needed

class Flashcard(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='flashcards')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    term = models.CharField(max_length=255)
    definition = models.TextField()
    status = models.CharField(max_length=20, default='new')  # new, learning, mastered
    created_at = models.DateTimeField(auto_now_add=True)

class Progress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    percent_complete = models.FloatField(default=0.0)
    questions_attempted = models.IntegerField(default=0)
    flashcards_reviewed = models.IntegerField(default=0)
    last_accessed = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.user.username} - {self.document.name} Progress"

class RoutineEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, null=True, blank=True)
    event_type = models.CharField(max_length=50)  # e.g., 'CT', 'Final', 'Assignment'
    title = models.CharField(max_length=255)
    date = models.DateField()
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f"{self.title} ({self.event_type}) on {self.date}"

class ExamPreparation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.ForeignKey(RoutineEvent, on_delete=models.CASCADE, related_name='preparations')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.title} - {self.user.username}"
    
    @property
    def documents_count(self):
        return self.documents.count()
    
    @property
    def total_questions(self):
        return sum(doc.questions.count() for doc in self.documents.all())
    
    @property
    def total_flashcards(self):
        return sum(doc.flashcards.count() for doc in self.documents.all())
