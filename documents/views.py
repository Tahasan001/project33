from django.shortcuts import render, get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.http import Http404
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from datetime import date, datetime, timedelta
import json
import logging
import os
import mimetypes
from pathlib import Path
import google.generativeai as genai
from .models import Document, Summary, Question, Flashcard, Progress, RoutineEvent, ExamPreparation, QuestionAnalysis
from .serializers import DocumentSerializer, SummarySerializer, QuestionSerializer, FlashcardSerializer, ProgressSerializer, RoutineEventSerializer, ExamPreparationSerializer, QuestionAnalysisSerializer
from .utils import extract_text, format_math_and_markdown
import re
import base64
from PIL import Image
import io

logger = logging.getLogger(__name__)

def clean_json_response(text):
    """Clean Gemini response to make it valid JSON"""
    # Remove markdown code block markers
    text = text.strip()
    if text.startswith('```json'):
        text = text[7:]
    if text.startswith('```'):
        text = text[3:]
    if text.endswith('```'):
        text = text[:-3]
    
    # Remove any text before the first '[' or '{'
    first_bracket = text.find('[')
    first_brace = text.find('{')
    
    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        text = text[first_bracket:]
    elif first_brace != -1:
        text = text[first_brace:]
    
    # Remove any text after the last ']' or '}'
    last_bracket = text.rfind(']')
    last_brace = text.rfind('}')
    
    if last_bracket != -1 and (last_brace == -1 or last_bracket > last_brace):
        text = text[:last_bracket + 1]
    elif last_brace != -1:
        text = text[:last_brace + 1]
    
    # Fix the specific JSON parsing issue by properly handling quotes
    # First, let's try a different approach - find and fix malformed JSON strings
    
    # Remove any text that's not part of the JSON structure
    # Find the main JSON array/object
    if '[' in text and ']' in text:
        start = text.find('[')
        end = text.rfind(']')
        if start < end:
            text = text[start:end+1]
    
    # Fix the specific issue with improperly escaped quotes
    # The problem is that quotes inside string values are being escaped incorrectly
    def fix_json_strings(match):
        key = match.group(1)
        value = match.group(2)
        
        # Clean up the value by removing incorrect escaping
        value = value.replace('\\"', '"')  # Remove incorrect escaping
        value = value.replace('"', '\\"')  # Properly escape quotes
        value = value.replace('\n', '\\n')  # Escape newlines
        value = value.replace('\r', '\\r')  # Escape carriage returns
        value = value.replace('\t', '\\t')  # Escape tabs
        
        return f'"{key}": "{value}"'
    
    # Apply the fix to key-value pairs
    text = re.sub(r'"([^"]+)":\s*"([^"]*(?:\\.[^"]*)*)"', fix_json_strings, text)
    
    # Remove trailing commas
    text = re.sub(r',(\s*[}\]])', r'\1', text)
    
    # If the response is truncated, try to find the last complete object
    if text.count('{') > text.count('}'):
        # Find the last complete object
        brace_count = 0
        last_complete_pos = -1
        
        for i, char in enumerate(text):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    last_complete_pos = i
        
        if last_complete_pos > 0:
            # Find the start of the array
            array_start = text.find('[')
            if array_start >= 0:
                text = text[array_start:last_complete_pos + 1] + ']'
    
    return text.strip()

# Create your views here.

class DocumentUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file provided.'}, status=status.HTTP_400_BAD_REQUEST)
        ext = os.path.splitext(file.name)[1].lower()
        if ext == '.pdf':
            doc_type = 'pdf'
        elif ext == '.docx':
            doc_type = 'docx'
        elif ext == '.txt':
            doc_type = 'txt'
        elif ext in ['.jpg', '.jpeg', '.png']:
            doc_type = 'img'
        else:
            return Response({'error': 'Unsupported file type.'}, status=status.HTTP_400_BAD_REQUEST)
        document = Document.objects.create(
            user=request.user,
            file=file,
            name=file.name,
            doc_type=doc_type
        )
        return Response({'message': 'File uploaded successfully.', 'id': document.id}, status=status.HTTP_201_CREATED)

class DocumentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        documents = Document.objects.filter(user=request.user)
        data = [
            {
                'id': doc.id,
                'name': doc.name,
                'doc_type': doc.doc_type,
                'uploaded_at': doc.uploaded_at,
                'processed': doc.processed,
                'file_url': doc.file.url
            } for doc in documents
        ]
        return Response(data)

class DocumentDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
            document.file.delete()
            document.delete()
            return Response({'message': 'Document deleted.'})
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)

class SummarizeDocumentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        file_path = document.file.path
        text = extract_text(file_path, document.doc_type)
        if not text.strip():
            return Response({'error': 'No text extracted from document.'}, status=status.HTTP_400_BAD_REQUEST)
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"Summarize the following document for a university student:\n\n{text[:8000]}"
        response = model.generate_content(prompt)
        summary = response.text
        return Response({'summary': summary})

class GenerateQuestionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        file_path = document.file.path
        text = extract_text(file_path, document.doc_type)
        if not text.strip():
            return Response({'error': 'No text extracted from document.'}, status=status.HTTP_400_BAD_REQUEST)

        # Delete previous questions for this document/user
        Question.objects.filter(document=document, user=request.user).delete()

        qtype = request.data.get('qtype', 'mcq')
        difficulty = request.data.get('difficulty', 'medium')

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            logger.error("GEMINI_API_KEY is not set in settings")
            return Response({'error': 'Gemini API key not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Try primary generation first
        created = self.try_generate_questions(model, text, qtype, document, request.user, difficulty)
        
        # If primary failed, try with smaller text
        if not created:
            created = self.try_generate_with_smaller_text(model, text, qtype, document, request.user, difficulty)
        
        # If still failed, create basic questions
        if not created:
            created = self.create_basic_questions(text, document, request.user, qtype, difficulty)
        
        if created:
            return Response({
                'success': True,
                'message': f'Generated {len(created)} questions successfully.',
                'questions': [{'id': q.id, 'question': q.question_text, 'answer': q.answer} for q in created]
            })
        else:
            return Response({'error': 'Failed to generate questions. Please try again.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def try_generate_questions(self, model, text, qtype, document, user, difficulty):
        """Try to generate questions with full text"""
        try:
            # Limit text to 3000 characters
            limited_text = text[:3000] if len(text) > 3000 else text
            
            if qtype == 'mcq':
                prompt = f'''Generate 8 multiple-choice questions from this text. Return ONLY valid JSON array:
[{{"question": "Question text", "options": ["A", "B", "C", "D"], "answer": "correct option"}}]

Text: {limited_text}'''
            elif qtype == 'fill':
                prompt = f'''Generate 8 fill-in-the-blank questions. Return ONLY valid JSON array:
[{{"question": "Question with ___ blank", "answer": "answer"}}]

Text: {limited_text}'''
            else:
                prompt = f'''Generate 8 short answer questions. Return ONLY valid JSON array:
[{{"question": "Question text", "answer": "answer"}}]

Text: {limited_text}'''
            
            response = model.generate_content(prompt)
            logger.info(f"Gemini response received: {len(response.text)} characters")
            
            # Clean the response before parsing
            cleaned_response = clean_json_response(response.text)
            logger.info(f"Cleaned response length: {len(cleaned_response)} characters")
            
            try:
                questions_data = json.loads(cleaned_response)
                logger.info(f"Successfully parsed JSON with {len(questions_data)} questions")
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing failed: {str(e)}")
                # Try to extract partial questions using regex
                questions_data = self.extract_questions_from_text(cleaned_response, qtype)
            
            return self.create_question_objects(questions_data, document, user, qtype, difficulty)
            
        except Exception as e:
            logger.error(f"Primary generation failed: {str(e)}")
            return []

    def try_generate_with_smaller_text(self, model, text, qtype, document, user, difficulty):
        """Try generating questions with a smaller text chunk"""
        try:
            # Use only first 1500 characters
            smaller_text = text[:1500]
            
            if qtype == 'mcq':
                prompt = f'''Generate 5 MCQ questions. Return ONLY valid JSON:
[{{"question": "Question", "options": ["A", "B", "C", "D"], "answer": "A"}}]

Text: {smaller_text}'''
            else:
                prompt = f'''Generate 5 questions. Return ONLY valid JSON:
[{{"question": "Question", "answer": "answer"}}]

Text: {smaller_text}'''
            
            response = model.generate_content(prompt)
            cleaned_response = clean_json_response(response.text)
            
            try:
                questions_data = json.loads(cleaned_response)
            except json.JSONDecodeError:
                questions_data = self.extract_questions_from_text(cleaned_response, qtype)
            
            return self.create_question_objects(questions_data, document, user, qtype, difficulty)
                
        except Exception as e:
            logger.error(f"Smaller text generation failed: {str(e)}")
            return []

    def extract_questions_from_text(self, text, qtype):
        """Extract questions from text using regex patterns"""
        try:
            if qtype == 'mcq':
                # Pattern for MCQ questions
                pattern = r'\{"question":\s*"[^"]*",\s*"options":\s*\[[^\]]*\],\s*"answer":\s*"[^"]*"\}'
            else:
                # Pattern for other question types
                pattern = r'\{"question":\s*"[^"]*",\s*"answer":\s*"[^"]*"\}'
            
            import re
            matches = re.findall(pattern, text)
            
            questions_data = []
            for match in matches:
                try:
                    question_obj = json.loads(match)
                    questions_data.append(question_obj)
                except json.JSONDecodeError:
                    continue
            
            return questions_data
        except Exception as e:
            logger.error(f"Failed to extract questions from text: {str(e)}")
            return []

    def create_question_objects(self, questions_data, document, user, qtype, difficulty):
        """Create Question objects from parsed data"""
        created = []
        for q in questions_data:
            question_text = q.get('question', '')
            answer = q.get('answer', '')
            options = q.get('options', None)
            
            if qtype == 'mcq' and not options:
                options = self.extract_options_from_text(q)
            
            if question_text and answer:
                try:
                    obj = Question.objects.create(
                        document=document,
                        user=user,
                        question_text=question_text,
                        answer=answer,
                        qtype=qtype,
                        difficulty=difficulty,
                        options=options
                    )
                    created.append(obj)
                except Exception as e:
                    logger.error(f"Failed to create question object: {str(e)}")
                    continue
        
        return created

    def create_basic_questions(self, text, document, user, qtype, difficulty):
        """Create basic questions when AI generation fails"""
        try:
            # Create 3 basic questions
            basic_questions = [
                {
                    'question': 'What is the main topic of this document?',
                    'answer': 'The document covers study materials and educational content.',
                    'options': ['Study materials', 'Entertainment', 'Sports', 'Politics'] if qtype == 'mcq' else None
                },
                {
                    'question': 'What type of document is this?',
                    'answer': 'This appears to be an educational or study document.',
                    'options': ['Educational', 'Fictional', 'Technical', 'Historical'] if qtype == 'mcq' else None
                },
                {
                    'question': 'What would be the best way to study this material?',
                    'answer': 'Review the content multiple times and create summaries.',
                    'options': ['Read once', 'Review multiple times', 'Skip it', 'Memorize only'] if qtype == 'mcq' else None
                }
            ]
            
            return self.create_question_objects(basic_questions, document, user, qtype, difficulty)
            
        except Exception as e:
            logger.error(f"Failed to create basic questions: {str(e)}")
            return []

    def extract_options_from_text(self, q):
        """Try to extract options from the question text or answer text"""
        options = []
        
        # Look for options in the answer field
        answer_text = q.get('answer', '')
        # Try different patterns for options
        patterns = [
            r'[A-D][).\-:]\s*([^\n]+)',  # A) option text
            r'[A-D]\.\s*([^\n]+)',       # A. option text
            r'[A-D]\s*[).\-:]\s*([^\n]+)', # A ) option text
        ]
        
        for pattern in patterns:
            match = re.findall(pattern, answer_text)
            if match:
                options = [opt.strip() for opt in match]
                break
        
        # Look for options in the question text if not found in answer
        if not options:
            qtext = q.get('question', '')
            for pattern in patterns:
                match = re.findall(pattern, qtext)
                if match:
                    options = [opt.strip() for opt in match]
                    break
        
        # If still not found, look for lines starting with A, B, C, D
        if not options:
            for line in answer_text.split('\n') + q.get('question', '').split('\n'):
                line = line.strip()
                if re.match(r'^[A-D][).\-:\.]', line):
                    # Extract text after the option letter
                    option_text = re.sub(r'^[A-D][).\-:\.]\s*', '', line)
                    if option_text.strip():
                        options.append(option_text.strip())
        
        # If we still don't have options, create some default ones
        if not options:
            options = [
                "Option A",
                "Option B", 
                "Option C",
                "Option D"
            ]
        
        return options if len(options) >= 2 else None



class GenerateFlashcardsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        file_path = document.file.path
        text = extract_text(file_path, document.doc_type)
        if not text.strip():
            return Response({'error': 'No text extracted from document.'}, status=status.HTTP_400_BAD_REQUEST)

        # Delete previous flashcards for this document/user
        Flashcard.objects.filter(document=document, user=request.user).delete()

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            logger.error("GEMINI_API_KEY is not set in settings")
            return Response({'error': 'Gemini API key not configured.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Limit text to 3000 characters to avoid truncation
        limited_text = text[:3000] if len(text) > 3000 else text
        
        prompt = f'''Create 8 flashcards from this text. Return ONLY valid JSON array:
[{{"term": "Term", "definition": "Definition"}}]

Text: {limited_text}'''
        
        try:
            response = model.generate_content(prompt)
            logger.info(f"Gemini response received: {len(response.text)} characters")
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            return Response({'error': f'Gemini API error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Clean the response before parsing
        cleaned_response = clean_json_response(response.text)
        logger.info(f"Cleaned response length: {len(cleaned_response)} characters")
        
        try:
            flashcards_data = json.loads(cleaned_response)
            logger.info(f"Successfully parsed JSON with {len(flashcards_data)} flashcards")
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed: {str(e)}")
            logger.error(f"Raw response: {response.text[:500]}...")
            
            # Try to extract partial flashcards using regex
            try:
                pattern = r'\{"term":\s*"[^"]*",\s*"definition":\s*"[^"]*"\}'
                import re
                matches = re.findall(pattern, cleaned_response)
                
                if matches:
                    logger.info(f"Found {len(matches)} complete flashcard objects in response")
                    flashcards_data = []
                    for match in matches:
                        try:
                            flashcard_obj = json.loads(match)
                            flashcards_data.append(flashcard_obj)
                        except json.JSONDecodeError:
                            continue
                    
                    if not flashcards_data:
                        # If still no flashcards, try with even smaller text
                        return self.generate_with_smaller_text(model, text, document, request.user)
                else:
                    # If no matches found, try with smaller text
                    return self.generate_with_smaller_text(model, text, document, request.user)
                    
            except Exception as extraction_error:
                logger.error(f"Failed to extract partial flashcards: {str(extraction_error)}")
                return self.generate_with_smaller_text(model, text, document, request.user)
        
        created = []
        for f in flashcards_data:
            term = f.get('term', '')
            definition = f.get('definition', '')
            
            if term and definition:
                obj = Flashcard.objects.create(
                    document=document,
                    user=request.user,
                    term=term,
                    definition=definition,
                    status='new'
                )
                created.append(obj)
        
        if created:
            return Response({
                'success': True,
                'message': f'Generated {len(created)} flashcards successfully.',
                'flashcards': [{'id': f.id, 'term': f.term, 'definition': f.definition} for f in created]
            })
        else:
            return Response({'error': 'No valid flashcards could be generated from the document.'}, status=status.HTTP_400_BAD_REQUEST)

    def generate_with_smaller_text(self, model, text, document, user):
        """Try generating flashcards with a smaller text chunk"""
        try:
            # Use only first 1500 characters
            smaller_text = text[:1500]
            
            prompt = f'''Create 5 flashcards. Return ONLY valid JSON:
[{{"term": "Term", "definition": "Definition"}}]

Text: {smaller_text}'''
            
            response = model.generate_content(prompt)
            cleaned_response = clean_json_response(response.text)
            
            try:
                flashcards_data = json.loads(cleaned_response)
            except json.JSONDecodeError:
                # If JSON parsing fails, try to extract with regex
                pattern = r'\{"term":\s*"[^"]*",\s*"definition":\s*"[^"]*"\}'
                import re
                matches = re.findall(pattern, cleaned_response)
                
                flashcards_data = []
                for match in matches:
                    try:
                        flashcard_obj = json.loads(match)
                        flashcards_data.append(flashcard_obj)
                    except json.JSONDecodeError:
                        continue
            
            created = []
            for f in flashcards_data:
                term = f.get('term', '')
                definition = f.get('definition', '')
                
                if term and definition:
                    obj = Flashcard.objects.create(
                        document=document,
                        user=user,
                        term=term,
                        definition=definition,
                        status='new'
                    )
                    created.append(obj)
            
            if created:
                return Response({
                    'success': True,
                    'message': f'Generated {len(created)} flashcards with smaller text chunk.',
                    'flashcards': [{'id': f.id, 'term': f.term, 'definition': f.definition} for f in created]
                })
            else:
                # Final fallback - create basic flashcards from text
                return self.create_basic_flashcards(text, document, user)
                
        except Exception as e:
            logger.error(f"Failed to generate flashcards with smaller text: {str(e)}")
            # Final fallback - create basic flashcards from text
            return self.create_basic_flashcards(text, document, user)

    def create_basic_flashcards(self, text, document, user):
        """Create basic flashcards from text when AI generation fails"""
        try:
            # Split text into sentences and create basic flashcards
            sentences = text.split('.')
            flashcards = []
            
            for i, sentence in enumerate(sentences[:5]):  # Take first 5 sentences
                sentence = sentence.strip()
                if len(sentence) > 10:  # Only use meaningful sentences
                    # Extract key terms (words that might be important)
                    words = sentence.split()
                    key_terms = [word for word in words if len(word) > 4 and word.isalpha()]
                    
                    if key_terms:
                        term = key_terms[0].title()  # Use first long word as term
                        definition = sentence[:100] + "..." if len(sentence) > 100 else sentence
                        
                        obj = Flashcard.objects.create(
                            document=document,
                            user=user,
                            term=term,
                            definition=definition,
                            status='new'
                        )
                        flashcards.append(obj)
            
            if flashcards:
                return Response({
                    'success': True,
                    'message': f'Generated {len(flashcards)} basic flashcards from document content.',
                    'flashcards': [{'id': f.id, 'term': f.term, 'definition': f.definition} for f in flashcards]
                })
            else:
                return Response({'error': 'Could not generate any flashcards from the document.'}, status=500)
                
        except Exception as e:
            logger.error(f"Failed to create basic flashcards: {str(e)}")
            return Response({'error': 'Failed to generate flashcards. Please try again.'}, status=500)

#upconig
class ExtractRoutineEventsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        file_path = document.file.path
        text = extract_text(file_path, document.doc_type)
        if not text.strip():
            return Response({'error': 'No text extracted from document.'}, status=status.HTTP_400_BAD_REQUEST)
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = (
            "Extract all upcoming CT, final exams, and assignment events with their dates from the following university routine or syllabus. "
            "Return the result as a JSON array: [{'event_type': 'CT', 'title': '...', 'date': 'YYYY-MM-DD', 'description': '...'}, ...].\n\n" + text[:8000]
        )
        response = model.generate_content(prompt)
        import json
        try:
            events_data = json.loads(response.text)
        except Exception:
            return Response({'error': 'Failed to parse events from Gemini response.', 'raw': response.text}, status=500)
        created = []
        for ev in events_data:
            obj = RoutineEvent.objects.create(
                user=request.user,
                document=document,
                event_type=ev.get('event_type', ''),
                title=ev.get('title', ''),
                date=ev.get('date', None),
                description=ev.get('description', '')
            )
            created.append(RoutineEventSerializer(obj).data)
        return Response({'events': created})

class ExtractEventsFromImageView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    """Extract exam/CT events from uploaded schedule images"""
    
    def calculate_date_from_day(self, day_name, current_date=None):
        """Calculate the actual date based on day name and current date"""
        if current_date is None:
            # Use July 2025 as the base date since the image shows "JUL 17"
            # Calculate a future date in 2025
            today = date.today()
            base_date = date(2025, 7, 17)  # July 17, 2025
            
            # If the base date is in the past, use next week's date
            if base_date < today:
                # Find the next occurrence of July 17th in 2025
                # Since we're in July 2025, we'll use the current week + 1 week
                days_since_july_17 = (today - base_date).days
                weeks_to_add = (days_since_july_17 // 7) + 1
                base_date = base_date + timedelta(weeks=weeks_to_add)
            
            current_date = base_date
        
        # Day name to weekday number mapping
        day_mapping = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        day_name_lower = day_name.lower().strip()
        if day_name_lower not in day_mapping:
            return None
        
        target_weekday = day_mapping[day_name_lower]
        current_weekday = current_date.weekday()
        
        # Calculate days to add to get to the target day
        days_to_add = (target_weekday - current_weekday) % 7
        
        # If it's the same day, move to next week
        if days_to_add == 0:
            days_to_add = 7
            
        calculated_date = current_date + timedelta(days=days_to_add)
        
        # Ensure the calculated date is in the future
        today = date.today()
        if calculated_date < today:
            # Add 7 more days to move to next week
            calculated_date += timedelta(days=7)
        
        return calculated_date
    
    def post(self, request):
        # Get the uploaded image
        image_file = request.FILES.get('image')
        if not image_file:
            return Response({'error': 'No image file provided'}, status=400)
        
        try:
            # Read and encode image
            image_data = image_file.read()
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # Configure Gemini
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            # Enhanced prompt to extract both types of information
            prompt = """Extract exam events from this image. 
            If the image shows actual dates (like 23/07/2025), return in this format:
            Date: 23/07/2025
            Course: CSE 3101 (Database Systems)
            
            If the image shows day names only (like Saturday, Sunday), return in this format:
            Day: Saturday
            Course: Database Quiz
            
            Extract the date or day name and course name for each exam."""
            
            response = model.generate_content([prompt, {"mime_type": image_file.content_type, "data": image_base64}])
            
            if not response.text:
                return Response({'error': 'No text could be extracted from the image'}, status=400)
            
            logger.info(f"Gemini response: {response.text[:200]}...")
            
            # Parse the response
            events = []
            lines = response.text.strip().split('\n')
            
            current_date = None
            current_day = None
            current_course = None
            
            for line in lines:
                line = line.strip()
                if line.startswith('Date:'):
                    date_str = line.replace('Date:', '').strip()
                    # Convert DD/MM/YYYY to YYYY-MM-DD
                    try:
                        day, month, year = date_str.split('/')
                        current_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        current_day = None  # Clear day if we have date
                    except:
                        current_date = None
                elif line.startswith('Day:'):
                    current_day = line.replace('Day:', '').strip()
                    current_date = None  # Clear date if we have day
                elif line.startswith('Course:'):
                    current_course = line.replace('Course:', '').strip()
                    
                    if current_course:
                        # Determine event type based on course name
                        course_lower = current_course.lower()
                        if 'ct' in course_lower:
                            event_type = 'CT'
                        elif 'quiz' in course_lower:
                            event_type = 'Quiz'
                        else:
                            event_type = 'Exam'
                        
                        # Calculate date based on what we have
                        final_date = None
                        if current_date:
                            # We have an actual date
                            final_date = current_date
                        elif current_day:
                            # We have a day name, calculate the date
                            calculated_date = self.calculate_date_from_day(current_day)
                            if calculated_date:
                                final_date = calculated_date.strftime('%Y-%m-%d')
                        
                        if final_date and current_course:
                            # Create event
                            # Convert string date to date object
                            from datetime import datetime
                            date_obj = datetime.strptime(final_date, '%Y-%m-%d').date()
                            
                            logger.info(f"Creating event: {current_course} - {event_type} on {final_date}")
                            logger.info(f"Date object: {date_obj}, Today: {date.today()}")
                            logger.info(f"Is upcoming: {date_obj >= date.today()}")
                            
                            event = RoutineEvent.objects.create(
                                user=request.user,
                                document=None,  # We don't have a document for image uploads
                                event_type=event_type,
                                title=f"{current_course} - {event_type}",
                                date=date_obj,
                                description=current_course
                            )
                            
                            events.append({
                                'id': event.id,
                                'title': event.title,
                                'date': final_date,
                                'course_name': current_course,
                                'event_type': event_type
                            })
                        
                        current_date = None
                        current_day = None
                        current_course = None
            
            if events:
                return Response({
                    'success': True,
                    'message': f'Successfully extracted {len(events)} CT events from the image',
                    'events': events
                })
            else:
                return Response({
                    'error': 'No CT events could be extracted from the image',
                    'extracted_text': response.text[:200]
                }, status=400)
                
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return Response({
                'error': f'Failed to process image: {str(e)}'
            }, status=500)

class ListSummariesView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        summaries = document.summaries.filter(user=request.user)
        from .serializers import SummarySerializer
        return Response({'summaries': SummarySerializer(summaries, many=True).data})

class ListQuestionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        questions = document.questions.filter(user=request.user)
        serialized_data = QuestionSerializer(questions, many=True).data
        logger.info(f"Returning {len(serialized_data)} questions")
        for i, q in enumerate(serialized_data):
            logger.info(f"Question {i+1}: {q.get('question_text', '')[:50]}...")
            logger.info(f"Options: {q.get('options', 'None')}")
        return Response({'questions': serialized_data})

class ListFlashcardsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        flashcards = document.flashcards.filter(user=request.user)
        return Response({'flashcards': FlashcardSerializer(flashcards, many=True).data})

class ProgressView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        progress, _ = Progress.objects.get_or_create(user=request.user, document=document)
        return Response({'progress': ProgressSerializer(progress).data})
    def post(self, request, pk):
        try:
            document = Document.objects.get(pk=pk, user=request.user)
        except Document.DoesNotExist:
            return Response({'error': 'Document not found.'}, status=status.HTTP_404_NOT_FOUND)
        progress, _ = Progress.objects.get_or_create(user=request.user, document=document)
        percent_complete = request.data.get('percent_complete')
        questions_attempted = request.data.get('questions_attempted')
        flashcards_reviewed = request.data.get('flashcards_reviewed')
        if percent_complete is not None:
            progress.percent_complete = percent_complete
        if questions_attempted is not None:
            progress.questions_attempted = questions_attempted
        if flashcards_reviewed is not None:
            progress.flashcards_reviewed = flashcards_reviewed
        progress.save()
        return Response({'progress': ProgressSerializer(progress).data})

class ChatView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        message = request.data.get('message', '')
        if not message:
            return Response({'success': False, 'error': 'Message is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get user's documents for context
        user_documents = Document.objects.filter(user=request.user)
        context = ""
        
        # Add document summaries as context
        for doc in user_documents[:3]:  # Limit to 3 most recent documents
            try:
                file_path = doc.file.path
                text = extract_text(file_path, doc.doc_type)
                if text.strip():
                    context += f"\nDocument '{doc.name}': {text[:1000]}...\n"
            except Exception as e:
                logger.error(f"Error extracting text from document {doc.id}: {str(e)}")
                continue
        
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        if context.strip():
            prompt = f"""You are an AI study assistant helping a university student. 
            Based on the following study materials, answer the student's question.
            
            Study Materials Context:
            {context}
            
            Student Question: {message}
            
            Provide a helpful, educational response based on the materials if relevant, 
            or general study advice if the question is not specific to the materials."""
        else:
            prompt = f"""You are an AI study assistant helping a university student. 
            The student hasn't uploaded any study materials yet, but you can still provide helpful study advice.
            
            Student Question: {message}
            
            Provide helpful, educational study advice and tips. If they ask about specific subjects or topics,
            give general guidance that would be useful for any student."""
        
        try:
            response = model.generate_content(prompt)
            return Response({'success': True, 'response': response.text})
        except Exception as e:
            logger.error(f"Chat error: {str(e)}")
            return Response({'success': False, 'error': 'Failed to generate response.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DashboardStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = request.user
        
        # Get counts
        total_docs = Document.objects.filter(user=user).count()
        total_questions = Question.objects.filter(user=user).count()
        total_flashcards = Flashcard.objects.filter(user=user).count()
        total_summaries = Summary.objects.filter(user=user).count()
        
        # Calculate average progress
        progress_objects = Progress.objects.filter(user=user)
        avg_progress = 0
        if progress_objects.exists():
            avg_progress = sum(p.percent_complete for p in progress_objects) / progress_objects.count()
        
        # Get event counts (only upcoming events)
        upcoming_events = RoutineEvent.objects.filter(user=user, date__gte=date.today())
        exam_preparations_count = ExamPreparation.objects.filter(user=user).count()
        upcoming_events_count = upcoming_events.count()
        total_exams = upcoming_events.filter(event_type='Exam').count()
        total_cts = upcoming_events.filter(event_type='CT').count()
        
        # Get recent events (upcoming only)
        upcoming_events = RoutineEvent.objects.filter(user=user, date__gte=date.today()).order_by('date')
        
        logger.info(f"DashboardStatsView - Total events for user: {RoutineEvent.objects.filter(user=user).count()}")
        logger.info(f"DashboardStatsView - Upcoming events count: {upcoming_events.count()}")
        logger.info(f"DashboardStatsView - Today's date: {date.today()}")
        
        for event in upcoming_events:
            logger.info(f"DashboardStatsView - Event: {event.title} on {event.date} (type: {event.event_type})")
        
        return Response({
            'total_documents': total_docs,
            'total_questions': total_questions,
            'total_flashcards': total_flashcards,
            'total_summaries': total_summaries,
            'average_progress': round(avg_progress, 1),
            'recent_events': RoutineEventSerializer(upcoming_events, many=True).data,
            'exam_preparations_count': exam_preparations_count,
            'upcoming_events_count': upcoming_events_count,
            'total_exams': total_exams,
            'total_cts': total_cts
        })


class ClearEventsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        try:
            # Delete all upcoming events for the current user
            deleted_count = RoutineEvent.objects.filter(
                user=request.user, 
                date__gte=date.today()
            ).delete()[0]
            
            return Response({
                'success': True,
                'message': f'Successfully cleared {deleted_count} upcoming events'
            })
        except Exception as e:
            logger.error(f"Error clearing events: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to clear events'
            }, status=500)

class CreateManualEventView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        try:
            event_type = request.data.get('event_type', '').strip()
            title = request.data.get('title', '').strip()
            event_date = request.data.get('date', '').strip()
            description = request.data.get('description', '').strip()
            
            # Validate required fields
            if not event_type:
                return Response({'error': 'Event type is required'}, status=400)
            if not title:
                return Response({'error': 'Title is required'}, status=400)
            if not event_date:
                return Response({'error': 'Date is required'}, status=400)
            
            # Parse and validate date
            try:
                from datetime import datetime
                parsed_date = datetime.strptime(event_date, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
            
            # Create the event
            event = RoutineEvent.objects.create(
                user=request.user,
                document=None,  # Manual events don't have associated documents
                event_type=event_type,
                title=title,
                date=parsed_date,
                description=description
            )
            
            return Response({
                'success': True,
                'message': 'Event created successfully',
                'event': {
                    'id': event.id,
                    'title': event.title,
                    'date': event.date.strftime('%Y-%m-%d'),
                    'event_type': event.event_type,
                    'description': event.description
                }
            })
            
        except Exception as e:
            logger.error(f"Error creating manual event: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to create event'
            }, status=500)

class DeleteIndividualEventView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def delete(self, request, event_id):
        try:
            event = RoutineEvent.objects.get(id=event_id, user=request.user)
            event_title = event.title
            event.delete()
            
            return Response({
                'success': True,
                'message': f'Event "{event_title}" deleted successfully'
            })
        except RoutineEvent.DoesNotExist:
            return Response({'error': 'Event not found'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting event: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to delete event'
            }, status=500)

class CreateExamPreparationView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        event_id = request.data.get('event_id')
        if not event_id:
            return Response({'error': 'Event ID is required'}, status=400)
        
        try:
            event = RoutineEvent.objects.get(id=event_id, user=request.user)
            
            # Check if preparation already exists
            if ExamPreparation.objects.filter(event=event, user=request.user).exists():
                prep = ExamPreparation.objects.get(event=event, user=request.user)
                return Response({
                    'success': True,
                    'preparation_id': prep.id,
                    'message': 'Preparation page already exists'
                })
            
            # Create new preparation
            prep = ExamPreparation.objects.create(
                user=request.user,
                event=event,
                title=f"{event.title} Preparation",
                description=f"Preparation materials for {event.title}"
            )
            
            return Response({
                'success': True,
                'preparation_id': prep.id,
                'message': 'Preparation page created successfully'
            })
        except RoutineEvent.DoesNotExist:
            return Response({'error': 'Event not found'}, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

class ExamPreparationListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        preparations = ExamPreparation.objects.filter(user=request.user).order_by('-updated_at')
        serializer = ExamPreparationSerializer(preparations, many=True)
        return Response(serializer.data)

class ExamPreparationDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, prep_id):
        try:
            prep = ExamPreparation.objects.get(id=prep_id, user=request.user)
            serializer = ExamPreparationSerializer(prep)
            return Response(serializer.data)
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Preparation not found'}, status=404)

class ExamPreparationDocumentsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, prep_id):
        try:
            prep = ExamPreparation.objects.get(id=prep_id, user=request.user)
            documents = Document.objects.filter(exam_preparation=prep)
            serializer = DocumentSerializer(documents, many=True)
            return Response(serializer.data)
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Preparation not found'}, status=404)

class UploadToExamPreparationView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, prep_id):
        try:
            preparation = ExamPreparation.objects.get(id=prep_id, user=request.user)
            files = request.FILES.getlist('file')
            category = request.data.get('category', 'reading')  # Default to reading material
            
            if not files:
                return Response({'error': 'No files provided'}, status=400)
            
            uploaded_files = []
            for file in files:
                # Determine document type based on file extension
                ext = os.path.splitext(file.name)[1].lower()
                if ext == '.pdf':
                    doc_type = 'pdf'
                elif ext == '.docx':
                    doc_type = 'docx'
                elif ext == '.txt':
                    doc_type = 'txt'
                elif ext in ['.jpg', '.jpeg', '.png']:
                    doc_type = 'img'
                else:
                    doc_type = 'txt'  # Default to txt for unknown types
                
                document = Document.objects.create(
                    user=request.user,
                    file=file,
                    name=file.name,
                    doc_type=doc_type,
                    category=category,
                    exam_preparation=preparation
                )
                uploaded_files.append({
                    'id': document.id,
                    'name': document.name,
                    'doc_type': document.doc_type,
                    'category': document.category
                })
            
            return Response({
                'success': True,
                'message': f'Successfully uploaded {len(uploaded_files)} files',
                'files': uploaded_files
            })
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Exam preparation not found'}, status=404)
        except Exception as e:
            logger.error(f"Upload error: {str(e)}")
            return Response({'error': 'Failed to upload files'}, status=500)


class DeleteExamPreparationView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def delete(self, request, prep_id):
        try:
            preparation = ExamPreparation.objects.get(id=prep_id, user=request.user)
            preparation.delete()
            return Response({'success': True})
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Exam preparation not found.'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting exam preparation: {str(e)}")
            return Response({'error': 'Failed to delete exam preparation.'}, status=500)

# Django Template Views
class ExamPreparationListView(LoginRequiredMixin, ListView):
    model = ExamPreparation
    template_name = 'exam_preparation_list.html'
    context_object_name = 'preparations'
    
    def get_queryset(self):
        return ExamPreparation.objects.filter(user=self.request.user).order_by('-updated_at')

class ExamPreparationDetailView(LoginRequiredMixin, DetailView):
    model = ExamPreparation
    template_name = 'exam_preparation.html'
    context_object_name = 'preparation'
    
    def get_queryset(self):
        return ExamPreparation.objects.filter(user=self.request.user)

class StatsView(LoginRequiredMixin, ListView):
    model = ExamPreparation
    template_name = 'stats.html'
    context_object_name = 'preparations'
    
    def get_queryset(self):
        return ExamPreparation.objects.filter(user=self.request.user).order_by('-updated_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Get all exam preparations for the user
        preparations = ExamPreparation.objects.filter(user=user)
        
        # Calculate comprehensive statistics
        total_preparations = preparations.count()
        total_documents = sum(prep.documents.count() for prep in preparations)
        total_questions = sum(sum(doc.questions.count() for doc in prep.documents.all()) for prep in preparations)
        total_flashcards = sum(sum(doc.flashcards.count() for doc in prep.documents.all()) for prep in preparations)
        
        # Calculate progress statistics
        total_progress_entries = Progress.objects.filter(document__exam_preparation__user=user).count()
        completed_progress = Progress.objects.filter(document__exam_preparation__user=user, percent_complete__gte=100).count()
        progress_percentage = (completed_progress / total_progress_entries * 100) if total_progress_entries > 0 else 0
        
        # Get recent activity (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_documents = Document.objects.filter(exam_preparation__user=user, uploaded_at__gte=thirty_days_ago).count()
        recent_questions = Question.objects.filter(document__exam_preparation__user=user, created_at__gte=thirty_days_ago).count()
        recent_flashcards = Flashcard.objects.filter(document__exam_preparation__user=user, created_at__gte=thirty_days_ago).count()
        
        # Calculate individual preparation statistics
        prep_stats = []
        for prep in preparations:
            prep_documents = prep.documents.count()
            prep_questions = sum(doc.questions.count() for doc in prep.documents.all())
            prep_flashcards = sum(doc.flashcards.count() for doc in prep.documents.all())
            
            # Calculate progress for this preparation
            prep_progress_entries = Progress.objects.filter(document__exam_preparation=prep).count()
            prep_completed_progress = Progress.objects.filter(document__exam_preparation=prep, percent_complete__gte=100).count()
            prep_progress_percentage = (prep_completed_progress / prep_progress_entries * 100) if prep_progress_entries > 0 else 0
            
            prep_stats.append({
                'preparation': prep,
                'documents_count': prep_documents,
                'questions_count': prep_questions,
                'flashcards_count': prep_flashcards,
                'progress_percentage': round(prep_progress_percentage, 1),
                'created_at': prep.created_at,
                'updated_at': prep.updated_at,
            })
        
        # Prepare data for charts
        chart_data = {
            'preparation_names': [prep.title for prep in preparations[:10]],  # Top 10
            'document_counts': [prep.documents.count() for prep in preparations[:10]],
            'question_counts': [sum(doc.questions.count() for doc in prep.documents.all()) for prep in preparations[:10]],
            'flashcard_counts': [sum(doc.flashcards.count() for doc in prep.documents.all()) for prep in preparations[:10]],
        }
        
        # Monthly activity data
        monthly_data = self.get_monthly_activity_data(user)
        
        context.update({
            'total_preparations': total_preparations,
            'total_documents': total_documents,
            'total_questions': total_questions,
            'total_flashcards': total_flashcards,
            'progress_percentage': round(progress_percentage, 1),
            'recent_documents': recent_documents,
            'recent_questions': recent_questions,
            'recent_flashcards': recent_flashcards,
            'chart_data': chart_data,
            'monthly_data': monthly_data,
            'prep_stats': prep_stats,
        })
        
        return context
    
    def get_monthly_activity_data(self, user):
        """Get monthly activity data for the last 6 months"""
        monthly_data = []
        for i in range(6):
            month_start = timezone.now() - timedelta(days=30*i)
            month_end = month_start + timedelta(days=30)
            
            documents_count = Document.objects.filter(
                exam_preparation__user=user,
                uploaded_at__gte=month_start,
                uploaded_at__lt=month_end
            ).count()
            
            questions_count = Question.objects.filter(
                document__exam_preparation__user=user,
                created_at__gte=month_start,
                created_at__lt=month_end
            ).count()
            
            flashcards_count = Flashcard.objects.filter(
                document__exam_preparation__user=user,
                created_at__gte=month_start,
                created_at__lt=month_end
            ).count()
            
            monthly_data.append({
                'month': month_start.strftime('%B %Y'),
                'documents': documents_count,
                'questions': questions_count,
                'flashcards': flashcards_count,
            })
        
        return list(reversed(monthly_data))  # Most recent first


class QuestionAnalysisListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, prep_id):
        try:
            preparation = ExamPreparation.objects.get(id=prep_id, user=request.user)
            analyses = QuestionAnalysis.objects.filter(exam_preparation=preparation, user=request.user).order_by('-created_at')
            serializer = QuestionAnalysisSerializer(analyses, many=True)
            return Response(serializer.data)
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Exam preparation not found'}, status=404)
        except Exception as e:
            logger.error(f"Error retrieving question analyses: {str(e)}")
            return Response({'error': 'Failed to retrieve question analyses'}, status=500)

class ViewSavedAnalysisView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, prep_id):
        """Get the most recent saved analysis response for an exam preparation"""
        try:
            preparation = ExamPreparation.objects.get(id=prep_id, user=request.user)
            analysis = QuestionAnalysis.objects.filter(
                exam_preparation=preparation, 
                user=request.user,
                full_analysis_response__isnull=False
            ).order_by('-created_at').first()
            
            if not analysis:
                return Response({'error': 'No saved analysis found'}, status=404)
            
            return Response({
                'success': True,
                'analysis_id': analysis.id,
                'formatted_response': analysis.full_analysis_response,
                'response_length': len(analysis.full_analysis_response),
                'created_at': analysis.created_at.isoformat()
            })
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Exam preparation not found'}, status=404)
        except Exception as e:
            logger.error(f"Error retrieving saved analysis: {str(e)}")
            return Response({'error': 'Failed to retrieve saved analysis'}, status=500)

class QuestionAnalysisView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, prep_id):
        """Perform question analysis with prettier formatting"""
        try:
            preparation = ExamPreparation.objects.get(id=prep_id, user=request.user)
            
            # Get reading and question documents
            reading_docs = preparation.documents.filter(category='reading')
            question_docs = preparation.documents.filter(category='questions')
            
            if not reading_docs.exists() or not question_docs.exists():
                return Response({'error': 'Both reading materials and questions are required'}, status=400)
            
            # Extract text from documents
            reading_text = ""
            for doc in reading_docs:
                try:
                    text = extract_text(doc.file.path, doc.doc_type)
                    reading_text += f"\n\n--- {doc.name} ---\n{text}"
                except Exception as e:
                    logger.error(f"Error extracting text from reading doc {doc.id}: {str(e)}")
            
            questions_text = ""
            for doc in question_docs:
                try:
                    text = extract_text(doc.file.path, doc.doc_type)
                    questions_text += f"\n\n--- {doc.name} ---\n{text}"
                except Exception as e:
                    logger.error(f"Error extracting text from question doc {doc.id}: {str(e)}")
            
            # Use AI to analyze questions and find answers from reading materials
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            prompt = f"""Analyze these exam questions and provide answers based on the study materials.

STUDY MATERIALS:
{reading_text[:8000]}

QUESTIONS:
{questions_text[:8000]}

             Your task is to:
            1. Identify questions from the previous year questions that are RELATED TO or BASED ON the concepts covered in the reading materials
            2. For each relevant question, provide a comprehensive answer using the knowledge from the reading materials
            3. Include questions that cover similar topics, concepts, or subject areas as the reading materials
            4. Use the reading materials as a foundation to explain concepts and provide detailed answers
            5. If there is any information about where the question is from like the year, question number, section, etc, include it in the response
            6. Generate at least 5-8 relevant questions if possible
            7. Focus on questions that test understanding of the same subject matter or related statistical/mathematical concepts
            8. DO NOT comment on whether the question can be solved by the study material - just provide the answer
            9. Do not derive questions from study material solve only questions of the questions document
            
            FORMAT YOUR RESPONSE AS FOLLOWS:
            
            # QUESTION ANALYSIS RESULTS
            
            ## Question 1
            Question: [Write the exact question text here] 
            [year, question number, section, etc]
            
            
            
            Answer: 
            [Provide a comprehensive, step-by-step answer based on the reading materials. Include calculations, explanations, and interpretations where applicable.]
            
            ---
            
            ## Question 2
            Question: [Write the exact question text here] 
            [year, question number, section, etc]
            
            
            
            Answer: 
            [Provide a comprehensive, step-by-step answer based on the reading materials. Include calculations, explanations, and interpretations where applicable.]
            
            ---
            
            [Continue for all relevant questions...]
            
             REQUIREMENTS: 
             - Focus on questions that are related to or based on the concepts covered in the reading materials
             - Include questions that test similar topics, statistical concepts, or mathematical principles
             - Use the reading materials as a foundation to explain and solve related questions
             - Provide detailed, comprehensive answers using knowledge from the reading materials
             - Include at least 5-8 relevant questions if possible
             - Use clear formatting with headers and separators
             - Provide step-by-step solutions for calculation problems
             - Include explanations and interpretations for statistical concepts
             - Apply concepts from the reading materials to solve related questions
             - Make the response easy to read and study from
             - Don't limit to only questions with direct answers - include questions on related topics
             - NEVER comment on whether the question can be solved by the study material
             - Just provide the answer directly without any disclaimers about material coverage
            """

            
            # Configure generation parameters for better responses
            generation_config = {
                "temperature": 0.1,  # Very low temperature for more consistent responses
                "top_p": 0.9,
                "top_k": 50,
                "max_output_tokens": 16384,  # Increased token limit for comprehensive analysis
            }
            
            try:
                response = model.generate_content(
                    prompt,
                    generation_config=generation_config
                )
                
                # Check if response is valid and has content
                if not response:
                    return Response({'error': 'AI service returned no response'}, status=500)
                
                # Handle different response types and potential errors
                if hasattr(response, 'text') and response.text:
                    response_text = response.text
                elif hasattr(response, 'parts') and response.parts:
                    # Try to extract text from parts
                    response_text = ''.join([part.text for part in response.parts if hasattr(part, 'text') and part.text])
                else:
                    # Check for finish reason errors
                    if hasattr(response, 'candidates') and response.candidates:
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'finish_reason'):
                            if candidate.finish_reason == 2:  # SAFETY
                                return Response({'error': 'AI response was blocked due to safety concerns. Please try with different content.'}, status=500)
                            elif candidate.finish_reason == 3:  # RECITATION
                                return Response({'error': 'AI response was blocked due to recitation concerns. Please try with different content.'}, status=500)
                            elif candidate.finish_reason == 4:  # OTHER
                                return Response({'error': 'AI response was blocked for other reasons. Please try again.'}, status=500)
                    
                    return Response({'error': 'AI response was empty or invalid'}, status=500)
                
                if not response_text.strip():
                    return Response({'error': 'AI response was empty'}, status=500)
                    
            except Exception as ai_error:
                logger.error(f"AI generation error: {str(ai_error)}")
                return Response({'error': f'AI generation failed: {str(ai_error)}'}, status=500)
            
            # Format the response to convert LaTeX math and markdown to proper symbols
            formatted_response = format_math_and_markdown(response_text)
            
            # Save the analysis response to database
            analysis = QuestionAnalysis.objects.create(
                exam_preparation=preparation,
                user=request.user,
                full_analysis_response=formatted_response,
                confidence_score=0.9  # High confidence for comprehensive analysis
            )
            
            # Associate related documents
            analysis.related_documents.set(list(reading_docs) + list(question_docs))
            
            return Response({
                'success': True,
                'formatted_response': formatted_response,
                'response_length': len(formatted_response),
                'analysis_id': analysis.id
            })
            
        except ExamPreparation.DoesNotExist:
            return Response({'error': 'Exam preparation not found'}, status=404)
        except Exception as e:
            logger.error(f"Raw AI response error: {str(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return Response({'error': f'Failed to get raw AI response: {str(e)}'}, status=500)
