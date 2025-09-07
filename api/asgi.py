import os
import sys
from django.core.asgi import get_asgi_application

# Add the project directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examassist.settings')

# Get Django ASGI application
application = get_asgi_application()

def handler(request):
    return application(request)
