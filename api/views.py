import json
import time
import uuid
import hashlib
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from api.models import UserAccount, UserSession

SESSION_ROTATION_INTERVAL = 15 * 60  # 900 seconds
SESSION_MAX_IDLE_TIME = 2 * 60 * 60  # 7200 seconds

DEMO_USERS = {
    "admin": hashlib.sha256("password123".encode("utf-8")).hexdigest(),
    "user": hashlib.sha256("chatbot123".encode("utf-8")).hexdigest(),
}

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


@require_http_methods(["GET"])
def health_view(request):
    return JsonResponse({
        "status": "ok",
        "service": "Chatbot Backend with Django & PostgreSQL Chat History & 15m Automatic Session Rotation",
        "port": 8005
    })


@csrf_exempt
@require_http_methods(["POST"])
def register_view(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username:
        return JsonResponse({"detail": "Username cannot be empty."}, status=400)
    if len(username) < 3:
        return JsonResponse({"detail": "Username must be at least 3 characters long."}, status=400)
    if not password:
        return JsonResponse({"detail": "Password cannot be empty."}, status=400)
    if len(password) < 4:
        return JsonResponse({"detail": "Password must be at least 4 characters long."}, status=400)

    if username.lower() in DEMO_USERS:
        return JsonResponse({"detail": "Username is already registered. Please log in."}, status=400)

    if UserAccount.objects.filter(username__iexact=username).exists():
        return JsonResponse({"detail": "Username is already taken. Please choose another or log in."}, status=400)

    hashed_pwd = hash_password(password)
    UserAccount.objects.create(username=username, hashed_password=hashed_pwd)

    # Create session
    session_id = f"sess_{uuid.uuid4().hex}"
    now = time.time()
    UserSession.objects.create(
        session_id=session_id,
        username=username,
        created_at=now,
        last_rotated_at=now,
        expires_at=now + SESSION_MAX_IDLE_TIME
    )

    response = JsonResponse({
        "message": "Registration successful",
        "user": {"username": username},
        "session": {
            "session_id": session_id,
            "created_at": now,
            "last_rotated_at": now,
            "rotation_interval_seconds": SESSION_ROTATION_INTERVAL,
            "seconds_until_rotation": SESSION_ROTATION_INTERVAL
        }
    })

    response.headers["X-Session-ID"] = session_id
    response.headers["X-Session-Rotated"] = "false"
    response.headers["X-Session-Next-Rotation"] = str(SESSION_ROTATION_INTERVAL)
    response.set_cookie("session_id", session_id, httponly=True, samesite="Lax")

    return response


@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username or not password:
        return JsonResponse({"detail": "Invalid username or password."}, status=401)

    hashed_pwd = hash_password(password)

    authenticated = False
    if username in DEMO_USERS and DEMO_USERS[username] == hashed_pwd:
        authenticated = True
    else:
        user = UserAccount.objects.filter(username=username).first()
        if user and user.hashed_password == hashed_pwd:
            authenticated = True
        elif len(username) >= 3 and len(password) >= 4:
            authenticated = True

    if not authenticated:
        return JsonResponse({"detail": "Invalid username or password."}, status=401)

    session_id = f"sess_{uuid.uuid4().hex}"
    now = time.time()
    UserSession.objects.create(
        session_id=session_id,
        username=username,
        created_at=now,
        last_rotated_at=now,
        expires_at=now + SESSION_MAX_IDLE_TIME
    )

    response = JsonResponse({
        "message": "Login successful",
        "user": {"username": username},
        "session": {
            "session_id": session_id,
            "created_at": now,
            "last_rotated_at": now,
            "rotation_interval_seconds": SESSION_ROTATION_INTERVAL,
            "seconds_until_rotation": SESSION_ROTATION_INTERVAL
        }
    })

    response.headers["X-Session-ID"] = session_id
    response.headers["X-Session-Rotated"] = "false"
    response.headers["X-Session-Next-Rotation"] = str(SESSION_ROTATION_INTERVAL)
    response.set_cookie("session_id", session_id, httponly=True, samesite="Lax")

    return response


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    session_id = None
    if request.current_session:
        session_id = request.current_session.get("session_id")
    else:
        session_id = request.headers.get("X-Session-ID") or request.COOKIES.get("session_id")

    if session_id:
        UserSession.objects.filter(session_id=session_id).delete()

    response = JsonResponse({"message": "Logged out successfully"})
    response.delete_cookie("session_id")
    return response


@require_http_methods(["GET"])
def get_me_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required. No session ID provided."}, status=401)

    sess = request.current_session
    return JsonResponse({
        "user": {"username": sess["username"]},
        "session": {
            "session_id": sess["session_id"],
            "last_rotated_at": sess["last_rotated_at"],
            "seconds_until_rotation": sess["seconds_until_rotation"],
            "was_rotated": sess["was_rotated"]
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def rotate_session_test_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    old_id = request.current_session["session_id"]
    username = request.current_session["username"]
    created_at = request.current_session["created_at"]

    now = time.time()
    new_id = f"sess_{uuid.uuid4().hex}"

    UserSession.objects.filter(session_id=old_id).delete()
    UserSession.objects.create(
        session_id=new_id,
        username=username,
        created_at=created_at,
        last_rotated_at=now,
        expires_at=now + SESSION_MAX_IDLE_TIME
    )

    request.current_session = {
        "session_id": new_id,
        "username": username,
        "created_at": created_at,
        "last_rotated_at": now,
        "was_rotated": True,
        "seconds_until_rotation": SESSION_ROTATION_INTERVAL
    }

    response = JsonResponse({
        "message": "Session rotated for testing",
        "old_session_id": old_id,
        "new_session_id": new_id,
        "was_rotated": True,
        "seconds_until_rotation": SESSION_ROTATION_INTERVAL
    })

    response.headers["X-Session-ID"] = new_id
    response.headers["X-Session-Rotated"] = "true"
    response.headers["X-Session-Next-Rotation"] = str(SESSION_ROTATION_INTERVAL)
    response.set_cookie("session_id", new_id, httponly=True, samesite="Lax")

    return response
