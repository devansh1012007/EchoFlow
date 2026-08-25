import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.EchoFlow.settings')

app = Celery('backend.EchoFlow')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks(['backend.app'])