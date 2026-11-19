from django.urls import path

from . import views

urlpatterns = [
    path("plan/", views.plan, name="workout_ai_plan"),
    path("ask/", views.ask, name="workout_ai_ask"),
    path("life/plan/", views.life_plan, name="workout_ai_life_plan"),
    path("life/summary/", views.life_summary, name="workout_ai_life_summary"),
    path("forj/", views.forj, name="workout_ai_forj"),
    path("import/", views.import_plan, name="workout_ai_import"),
    path("health/", views.health, name="workout_ai_health"),
]
