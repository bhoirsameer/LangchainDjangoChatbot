import time
import uuid
from django.http import JsonResponse
from api.models import UserSession

SESSION_ROTATION_INTERVAL = 15 * 60  # 15 minutes in seconds
SESSION_MAX_IDLE_TIME = 2 * 60 * 60  # 2 hours max idle time

EXEMPT_PATHS = [
    '/',
    '/api/register',
    '/register',
    '/api/login',
    '/login',
    '/admin/',
]

class SlidingSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        session_id = self._get_session_id(request)
        request.current_session = None

        is_exempt = any(path == p or path.startswith('/admin/') for p in EXEMPT_PATHS)

        if session_id:
            now = time.time()
            session = UserSession.objects.filter(session_id=session_id).first()

            if not session:
                if not is_exempt:
                    return JsonResponse(
                        {"detail": "Invalid or expired session. Please log in again."},
                        status=401
                    )
            elif now > session.expires_at:
                session.delete()
                if not is_exempt:
                    return JsonResponse(
                        {"detail": "Invalid or expired session. Please log in again."},
                        status=401
                    )
            else:
                time_since_rotation = now - session.last_rotated_at

                if time_since_rotation >= SESSION_ROTATION_INTERVAL:
                    # Perform session rotation
                    new_session_id = f"sess_{uuid.uuid4().hex}"
                    username = session.username
                    created_at = session.created_at

                    session.delete()

                    new_session = UserSession.objects.create(
                        session_id=new_session_id,
                        username=username,
                        created_at=created_at,
                        last_rotated_at=now,
                        expires_at=now + SESSION_MAX_IDLE_TIME
                    )

                    request.current_session = {
                        "session_id": new_session_id,
                        "username": username,
                        "created_at": created_at,
                        "last_rotated_at": now,
                        "was_rotated": True,
                        "seconds_until_rotation": SESSION_ROTATION_INTERVAL
                    }
                else:
                    session.expires_at = now + SESSION_MAX_IDLE_TIME
                    session.save(update_fields=['expires_at'])

                    seconds_until_rotation = max(0, int(SESSION_ROTATION_INTERVAL - time_since_rotation))

                    request.current_session = {
                        "session_id": session.session_id,
                        "username": session.username,
                        "created_at": session.created_at,
                        "last_rotated_at": session.last_rotated_at,
                        "was_rotated": False,
                        "seconds_until_rotation": seconds_until_rotation
                    }

        elif not is_exempt:
            return JsonResponse(
                {"detail": "Authentication required. No session ID provided."},
                status=401
            )

        response = self.get_response(request)

        # Attach session response headers if a session is present
        if request.current_session:
            sess = request.current_session
            response.headers["X-Session-ID"] = sess["session_id"]
            response.headers["X-Session-Rotated"] = "true" if sess["was_rotated"] else "false"
            response.headers["X-Session-Next-Rotation"] = str(sess["seconds_until_rotation"])
            response.set_cookie("session_id", sess["session_id"], httponly=True, samesite="Lax")

        return response

    def _get_session_id(self, request):
        x_session_id = request.headers.get("X-Session-ID")
        if x_session_id:
            return x_session_id.strip()

        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            return auth.split("Bearer ")[1].strip()

        return request.COOKIES.get("session_id")
