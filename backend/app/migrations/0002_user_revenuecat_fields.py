from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='revenuecat_app_user_id',
            field=models.UUIDField(default=uuid.uuid4, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='user',
            name='has_pro_entitlement',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_grace_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_last_synced',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
