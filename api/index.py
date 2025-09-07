import os
import sys
from django.core.wsgi import get_wsgi_application

# Add the project directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examassist.settings')

# Get Django application
application = get_wsgi_application()

# Vercel expects 'handler' function
def handler(request):
    return application(request)