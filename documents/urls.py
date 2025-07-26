from django.urls import path
from .views import DocumentUploadView, DocumentListView, DocumentDeleteView, SummarizeDocumentView, GenerateQuestionsView, GenerateFlashcardsView, ListSummariesView, ListQuestionsView, ListFlashcardsView, ProgressView, ExtractRoutineEventsView, ChatView, DashboardStatsView, ExtractEventsFromImageView, ClearEventsView, CreateExamPreparationView, ExamPreparationListAPIView, ExamPreparationDetailAPIView, ExamPreparationDocumentsView, UploadToExamPreparationView, DeleteExamPreparationView

urlpatterns = [
    path('upload/', DocumentUploadView.as_view(), name='document-upload'),
    path('list/', DocumentListView.as_view(), name='document-list'),
    path('delete/<int:pk>/', DocumentDeleteView.as_view(), name='document-delete'),
    path('summarize/<int:pk>/', SummarizeDocumentView.as_view(), name='summarize-document'),
    path('questions/<int:pk>/', GenerateQuestionsView.as_view(), name='generate-questions'),
    path('flashcards/<int:pk>/', GenerateFlashcardsView.as_view(), name='generate-flashcards'),
    path('all-questions/<int:pk>/', ListQuestionsView.as_view(), name='list-questions'),
    path('all-flashcards/<int:pk>/', ListFlashcardsView.as_view(), name='list-flashcards'),
    path('progress/<int:pk>/', ProgressView.as_view(), name='document-progress'),
    path('extract-events/', ExtractEventsFromImageView.as_view(), name='extract-events'),
    path('extract-events-document/<int:pk>/', ExtractRoutineEventsView.as_view(), name='extract-events-document'),
    path('clear-events/', ClearEventsView.as_view(), name='clear-events'),
    path('chat/', ChatView.as_view(), name='chat'),
    path('stats/', DashboardStatsView.as_view(), name='dashboard-stats'),
    path('preparations/create/', CreateExamPreparationView.as_view(), name='create-exam-preparation'),
    path('preparations/', ExamPreparationListAPIView.as_view(), name='exam-preparation-list-api'),
    path('preparations/<int:prep_id>/', ExamPreparationDetailAPIView.as_view(), name='exam-preparation-detail-api'),
    path('preparations/<int:prep_id>/documents/', ExamPreparationDocumentsView.as_view(), name='exam-preparation-documents'),
    path('preparations/<int:prep_id>/upload/', UploadToExamPreparationView.as_view(), name='upload-to-exam-preparation'),
    path('preparations/<int:prep_id>/delete/', DeleteExamPreparationView.as_view(), name='exam-preparation-delete'),
] 