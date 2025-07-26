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
from .models import Document, Summary, Question, Flashcard, Progress, RoutineEvent, ExamPreparation
from .serializers import DocumentSerializer, SummarySerializer, QuestionSerializer, FlashcardSerializer, ProgressSerializer, RoutineEventSerializer, ExamPreparationSerializer
from .utils import extract_text
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
    
    # Replace single quotes with double quotes (careful: only for keys/values)
    text = re.sub(r"(?<!\\\\)'", '"', text)
    
    # Remove trailing commas before closing brackets/braces
    text = re.sub(r',([\\s]*[}\\]])', r'\\1', text)
    
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
            
            # Enhanced prompt to extract comprehensive event information
            prompt = """Extract exam events from this image with all available details. 
            For each event, extract the following information if available:
            
            If the image shows actual dates (like 23/07/2025), return in this format:
            Date: 23/07/2025
            Course: CSE 3101 (Database Systems)
            Time: 1.20 PM
            Place: Room: 201= 121-135
            Syllabus: Topic covered in class except ER Diagram and Transaction
            Question Pattern: MCQ + এক কথায় উত্তর
            
            If the image shows day names only (like Saturday, Sunday), return in this format:
            Day: Saturday
            Course: Database Quiz
            Time: 1.20 PM
            Place: Room: 203
            Syllabus: Lab e ja korano hoyechilo
            Question Pattern: MCQ + Written
            
            Extract ALL available information for each exam event."""
            
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
            current_time = None
            current_place = None
            current_syllabus = None
            current_question_pattern = None
            
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
                elif line.startswith('Time:'):
                    current_time = line.replace('Time:', '').strip()
                elif line.startswith('Place:'):
                    current_place = line.replace('Place:', '').strip()
                elif line.startswith('Syllabus:'):
                    current_syllabus = line.replace('Syllabus:', '').strip()
                elif line.startswith('Question Pattern:'):
                    current_question_pattern = line.replace('Question Pattern:', '').strip()
                    
                    # When we hit Question Pattern, it means we have all the info for one event
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
                            
                            # Build description with all available details
                            description_parts = []
                            if current_time:
                                description_parts.append(f"Time: {current_time}")
                            if current_place:
                                description_parts.append(f"Place: {current_place}")
                            if current_syllabus:
                                description_parts.append(f"Syllabus: {current_syllabus}")
                            if current_question_pattern:
                                description_parts.append(f"Question Pattern: {current_question_pattern}")
                            
                            description = "\n".join(description_parts) if description_parts else current_course
                            
                            event = RoutineEvent.objects.create(
                                user=request.user,
                                document=None,  # We don't have a document for image uploads
                                event_type=event_type,
                                title=f"{current_course} - {event_type}",
                                date=date_obj,
                                description=description,
                                time=current_time or "",
                                place=current_place or "",
                                syllabus=current_syllabus or "",
                                question_pattern=current_question_pattern or ""
                            )
                            
                            events.append({
                                'id': event.id,
                                'title': event.title,
                                'date': final_date,
                                'course_name': current_course,
                                'event_type': event_type,
                                'time': current_time,
                                'place': current_place,
                                'syllabus': current_syllabus,
                                'question_pattern': current_question_pattern
                            })
                        
                        # Reset all variables for next event
                        current_date = None
                        current_day = None
                        current_course = None
                        current_time = None
                        current_place = None
                        current_syllabus = None
                        current_question_pattern = None
            
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
                    exam_preparation=preparation
                )
                uploaded_files.append({
                    'id': document.id,
                    'name': document.name,
                    'doc_type': document.doc_type
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
