import os
from celery import Celery

# Point this to the EchoFlow folder where settings.py lives
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'EchoFlow.settings')

# Name the Celery app after your project
app = Celery('EchoFlow')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()