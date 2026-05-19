from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterView, ProfileViewSet, SubjectViewSet,
    TaskViewSet, ResourceViewSet, AnalyticsViewSet,
)
from .auth_views import CSRFView, LoginView, RefreshView, LogoutView, MeView


router = DefaultRouter()
router.register(r'profile', ProfileViewSet, basename='profile')
router.register(r'subjects', SubjectViewSet, basename='subject')
router.register(r'tasks', TaskViewSet, basename='task')
router.register(r'resources', ResourceViewSet, basename='resource')
router.register(r'analytics', AnalyticsViewSet, basename='analytics')

urlpatterns = [
    # Auth
    path('auth/csrf/', CSRFView.as_view(), name='auth_csrf'),
    path('auth/login/', LoginView.as_view(), name='auth_login'),
    path('auth/refresh/', RefreshView.as_view(), name='auth_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/me/', MeView.as_view(), name='auth_me'),
    path('auth/register/', RegisterView.as_view(), name='auth_register'),

    path('', include(router.urls)),
]
