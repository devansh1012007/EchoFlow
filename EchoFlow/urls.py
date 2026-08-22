from django.contrib import admin
from django.urls import path, include
from django_prometheus import views as prom_views
from .health import health_check, readiness_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('app_1.urls')),
    path('health/', health_check, name='health_check'),
    path('ready/', readiness_check, name='readiness_check'),
    path('metrics/', prom_views.grafana_metrics, name='prometheus_django_metrics'),
]