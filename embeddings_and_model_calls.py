import uuid
import psycopg
from configs import settings
from langchain_postgres import PostgresChatMessageHistory

conn = psycopg.connect(settings.database_url)
table_name = "langchain_chat_history"
PostgresChatMessageHistory.create_tables(conn, table_name)


def add_embeddings_and_call_llm(chat_id, user_message):
    if not chat_id:
        chat_id = str(uuid.uuid4())
    else:
        try:
            uuid.UUID(str(chat_id))
        except ValueError:
            chat_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(chat_id)))
    from langchain_groq import ChatGroq
    llm = ChatGroq(model="openai/gpt-oss-120b",api_key=settings.groq_api_key)
    
    
    history = PostgresChatMessageHistory(table_name, chat_id, sync_connection=conn)

    history.add_user_message(user_message)

    response = llm.invoke(history.messages)
    history.add_ai_message(response.content)
    print(response.content)
    return chat_id , response.content