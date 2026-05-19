"""
Кастомная JWT-аутентификация, читающая access-токен из httpOnly cookie.
"""
from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class CookieJWTAuthentication(JWTAuthentication):
    """
    Сначала пытаемся прочитать токен из httpOnly cookie (основной путь
    при работе со SPA). Если cookie нет — fallback на стандартный заголовок
    ``Authorization: Bearer ...`` (удобно для curl/Postman/тестов).
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get(settings.AUTH_COOKIE_ACCESS)
        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token)
            except InvalidToken:
                return None
            return self.get_user(validated_token), validated_token

        return super().authenticate(request)
