from django.urls import path
from api import views

urlpatterns = [
    path('', views.health_view, name='health'),
    
    # Registration
    path('register', views.register_view, name='register_alt'),
    path('api/register', views.register_view, name='register'),
    
    # Login
    path('login', views.login_view, name='login_alt'),
    path('api/login', views.login_view, name='login'),
    
    # Logout
    path('logout', views.logout_view, name='logout_alt'),
    path('api/logout', views.logout_view, name='logout'),
    
    # User info
    path('me', views.get_me_view, name='me_alt'),
    path('api/me', views.get_me_view, name='me'),
    
    # Session rotation test
    path('api/rotate-session-test', views.rotate_session_test_view, name='rotate_session_test'),
]
