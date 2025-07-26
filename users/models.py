from django.contrib.auth.models import User
from django.db import models

# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    # Add more fields as needed (e.g., university, major, etc.)
    def __str__(self):
        return self.user.username
