import os
import uuid
import psycopg
from configs import settings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PostgresChatMessageHistory
from langchain_postgres import PGVector

# --- Raw chat history storage (unchanged) ---
conn = psycopg.connect(settings.database_url)
table_name = "langchain_chat_history"
PostgresChatMessageHistory.create_tables(conn, table_name)

# --- Hybrid context config ---
DEFAULT_KEEP_LAST_N = 8  # how many most-recent raw messages are always sent as-is
DEFAULT_TOP_K = 3  # how many retrieved older chunks to inject
VECTORSTORE_COLLECTION_NAME = "chat_turn_embeddings"

# PGVector (via SQLAlchemy) needs the psycopg3 driver spelled out in the URL,
# while our raw psycopg.connect() above wants the plain "postgresql://" form.
_PGVECTOR_CONNECTION_STRING = settings.database_url.replace(
    "postgresql://", "postgresql+psycopg://", 1
)

# huggingface_hub / sentence-transformers read the token from the process
# environment, not from our pydantic Settings object — export it here so
# downloading/using the embedding model is authenticated (higher rate limits).
if settings.hf_token:
    os.environ.setdefault("HF_TOKEN", settings.hf_token)

# Local embedding model — inference itself needs no API key; the token above
# only affects downloading model weights from the HF Hub.
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# The vector store is optional infrastructure: if it can't be reached (e.g. the
# pgvector extension isn't installed yet), the chatbot should still work using
# only the raw "last N" window instead of hard-failing every request.
try:
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=VECTORSTORE_COLLECTION_NAME,
        connection=_PGVECTOR_CONNECTION_STRING,
        use_jsonb=True,
    )
except Exception as e:
    print(f"[warn] PGVector init failed, older-context retrieval/storage disabled: {e}")
    vectorstore = None

SYSTEM_PROMPT_TEMPLATE = """You are a helpful assistant continuing an ongoing conversation with the user.

Relevant context retrieved from earlier in this conversation (may or may not apply to the current message):
{retrieved_context}

Guidelines:
- The messages below this system message (the most recent turns) are the source of truth. \
If the retrieved context above conflicts with them in any way, trust the recent messages instead.
- Use the retrieved context only to fill in details that the recent messages don't already cover.
- If the retrieved context isn't relevant to the user's current message, ignore it entirely.
- Never mention "retrieval", "vector search", "embeddings", or that you looked anything up. \
Respond as if you simply remember the earlier conversation.
"""


def normalize_chat_id(chat_id):
    """Return a valid UUID string for chat_id, generating/deriving one if needed."""
    if not chat_id:
        return str(uuid.uuid4())
    try:
        uuid.UUID(str(chat_id))
        return chat_id
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(chat_id)))


def _build_llm():
    """Construct a fresh ChatGroq client for a single call."""
    return ChatGroq(model="openai/gpt-oss-120b", api_key=settings.groq_api_key)


def _recent_start_idx(all_messages, keep_last):
    """Raw-message index at which the 'always sent raw' window begins."""
    return max(0, len(all_messages) - keep_last)


def _search_similar_chunks(vectorstore, query, chat_id, overfetch_k):
    """Run the raw PGVector similarity search; return [] on any failure."""
    try:
        # PGVector's `filter` only does chat_id equality server-side; there's no
        # server-side "turn_index < X" operator, so we overfetch by similarity
        # and filter turn_index client-side afterwards.
        return vectorstore.similarity_search(query, k=overfetch_k, filter={"chat_id": chat_id})
    except Exception as e:
        # Vectorstore empty / unreachable / query failed — fall back gracefully.
        print(f"[warn] similarity search failed for chat_id={chat_id}: {e}")
        return []


def _filter_chunks_older_than(chunks, recent_start_idx):
    """Keep only chunks whose turn_index falls before the raw 'last N' window."""
    return [doc for doc in chunks if doc.metadata.get("turn_index", -1) < recent_start_idx]


def _format_context_chunks(chunks):
    """Join retrieved chunks into a single context string for the system prompt."""
    return "\n\n---\n\n".join(doc.page_content for doc in chunks)


def retrieve_older_context(
    chat_id,
    query,
    vectorstore,
    all_messages,
    keep_last=DEFAULT_KEEP_LAST_N,
    k=DEFAULT_TOP_K,
):
    """
    Return a string of relevant older context for `query`, drawn only from turns
    older than the raw "last N" window — or "" if there's nothing usable.

    `all_messages` is the raw message list (as it stands *before* the current
    turn is appended) used purely to compute where the "last N" boundary sits.
    """
    recent_start_idx = _recent_start_idx(all_messages, keep_last)

    # Nothing has aged out of the raw window yet — no point querying the store.
    if recent_start_idx <= 0 or vectorstore is None:
        return ""

    overfetch_k = max(k * 4, 10)
    results = _search_similar_chunks(vectorstore, query, chat_id, overfetch_k)
    if not results:
        return ""

    # Filtering preserves the similarity ranking order PGVector returned them in.
    older_chunks = _filter_chunks_older_than(results, recent_start_idx)
    if not older_chunks:
        return ""

    return _format_context_chunks(older_chunks[:k])


def _build_system_prompt(retrieved_context):
    return SYSTEM_PROMPT_TEMPLATE.format(
        retrieved_context=retrieved_context if retrieved_context else "(none found)"
    )


def _build_final_messages(system_prompt, recent_raw_messages, user_message):
    """system prompt + last-N raw messages + current user message."""
    return (
        [SystemMessage(content=system_prompt)]
        + recent_raw_messages
        + [HumanMessage(content=user_message)]
    )


def _persist_turn(history, user_message, ai_content):
    """Save the raw user/AI messages to Postgres history, same as before."""
    history.add_user_message(user_message)
    history.add_ai_message(ai_content)


def _embed_turn(vectorstore, chat_id, turn_index, user_message, ai_content):
    """Embed the completed Q&A pair, tagged with chat_id + turn_index. No-ops on failure."""
    if vectorstore is None:
        return
    try:
        vectorstore.add_texts(
            texts=[f"User: {user_message}\nAI: {ai_content}"],
            metadatas=[{"chat_id": chat_id, "turn_index": turn_index}],
        )
    except Exception as e:
        print(f"[warn] failed to store turn embedding for chat_id={chat_id}: {e}")


def add_embeddings_and_call_llm(
    chat_id, user_message, keep_last=DEFAULT_KEEP_LAST_N, k=DEFAULT_TOP_K
):
    chat_id = normalize_chat_id(chat_id)
    llm = _build_llm()
    history = PostgresChatMessageHistory(table_name, chat_id, sync_connection=conn)
    print(history, "\nHistory")
    # Raw messages already persisted for this chat, BEFORE this turn is added.
    # turn_index values stored in the vector store are message-indexes into
    # this same growing sequence, so they stay directly comparable across turns.
    prior_messages = history.messages
    recent_start_idx = _recent_start_idx(prior_messages, keep_last)

    # Step 1: pull in relevant context from turns older than the raw window.
    retrieved_context = retrieve_older_context(
        chat_id=chat_id,
        query=user_message,
        vectorstore=vectorstore,
        all_messages=prior_messages,
        keep_last=keep_last,
        k=k,
    )
    system_prompt = _build_system_prompt(retrieved_context)

    # Step 2: build the final prompt — system + last-N raw messages + current message.
    recent_raw_messages = prior_messages[recent_start_idx:]
    final_messages = _build_final_messages(system_prompt, recent_raw_messages, user_message)

    # Step 3: call the LLM with the hybrid message list (not the full raw history).
    response = llm.invoke(final_messages)
    print(response.content, "@11111")

    # Step 4: persist the raw turn to Postgres history, same as before.
    _persist_turn(history, user_message, response.content)

    # Step 5: embed the completed Q&A pair now that we have the AI's response.
    # turn_index is the raw-message index the user message occupies in the
    # sequence above — the same units recent_start_idx is computed in later.
    turn_index = len(prior_messages)
    _embed_turn(vectorstore, chat_id, turn_index, user_message, response.content)

    return chat_id, response.content


def stream_llm_reply(chat_id, user_message, keep_last=DEFAULT_KEEP_LAST_N, k=DEFAULT_TOP_K):
    """
    Streaming twin of `add_embeddings_and_call_llm`: yields response text pieces
    as the LLM produces them, then persists/embeds the full turn once done.

    `chat_id` must already be normalized (see `normalize_chat_id`) — callers that
    need the chat_id up front (e.g. to save a row before streaming starts) should
    normalize it themselves and pass the result in here, so the same id is used
    consistently across their own storage and this function's.
    """
    llm = _build_llm()
    history = PostgresChatMessageHistory(table_name, chat_id, sync_connection=conn)

    prior_messages = history.messages
    recent_start_idx = _recent_start_idx(prior_messages, keep_last)

    retrieved_context = retrieve_older_context(
        chat_id=chat_id,
        query=user_message,
        vectorstore=vectorstore,
        all_messages=prior_messages,
        keep_last=keep_last,
        k=k,
    )
    system_prompt = _build_system_prompt(retrieved_context)
    recent_raw_messages = prior_messages[recent_start_idx:]
    final_messages = _build_final_messages(system_prompt, recent_raw_messages, user_message)

    # Stream chunks from Groq as they arrive, accumulating the full text so we
    # can persist/embed it exactly once the stream ends.
    full_content = ""
    for chunk in llm.stream(final_messages):
        piece = chunk.content or ""
        if piece:
            full_content += piece
            yield piece

    _persist_turn(history, user_message, full_content)

    turn_index = len(prior_messages)
    _embed_turn(vectorstore, chat_id, turn_index, user_message, full_content)
