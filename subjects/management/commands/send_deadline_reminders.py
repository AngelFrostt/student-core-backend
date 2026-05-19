"""
Команда отправляет email-напоминания по задачам, у которых:
    - не выставлен флаг reminder_sent,
    - указано reminder_days_before,
    - до дедлайна осталось <= reminder_days_before дней,
    - пользователь подтвердил email-уведомления.

Запускать раз в час (cron / scheduler) — это идемпотентно, флаг reminder_sent
сбрасывается на False при сдвиге дедлайна.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import F, ExpressionWrapper, DurationField
from django.utils import timezone

from subjects.models import Task


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправляет email-напоминания о приближающихся дедлайнах'

    def handle(self, *args, **options):
        now = timezone.now()

        candidates = Task.objects.filter(
            reminder_sent=False,
            reminder_days_before__isnull=False,
            deadline__gt=now,
        ).select_related('user', 'user__profile')

        sent = 0
        for task in candidates:
            remind_at = task.deadline - timedelta(days=task.reminder_days_before)
            if now < remind_at:
                continue

            user = task.user
            if not user.email:
                continue
            if not getattr(getattr(user, 'profile', None), 'email_notifications_enabled', False):
                continue

            try:
                send_mail(
                    subject=f'Напоминание: скоро дедлайн "{task.title}"',
                    message=(
                        f'Здравствуйте!\n\n'
                        f'Задача "{task.title}" должна быть выполнена до '
                        f'{timezone.localtime(task.deadline).strftime("%d.%m.%Y %H:%M")}.\n\n'
                        f'С уважением, Student Core.'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception('Reminder email failed for task %s', task.pk)
                continue

            task.reminder_sent = True
            task.save(update_fields=['reminder_sent'])
            sent += 1

        self.stdout.write(self.style.SUCCESS(f'Отправлено напоминаний: {sent}'))
