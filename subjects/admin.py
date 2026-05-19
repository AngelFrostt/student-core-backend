from django.contrib import admin
from .models import Subject, SubjectImage, Profile 

class ImageInline(admin.TabularInline):
    model = SubjectImage
    extra = 1

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    inlines = [ImageInline]

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'avatar_url')
    search_fields = ('user__username',) 

# Register your models here.
