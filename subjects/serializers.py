from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from .models import Profile, Subject, Task, Resource


User = get_user_model()


# --- Helpers ---------------------------------------------------------------

def _current_user(serializer):
    request = serializer.context.get('request') if hasattr(serializer, 'context') else None
    return request.user if request and request.user.is_authenticated else None


# --- Auth ------------------------------------------------------------------

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'email')

    def validate_username(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Имя пользователя не может быть пустым')
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('Пользователь с таким именем уже существует')
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if not value:
            raise serializers.ValidationError('Email обязателен')
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Пользователь с таким email уже существует')
        return value

    def validate_password(self, value):
        # Передаём «черновой» User в валидаторы, чтобы они могли проверить
        # схожесть с username/email.
        user = User(
            username=self.initial_data.get('username', ''),
            email=self.initial_data.get('email', ''),
        )
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
        )


# --- Profile ---------------------------------------------------------------

class ProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    date_joined = serializers.DateTimeField(source='user.date_joined', read_only=True)

    class Meta:
        model = Profile
        fields = [
            'id', 'username', 'email', 'avatar_url', 'status',
            'date_joined', 'email_notifications_enabled',
        ]

    def validate_avatar_url(self, value):
        # avatar_url — это просто имя файла из public/assets/avatars или
        # относительный путь, который выдаёт бэкенд. Запрещаем абсолютные
        # внешние ссылки и попытки выйти из директории.
        if not value:
            return value
        bad = ('..', '://', '\x00')
        if any(b in value for b in bad):
            raise serializers.ValidationError('Недопустимый путь к аватару')
        return value


# --- Subject ---------------------------------------------------------------

class _ExtraLinksField(serializers.JSONField):
    """Список объектов {name, url}."""
    url_validator = URLValidator(schemes=['http', 'https'])

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        if data is None or data == '':
            return []
        if not isinstance(data, list):
            raise serializers.ValidationError('extra_links должен быть массивом')
        cleaned = []
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f'extra_links[{idx}] должен быть объектом')
            name = (item.get('name') or '').strip()[:200]
            url = (item.get('url') or '').strip()
            if not url:
                raise serializers.ValidationError(f'extra_links[{idx}].url обязателен')
            try:
                self.url_validator(url)
            except DjangoValidationError:
                raise serializers.ValidationError(f'extra_links[{idx}].url не является корректным URL')
            cleaned.append({'name': name, 'url': url})
        return cleaned


class SubjectSerializer(serializers.ModelSerializer):
    extra_links = _ExtraLinksField(required=False)

    class Meta:
        model = Subject
        fields = ['id', 'name', 'teacher_name', 'extra_links', 'created_at']
        read_only_fields = ['created_at']


class SubjectSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ['id', 'name']


# --- Resource --------------------------------------------------------------

class ResourceSerializer(serializers.ModelSerializer):
    task = serializers.PrimaryKeyRelatedField(
        queryset=Task.objects.all(), required=False, write_only=True,
    )

    class Meta:
        model = Resource
        fields = ['id', 'task', 'resource_type', 'name', 'url', 'file', 'uploaded_at']
        read_only_fields = ['uploaded_at']

    def validate_task(self, value):
        user = _current_user(self)
        if user and value.user_id != user.id:
            raise serializers.ValidationError('Нельзя добавлять ресурсы к чужой задаче')
        return value

    def validate(self, attrs):
        rtype = attrs.get('resource_type') or getattr(self.instance, 'resource_type', None)
        url = attrs.get('url') or (getattr(self.instance, 'url', '') if self.instance else '')
        file = attrs.get('file') or (getattr(self.instance, 'file', None) if self.instance else None)
        if rtype == 'link':
            if not url:
                raise serializers.ValidationError({'url': 'URL обязателен для ссылки'})
            try:
                URLValidator(schemes=['http', 'https'])(url)
            except DjangoValidationError:
                raise serializers.ValidationError({'url': 'Некорректный URL'})
        elif rtype in ('file', 'photo'):
            if not file and not self.instance:
                raise serializers.ValidationError({'file': 'Файл обязателен'})
        return attrs


# --- Task ------------------------------------------------------------------

class TaskSerializer(serializers.ModelSerializer):
    subject = SubjectSimpleSerializer(read_only=True)
    subject_id = serializers.PrimaryKeyRelatedField(
        source='subject', queryset=Subject.objects.none(),
        write_only=True, required=False, allow_null=True,
    )
    resources = ResourceSerializer(many=True, read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'deadline', 'priority', 'auto_priority',
            'progress_status', 'status', 'subject', 'subject_id', 'resources',
            'reminder_sent', 'reminder_days_before', 'created_at', 'updated_at',
        ]
        read_only_fields = ['user', 'reminder_sent', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ограничиваем queryset предметами текущего пользователя —
        # защита от IDOR через subject_id.
        user = _current_user(self)
        if user is not None and 'subject_id' in self.fields:
            self.fields['subject_id'].queryset = Subject.objects.filter(owner=user)

    def get_status(self, obj):
        return 'completed' if obj.progress_status == 'completed' else 'active'

    def validate_title(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Название задачи обязательно')
        return value

    def validate_reminder_days_before(self, value):
        if value is None:
            return value
        if value < 0 or value > 90:
            raise serializers.ValidationError('Значение должно быть от 0 до 90 дней')
        return value
