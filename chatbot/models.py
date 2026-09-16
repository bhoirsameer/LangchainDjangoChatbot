import uuid
from django.db import models
from django.utils import timezone


def generate_uuid_str():
    return str(uuid.uuid4())


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        """Soft delete: set is_deleted=True and deleted_at timestamp instead of purging DB row."""
        return super().update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        """Physical deletion from database if ever explicitly needed."""
        return super().delete()

    def active(self):
        return self.filter(is_deleted=False)

    def deleted(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        """Default manager queryset automatically excludes soft-deleted records."""
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def all_with_deleted(self):
        """Retrieve all records including soft-deleted items."""
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self):
        """Retrieve only soft-deleted records."""
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=True)


class ChatMessage(models.Model):
    chat_id = models.CharField(max_length=100, default=generate_uuid_str)
    username = models.CharField(max_length=50)
    role = models.CharField(max_length=20)
    content = models.TextField()
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def delete(self, using=None, keep_parents=False):
        """Soft delete a single model instance."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using)

    class Meta:
        db_table = 'chat_history'
        managed = True
        indexes = [
            models.Index(fields=['username', 'chat_id'], name='idx_chat_history_user_chat'),
        ]

    def __str__(self):
        return f"[{self.chat_id}] {self.role}: {self.content[:20]}"


class AIModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_models'
        managed = True

    def __str__(self):
        return self.name
