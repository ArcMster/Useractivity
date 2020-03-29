from django.contrib import admin
from django.urls import path,include
from . import views

urlpatterns = [
    path('',views.home,name = 'home'),
    path('data_input',views.data_input,name='dinput'),
    path('home',views.home,name = 'home'),
    path('dates',views.dates,name = 'dates'),
    path('userfetch',views.userfetch,name = 'userfetch'),
    path('userdata',views.userdata,name = 'usedata'),
]