from django.db import models

class UserAccount(models.Model):
    username = models.CharField(max_length=50, unique=True)
    hashed_password = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'users'
        managed = True

    def __str__(self):
        return self.username


class UserSession(models.Model):
    session_id = models.CharField(max_length=100, primary_key=True)
    username = models.CharField(max_length=50)
    created_at = models.FloatField()
    last_rotated_at = models.FloatField()
    expires_at = models.FloatField()

    class Meta:
        db_table = 'sessions'
        managed = True

    def __str__(self):
        return f"{self.username} - {self.session_id}"

