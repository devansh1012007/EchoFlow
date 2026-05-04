from django.db import migrations

class Migration(migrations.Migration):
    """
    Forces allauth to wait for app_1.User to exist.
    Without this, account.0001_initial can run before app_1.0001_initial.
    """
    dependencies = [
        ('app_1', '0001_initial'),
        ('account', '0001_initial'),
    ]
    operations = []