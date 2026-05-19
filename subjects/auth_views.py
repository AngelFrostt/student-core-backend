"""
Auth views: cookie-based JWT.

Особенности:
- access/refresh JWT кладутся в httpOnly cookie;
- ответ /api/auth/login возвращает только данные пользователя, токены наружу
  не отдаются;
- /api/auth/refresh использует refresh-cookie, обновляет оба cookie;
- /api/auth/logout добавляет refresh в blacklist и стирает cookie;
- /api/auth/csrf возвращает CSRF-токен (нужен фронту для модифицирующих
  запросов) — Django сам положит cookie csrftoken, фронт прочитает его и
  отправит в заголовке X-CSRFToken.
"""
from django.conf import settings
from django.contrib.auth import authenticate
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator

from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .serializers import ProfileSerializer


def _set_auth_cookies(response, access: str, refresh: str | None = None) -> None:
    """Кладёт access/refresh JWT в httpOnly cookie."""
    access_lifetime = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
    response.set_cookie(
        settings.AUTH_COOKIE_ACCESS,
        access,
        max_age=access_lifetime,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path='/',
    )
    if refresh is not None:
        refresh_lifetime = int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds())
        response.set_cookie(
            settings.AUTH_COOKIE_REFRESH,
            refresh,
            max_age=refresh_lifetime,
            httponly=True,
            secure=settings.AUTH_COOKIE_SECURE,
            samesite=settings.AUTH_COOKIE_SAMESITE,
            domain=settings.AUTH_COOKIE_DOMAIN,
            path='/',
        )


def _clear_auth_cookies(response) -> None:
    for name in (settings.AUTH_COOKIE_ACCESS, settings.AUTH_COOKIE_REFRESH):
        response.delete_cookie(
            name,
            domain=settings.AUTH_COOKIE_DOMAIN,
            samesite=settings.AUTH_COOKIE_SAMESITE,
            path='/',
        )


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CSRFView(APIView):
    """GET /api/auth/csrf — выставляет csrftoken cookie."""
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get(self, request):
        return Response({'csrfToken': get_token(request)})


class LoginView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        if not username or not password:
            return Response(
                {'detail': 'Введите логин и пароль'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None or not user.is_active:
            return Response(
                {'detail': 'Неверный логин или пароль'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        response = Response(
            {'user': ProfileSerializer(user.profile).data},
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, str(refresh.access_token), str(refresh))
        return response


class RefreshView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        raw_refresh = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        if not raw_refresh:
            return Response(
                {'detail': 'Refresh-токен не найден'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            refresh = RefreshToken(raw_refresh)
            # Ротация: blacklist старый, выпустить новый
            new_access = str(refresh.access_token)
            new_refresh_value = None
            if settings.SIMPLE_JWT.get('ROTATE_REFRESH_TOKENS'):
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass
                new_refresh = RefreshToken.for_user_id(refresh['user_id']) \
                    if hasattr(RefreshToken, 'for_user_id') else None
                # Универсальный путь: пересоздать через user_id
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(pk=refresh['user_id'])
                new_refresh = RefreshToken.for_user(user)
                new_access = str(new_refresh.access_token)
                new_refresh_value = str(new_refresh)
        except (InvalidToken, TokenError) as exc:
            response = Response(
                {'detail': 'Refresh-токен недействителен'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_auth_cookies(response)
            return response

        response = Response({'detail': 'ok'}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, new_access, new_refresh_value)
        return response


class LogoutView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        raw_refresh = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except (InvalidToken, TokenError, AttributeError):
                pass
        response = Response({'detail': 'ok'}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class MeView(APIView):
    """GET /api/auth/me — для bootstrap-проверки на фронте."""
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return Response(ProfileSerializer(request.user.profile).data)


__all__ = [
    'CSRFView', 'LoginView', 'RefreshView', 'LogoutView', 'MeView',
    '_set_auth_cookies', '_clear_auth_cookies',
]
