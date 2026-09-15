import json
import uuid
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Max

from chatbot.models import ChatMessage
from embeddings_and_model_calls import add_embeddings_and_call_llm


@csrf_exempt
@require_http_methods(["POST"])
def chat_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    user_msg = body.get("message", "").strip()
    cid = body.get("chat_id")
    if not cid:
        cid = f"chat_{uuid.uuid4().hex[:8]}"

    username = request.current_session["username"]

    # 1. Save user message in PostgreSQL
    ChatMessage.objects.create(
        chat_id=cid,
        username=username,
        role="user",
        content=user_msg
    )

    # 2. Call LangChain + Groq LLM
    try:
        _, response_text = add_embeddings_and_call_llm(chat_id=cid, user_message=user_msg)
    except Exception as e:
        response_text = f"LLM error: {str(e)}"

    # 3. Save assistant response in PostgreSQL
    ChatMessage.objects.create(
        chat_id=cid,
        username=username,
        role="assistant",
        content=response_text
    )

    return JsonResponse({
        "reply": response_text,
        "chat_id": cid
    })


@require_http_methods(["GET"])
def get_history_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    username = request.current_session["username"]
    chat_id = request.GET.get("chat_id")

    qs = ChatMessage.objects.filter(username=username)
    if chat_id:
        qs = qs.filter(chat_id=chat_id.strip())

    qs = qs.order_by("id")

    history = [
        {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "username": msg.username,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else None
        }
        for msg in qs
    ]

    return JsonResponse({
        "history": history,
        "chat_id": chat_id
    })


@require_http_methods(["GET"])
def get_threads_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    username = request.current_session["username"]

    # Get distinct chat_ids and their latest creation date
    user_chats = (
        ChatMessage.objects.filter(username=username)
        .values("chat_id")
        .annotate(last_activity=Max("created_at"))
        .order_by("-last_activity")
    )

    threads = []
    for chat in user_chats:
        cid = chat["chat_id"]
        first_user_msg = (
            ChatMessage.objects.filter(username=username, chat_id=cid, role="user")
            .order_by("id")
            .first()
        )
        title = first_user_msg.content if first_user_msg else "Chat Session"
        if len(title) > 30:
            title = title[:28] + "..."

        threads.append({
            "chat_id": cid,
            "title": title,
            "last_activity": chat["last_activity"].isoformat() if chat["last_activity"] else ""
        })

    return JsonResponse({"threads": threads})
