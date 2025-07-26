import os
from PyPDF2 import PdfReader
from docx import Document as DocxDocument
from PIL import Image
import pytesseract

def extract_text_from_pdf(file_path):
    text = ''
    with open(file_path, 'rb') as f:
        reader = PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() or ''
    return text

def extract_text_from_docx(file_path):
    doc = DocxDocument(file_path)
    return '\n'.join([p.text for p in doc.paragraphs])

def extract_text_from_txt(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

def extract_text_from_image(file_path):
    image = Image.open(file_path)
    return pytesseract.image_to_string(image)

def extract_text(file_path, doc_type):
    if doc_type == 'pdf':
        return extract_text_from_pdf(file_path)
    elif doc_type == 'docx':
        return extract_text_from_docx(file_path)
    elif doc_type == 'txt':
        return extract_text_from_txt(file_path)
    elif doc_type == 'img':
        return extract_text_from_image(file_path)
    else:
        return '' 