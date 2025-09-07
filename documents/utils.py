import os
import re
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

def format_math_and_markdown(text):
    """
    Convert LaTeX math expressions and markdown formatting to proper symbols and HTML
    """
    if not text:
        return text
    
    # First, handle LaTeX expressions that might be broken across lines
    # Look for patterns like "rac{" which should be "\frac{"
    text = re.sub(r'\brac\{', r'\\frac{', text)
    
    # Remove dollar signs around math expressions to avoid conflicts
    text = re.sub(r'\$([^$]+)\$', r'\1', text)
    
    # Convert LaTeX math expressions to proper symbols
    # Handle fractions: \frac{a}{b} -> a/b
    text = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'\1/\2', text)
    
    # Handle summation: \sum -> Σ
    text = re.sub(r'\\sum', 'Σ', text)
    
    # Handle mean symbol: X̄ -> X̄ (Unicode combining overline)
    text = re.sub(r'\\bar\{X\}', 'X̄', text)
    
    # Handle other common math symbols
    text = re.sub(r'\\alpha', 'α', text)
    text = re.sub(r'\\beta', 'β', text)
    text = re.sub(r'\\gamma', 'γ', text)
    text = re.sub(r'\\delta', 'δ', text)
    text = re.sub(r'\\epsilon', 'ε', text)
    text = re.sub(r'\\theta', 'θ', text)
    text = re.sub(r'\\lambda', 'λ', text)
    text = re.sub(r'\\mu', 'μ', text)
    text = re.sub(r'\\pi', 'π', text)
    text = re.sub(r'\\sigma', 'σ', text)
    text = re.sub(r'\\tau', 'τ', text)
    text = re.sub(r'\\phi', 'φ', text)
    text = re.sub(r'\\omega', 'ω', text)
    
    # Handle superscripts: ^{2} -> ²
    text = re.sub(r'\^\{2\}', '²', text)
    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^\{3\}', '³', text)
    text = re.sub(r'\^3', '³', text)
    text = re.sub(r'\^\{n\}', 'ⁿ', text)
    text = re.sub(r'\^n', 'ⁿ', text)
    
    # Handle subscripts: _{i} -> ᵢ
    text = re.sub(r'_\{i\}', 'ᵢ', text)
    text = re.sub(r'_i', 'ᵢ', text)
    text = re.sub(r'_\{j\}', 'ⱼ', text)
    text = re.sub(r'_j', 'ⱼ', text)
    text = re.sub(r'_\{n\}', 'ₙ', text)
    text = re.sub(r'_n', 'ₙ', text)
    
    # Handle square root: \sqrt{x} -> √x
    text = re.sub(r'\\sqrt\{([^}]+)\}', r'√\1', text)
    
    # Handle infinity: \infty -> ∞
    text = re.sub(r'\\infty', '∞', text)
    
    # Handle plus-minus: \pm -> ±
    text = re.sub(r'\\pm', '±', text)
    
    # Handle not equal: \neq -> ≠
    text = re.sub(r'\\neq', '≠', text)
    
    # Handle less than or equal: \leq -> ≤
    text = re.sub(r'\\leq', '≤', text)
    
    # Handle greater than or equal: \geq -> ≥
    text = re.sub(r'\\geq', '≥', text)
    
    # Handle approximately equal: \approx -> ≈
    text = re.sub(r'\\approx', '≈', text)
    
    # Handle integral: \int -> ∫
    text = re.sub(r'\\int', '∫', text)
    
    # Handle partial derivative: \partial -> ∂
    text = re.sub(r'\\partial', '∂', text)
    
    # Handle nabla: \nabla -> ∇
    text = re.sub(r'\\nabla', '∇', text)
    
    # Convert markdown bold (**text** or *text*) to HTML bold
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*([^*]+)\*', r'<strong>\1</strong>', text)
    
    # Convert markdown italic (_text_) to HTML italic, but be careful not to affect subscripts
    # Only convert if it's not part of a math expression
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<em>\1</em>', text)
    
    # Convert markdown headers to HTML headers
    text = re.sub(r'^### (.*)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.*)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.*)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    
    # Convert line breaks to HTML breaks
    text = re.sub(r'\n\n', '<br><br>', text)
    text = re.sub(r'\n', '<br>', text)
    
    return text 