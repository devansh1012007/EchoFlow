from django.contrib import admin
from django.urls import path, include
from django_prometheus.exports import ExportToDjangoView
from .health import health_check, readiness_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('backend.app.urls')),
    path('health/', health_check, name='health_check'),
    path('ready/', readiness_check, name='readiness_check'),
    # Modern django-prometheus export view; prometheus_client ships as a
    # dependency of django-prometheus (installed via requirements-base.txt).
    path('metrics/', ExportToDjangoView, name='prometheus_django_metrics'),
]
