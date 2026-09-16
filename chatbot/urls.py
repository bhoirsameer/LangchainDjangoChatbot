from django.urls import path
from chatbot import views

urlpatterns = [
    # Chat
    path('chat', views.chat_view, name='chatbot_chat_alt'),
    path('api/chat', views.chat_view, name='chatbot_chat'),

    # Chat (streaming, Server-Sent Events)
    path('chat/stream', views.chat_stream_view, name='chatbot_chat_stream_alt'),
    path('api/chat/stream', views.chat_stream_view, name='chatbot_chat_stream'),

    # History
    path('history', views.get_history_view, name='chatbot_history_alt'),
    path('api/history', views.get_history_view, name='chatbot_history'),

    # Threads
    path('api/threads', views.get_threads_view, name='chatbot_threads'),
    path('api/delete-thread', views.delete_thread_view, name='chatbot_delete_thread'),
    path('api/threads/delete', views.delete_thread_view, name='chatbot_delete_thread_alt'),
]

