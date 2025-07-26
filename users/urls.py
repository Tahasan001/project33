from django.urls import path
from .views import RegisterView, LoginView, LogoutView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('login-page/', LoginView.as_view(), name='login-page'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('register-page/', RegisterView.as_view(), name='register-page'),
] 