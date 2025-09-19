from django.contrib import admin
from django.urls import path,include
from . import views

urlpatterns = [

    path('',views.adderpage, name = 'adderpage'),
    #path('',views.firstpage,name = 'firstpage'),
    # path('firstpage',views.firstpage,name = 'firstpage'),
    # path('data_input',views.data_input,name='dinput'),
    # path('home',views.home,name = 'home'),
    # path('userfetch',views.userfetch,name = 'userfetch'),
    # path('userdata',views.userdata,name = 'usedata'),
    # path('manualfetch',views.manualfetch,name='manualfetch'),
    # path('fetchall',views.fetchall,name='fetch_all'),
    # path('lastpage',views.lastpage,name='lastpage'),
    # path('email', views.email, name = "email"),
    # path('sendmail',views.sendmail,name = "sendmail")
]