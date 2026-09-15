from django.db import models


class ChatMessage(models.Model):
    chat_id = models.CharField(max_length=100)
    username = models.CharField(max_length=50)
    role = models.CharField(max_length=20)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

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
