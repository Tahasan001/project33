from django.contrib import admin
from .models import Document, Summary, Question, Flashcard, Progress, RoutineEvent

# Register your models here.

admin.site.register(Document)
admin.site.register(Summary)
admin.site.register(Question)
admin.site.register(Flashcard)
admin.site.register(Progress)
admin.site.register(RoutineEvent)
