"""URL configuration for core project."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('subjects.urls')),
]

# Раздача медиафайлов работает всегда (и на компьютере, и на сервере)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)