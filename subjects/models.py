from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone


class Subject(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField("Название предмета", max_length=200)
    teacher_name = models.CharField("Преподаватель", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    extra_links = models.JSONField("Дополнительные ссылки", default=list, blank=True)
    difficulty = models.PositiveSmallIntegerField("Сложность (1-5)", default=3, choices=[(i, i) for i in range(1, 6)])

    def __str__(self):
        return f"{self.name} | {self.owner.username}"


class SubjectImage(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='subjects/photos/')
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Task(models.Model):
    PRIORITY_CHOICES = [
        ('high', 'Высокий'),
        ('medium', 'Средний'),
        ('low', 'Низкий'),
    ]
    PROGRESS_CHOICES = [
        ('not_started', 'Не начато'),
        ('in_progress', 'В процессе'),
        ('completed', 'Выполнено'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='tasks', null=True, blank=True)
    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    deadline = models.DateTimeField("Срок сдачи")
    priority = models.CharField("Приоритет", max_length=10, choices=PRIORITY_CHOICES, default='medium')
    auto_priority = models.BooleanField("Авто-приоритет", default=False)
    progress_status = models.CharField("Статус выполнения", max_length=20, choices=PROGRESS_CHOICES, default='not_started')
    reminder_sent = models.BooleanField("Напоминание отправлено", default=False)
    reminder_days_before = models.PositiveSmallIntegerField("Напомнить за дней", default=3, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField("Дата выполнения", null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.get_priority_display()})"


class Resource(models.Model):
    RESOURCE_TYPES = [
        ('link', 'Ссылка'),
        ('file', 'Файл'),
        ('photo', 'Фото'),
    ]
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='resources')
    resource_type = models.CharField(max_length=10, choices=RESOURCE_TYPES)
    name = models.CharField("Название", max_length=200, blank=True)
    url = models.URLField("URL / Путь к файлу", blank=True)
    file = models.FileField("Файл", upload_to='task_files/', blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_resource_type_display()}: {self.name or self.url}"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar_url = models.CharField(max_length=255, default='avatar1.png')
    status = models.CharField(max_length=100, blank=True, default='Студент Student Core')
    email_notifications_enabled = models.BooleanField("Уведомления по почте", default=True)

    def __str__(self):
        return f"Профиль: {self.user.username}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    # Профиль создаётся только при создании юзера. Лишний UPDATE на каждый
    # save User'а (как было раньше через второй сигнал) убран.
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(pre_save, sender=Task)
def set_completed_at(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = Task.objects.get(pk=instance.pk)
        except Task.DoesNotExist:
            return
        if old.progress_status != 'completed' and instance.progress_status == 'completed':
            instance.completed_at = timezone.now()
        elif old.progress_status == 'completed' and instance.progress_status != 'completed':
            instance.completed_at = None
        # Если дедлайн был сдвинут в будущее — даём напоминанию шанс
        # отправиться заново.
        if old.deadline != instance.deadline and instance.deadline > timezone.now():
            instance.reminder_sent = False
    else:
        if instance.progress_status == 'completed':
            instance.completed_at = timezone.now()