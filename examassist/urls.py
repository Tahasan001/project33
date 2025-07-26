"""
URL configuration for examassist project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from documents.views import ExamPreparationListView, ExamPreparationDetailView, StatsView

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    else:
        return redirect('login-page')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home_redirect, name='home'),
    path('dashboard/', TemplateView.as_view(template_name='dashboard.html'), name='dashboard'),
    path('users/', include('users.urls')),
    path('documents/', include('documents.urls')),
    # Exam Preparation URLs
    path('exam-prep/', ExamPreparationListView.as_view(), name='exam-preparation-list'),
    path('exam-prep/<int:pk>/', ExamPreparationDetailView.as_view(), name='exam-preparation-detail'),
    # Stats URL
    path('stats/', StatsView.as_view(), name='stats'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
