"""DRF views for Student Core."""
import json
import logging
from datetime import date as date_type, datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from rest_framework import generics, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .auth_views import _clear_auth_cookies
from .models import Profile, Subject, Task, Resource
from .serializers import (
    RegisterSerializer, ProfileSerializer, SubjectSerializer,
    TaskSerializer, ResourceSerializer,
)


logger = logging.getLogger(__name__)
User = get_user_model()


# --- Helpers ---------------------------------------------------------------

def calculate_auto_priority(task: Task) -> str:
    days_left = (task.deadline - timezone.now()).days
    if days_left <= 3:
        return 'high'
    if days_left <= 7:
        return 'medium'
    return 'low'


def send_task_reminder(task: Task) -> None:
    if task.reminder_sent or not task.reminder_days_before:
        return
    if not task.user.email:
        return
    if not getattr(task.user.profile, 'email_notifications_enabled', True):
        return

    remind_at = task.deadline - timedelta(days=task.reminder_days_before)
    if timezone.now() < remind_at:
        return

    try:
        send_mail(
            subject=f'Напоминание: задача "{task.title}"',
            message=(
                f'Здравствуйте!\n\n'
                f'Срок выполнения задачи "{task.title}" истекает '
                f'{timezone.localtime(task.deadline).strftime("%d.%m.%Y %H:%M")}.\n\n'
                f'Проверьте свой список дел.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[task.user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Не удалось отправить напоминание о задаче %s', task.pk)
        return

    task.reminder_sent = True
    task.save(update_fields=['reminder_sent'])


def _parse_resources_payload(raw):
    """Распарсить JSON-список ресурсов из FormData либо JSON-тела."""
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return []


def _flatten_multivalue(data):
    """QueryDict → dict, без потери массивов там, где они нужны."""
    flat = {}
    for key in data:
        values = data.getlist(key) if hasattr(data, 'getlist') else [data[key]]
        flat[key] = values[0] if len(values) == 1 else values
    return flat


# --- Auth ------------------------------------------------------------------

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        if user.email:
            try:
                send_mail(
                    subject='Добро пожаловать в Student Core!',
                    message=(
                        f'Привет, {user.username}!\n\n'
                        f'Спасибо за регистрацию. Желаем продуктивной учёбы!'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception:
                logger.exception('Welcome email failed for user %s', user.pk)

        return Response(
            {'username': user.username, 'email': user.email},
            status=status.HTTP_201_CREATED,
        )


# --- Profile ---------------------------------------------------------------

class ProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']  # list/retrieve/me/etc.

    def get_queryset(self):
        return Profile.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get', 'patch'], url_path='me')
    def me(self, request):
        profile = request.user.profile
        if request.method == 'GET':
            return Response(self.get_serializer(profile).data)
        serializer = self.get_serializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='change_password')
    def change_password(self, request):
        user = request.user
        old_password = request.data.get('old_password') or ''
        new_password = request.data.get('new_password') or ''

        if not old_password or not new_password:
            return Response(
                {'detail': 'Укажите старый и новый пароль'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.check_password(old_password):
            return Response(
                {'detail': 'Неверный старый пароль'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            return Response(
                {'detail': list(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=['password'])

        # Принудительный логаут на всех устройствах
        response = Response({'detail': 'Пароль изменён'})
        _clear_auth_cookies(response)
        return response

    @action(detail=False, methods=['post'], url_path='delete_account')
    def delete_account(self, request):
        user = request.user
        password = request.data.get('password') or ''
        if not password or not user.check_password(password):
            return Response(
                {'detail': 'Неверный пароль'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.delete()
        response = Response({'detail': 'Аккаунт удалён'})
        _clear_auth_cookies(response)
        return response


# --- Subjects --------------------------------------------------------------

class SubjectViewSet(viewsets.ModelViewSet):
    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subject.objects.filter(owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


# --- Tasks -----------------------------------------------------------------

class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Task.objects.filter(user=user).select_related('subject').prefetch_related('resources')

        status_param = self.request.query_params.get('status')
        if status_param == 'active':
            qs = qs.exclude(progress_status='completed')
        elif status_param == 'completed':
            qs = qs.filter(progress_status='completed')

        deadline_date = self.request.query_params.get('date')
        if deadline_date:
            qs = qs.filter(deadline__date=deadline_date)

        return qs.order_by('deadline')

    # --- create/update: единая логика обработки ресурсов и файлов ----------

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        data = _flatten_multivalue(request.data)
        resources_raw = data.pop('resources', None)
        files = request.FILES.getlist('files') if hasattr(request.FILES, 'getlist') else []

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(user=request.user)

        self._apply_resources(task, _parse_resources_payload(resources_raw), files, replace=False)
        self._post_save(task)

        # перечитываем со всеми ресурсами
        out = self.get_serializer(task)
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        data = _flatten_multivalue(request.data)
        resources_raw = data.pop('resources', None)
        replace_resources = data.pop('replace_resources', 'true')
        files = request.FILES.getlist('files') if hasattr(request.FILES, 'getlist') else []

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        task = serializer.save()

        if resources_raw is not None or files:
            self._apply_resources(
                task,
                _parse_resources_payload(resources_raw),
                files,
                replace=str(replace_resources).lower() in ('1', 'true', 'yes'),
            )
        self._post_save(task)

        out = self.get_serializer(task)
        return Response(out.data)

    def perform_destroy(self, instance):
        instance.delete()

    # --- internals ---------------------------------------------------------

    def _apply_resources(self, task, resources_list, files, *, replace: bool) -> None:
        if replace:
            # Удаляем только те, что заменяются (links + uploaded files).
            task.resources.all().delete()

        for res in resources_list:
            if not isinstance(res, dict):
                continue
            rtype = res.get('resource_type') or 'link'
            url = (res.get('url') or '').strip()
            name = (res.get('name') or '').strip()[:200]
            if rtype == 'link' and not url:
                continue
            Resource.objects.create(
                task=task, resource_type=rtype, name=name, url=url,
            )

        for f in files:
            Resource.objects.create(
                task=task, resource_type='file', file=f, name=f.name[:200],
            )

    def _post_save(self, task) -> None:
        if task.auto_priority:
            new_priority = calculate_auto_priority(task)
            if task.priority != new_priority:
                task.priority = new_priority
                task.save(update_fields=['priority'])
        send_task_reminder(task)


# --- Resources -------------------------------------------------------------

class ResourceViewSet(viewsets.ModelViewSet):
    serializer_class = ResourceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Resource.objects.filter(task__user=self.request.user)

    def perform_create(self, serializer):
        task = serializer.validated_data.get('task')
        if task is None or task.user_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Нельзя добавлять ресурсы к чужой задаче')
        serializer.save()


# --- Analytics -------------------------------------------------------------

class AnalyticsViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='weekly')
    def weekly(self, request):
        user = request.user
        today = timezone.localdate()
        start_of_week = today - timedelta(days=today.weekday())  # понедельник
        days = [start_of_week + timedelta(days=i) for i in range(7)]
        end_of_week = days[-1]

        # Группируем агрегаты за один запрос на каждый из двух наборов.
        tz = timezone.get_current_timezone()
        start_dt = datetime.combine(start_of_week, time.min).replace(tzinfo=tz)
        end_dt = datetime.combine(end_of_week, time.max).replace(tzinfo=tz)

        planned_by_day = dict(
            Task.objects.filter(user=user, deadline__range=(start_dt, end_dt))
            .exclude(progress_status='completed')
            .annotate(d=TruncDate('deadline'))
            .values('d')
            .annotate(c=Count('id'))
            .values_list('d', 'c')
        )
        completed_by_day = dict(
            Task.objects.filter(
                user=user,
                progress_status='completed',
                completed_at__range=(start_dt, end_dt),
            )
            .annotate(d=TruncDate('completed_at'))
            .values('d')
            .annotate(c=Count('id'))
            .values_list('d', 'c')
        )

        result = []
        for day in days:
            planned = planned_by_day.get(day, 0)
            completed = completed_by_day.get(day, 0)
            efficiency = round((completed / planned) * 100, 1) if planned > 0 else 0
            result.append({
                'day_name': day.strftime('%a'),
                'day_number': day.day,
                'month': day.strftime('%b'),
                'planned_count': planned,
                'completed_count': completed,
                'efficiency': efficiency,
                'date': day.isoformat(),
            })

        return Response({'days': result})

    @action(detail=False, methods=['get'], url_path='monthly')
    def monthly_efficiency(self, request):
        user = request.user
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end_of_month = start_of_month.replace(year=now.year + 1, month=1)
        else:
            end_of_month = start_of_month.replace(month=now.month + 1)

        agg = Task.objects.filter(
            user=user,
            deadline__gte=start_of_month,
            deadline__lt=end_of_month,
        ).aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(progress_status='completed')),
        )
        total = agg['total'] or 0
        completed = agg['completed'] or 0

        if total == 0:
            efficiency = 0
            message = 'Нет задач на месяц'
        else:
            efficiency = round((completed / total) * 100, 1)
            if efficiency >= 90:
                message = 'Отлично!'
            elif efficiency >= 70:
                message = 'Хорошо!'
            elif efficiency >= 50:
                message = 'Можно лучше'
            else:
                message = 'Ставь реальные цели'

        return Response({
            'efficiency': efficiency,
            'message': message,
            'total': total,
            'completed': completed,
        })

    @action(detail=False, methods=['get'], url_path='in-progress-count')
    def in_progress_count(self, request):
        count = Task.objects.filter(
            user=request.user, progress_status='in_progress'
        ).count()
        return Response({'count': count})
