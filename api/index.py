import os
import sys
import django
from django.core.handlers.wsgi import WSGIHandler
from django.core.wsgi import get_wsgi_application

# Add the project directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examassist.settings')

# Initialize Django
django.setup()

# Get Django application
application = get_wsgi_application()

def handler(request):
    return application(request)