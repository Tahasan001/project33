from rest_framework import serializers
from .models import Document, Summary, Question, Flashcard, Progress, RoutineEvent, ExamPreparation

class DocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    
    def get_file_url(self, obj):
        return obj.file.url if obj.file else None
    
    class Meta:
        model = Document
        fields = ['id', 'name', 'doc_type', 'uploaded_at', 'processed', 'file', 'file_url', 'exam_preparation']

class SummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Summary
        fields = ['id', 'document', 'user', 'text', 'created_at']

class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = ['id', 'document', 'user', 'question_text', 'answer', 'qtype', 'difficulty', 'options', 'created_at']

class FlashcardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Flashcard
        fields = ['id', 'document', 'user', 'term', 'definition', 'status', 'created_at']

class ProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Progress
        fields = ['id', 'user', 'document', 'percent_complete', 'questions_attempted', 'flashcards_reviewed', 'last_accessed']

class RoutineEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutineEvent
        fields = ['id', 'user', 'document', 'event_type', 'title', 'date', 'description', 'time', 'place', 'syllabus', 'question_pattern', 'created_at']

class ExamPreparationSerializer(serializers.ModelSerializer):
    documents_count = serializers.ReadOnlyField()
    total_questions = serializers.ReadOnlyField()
    total_flashcards = serializers.ReadOnlyField()
    
    class Meta:
        model = ExamPreparation
        fields = ['id', 'user', 'event', 'title', 'description', 'created_at', 'updated_at', 'documents_count', 'total_questions', 'total_flashcards'] 