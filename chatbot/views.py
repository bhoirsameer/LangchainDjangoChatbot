import json
import uuid
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Max

from chatbot.models import ChatMessage
from embeddings_and_model_calls import add_embeddings_and_call_llm, normalize_chat_id, stream_llm_reply


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
        cid = str(uuid.uuid4())

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


def _sse_event(data, event=None):
    """Format one Server-Sent Events message (a named event + a JSON data line)."""
    payload = json.dumps(data)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


@csrf_exempt
@require_http_methods(["POST"])
def chat_stream_view(request):
    """
    Same contract as chat_view, but streams the reply as Server-Sent Events
    instead of waiting for the full LLM response:
      - event: start  -> {"chat_id": ...}                      (sent once, immediately)
      - event: token  -> {"content": "<piece of the reply>"}   (sent repeatedly)
      - event: error  -> {"detail": "..."}                     (sent instead of tokens, on failure)
      - event: done   -> {"chat_id": ..., "full_response": "..."} (sent once, always last)
    """
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"detail": "Invalid JSON body"}, status=400)

    user_msg = body.get("message", "").strip()
    # Normalized up front so the ChatMessage rows below and stream_llm_reply's
    # own Postgres history / vectorstore usage all agree on the same chat_id.
    cid = normalize_chat_id(body.get("chat_id"))
    username = request.current_session["username"]

    # 1. Save user message in PostgreSQL, same as the non-streaming endpoint.
    ChatMessage.objects.create(
        chat_id=cid,
        username=username,
        role="user",
        content=user_msg
    )

    def event_stream():
        full_response = ""
        try:
            yield _sse_event({"chat_id": cid}, event="start")
            for piece in stream_llm_reply(chat_id=cid, user_message=user_msg):
                full_response += piece
                yield _sse_event({"content": piece}, event="token")
        except Exception as e:
            full_response = full_response or f"LLM error: {str(e)}"
            yield _sse_event({"detail": f"LLM error: {str(e)}"}, event="error")
        finally:
            # 3. Save assistant response in PostgreSQL, same as the non-streaming endpoint.
            ChatMessage.objects.create(
                chat_id=cid,
                username=username,
                role="assistant",
                content=full_response
            )
            yield _sse_event({"chat_id": cid, "full_response": full_response}, event="done")

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # disable proxy buffering (e.g. nginx) so chunks flush immediately
    return response


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


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def delete_thread_view(request):
    if not request.current_session:
        return JsonResponse({"detail": "Authentication required."}, status=401)

    username = request.current_session["username"]
    chat_id = None

    if request.body:
        try:
            body = json.loads(request.body.decode('utf-8'))
            chat_id = body.get("chat_id")
        except Exception:
            pass

    if not chat_id:
        chat_id = request.GET.get("chat_id")

    if not chat_id:
        return JsonResponse({"detail": "chat_id is required."}, status=400)

    # Call custom ORM delete() on filtered queryset -> performs soft delete (is_deleted=True)
    updated_count = ChatMessage.objects.filter(username=username, chat_id=chat_id.strip()).delete()

    return JsonResponse({
        "message": "Thread soft-deleted successfully",
        "chat_id": chat_id,
        "affected_count": updated_count
    })

