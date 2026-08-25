import django.contrib.auth.models
import django.db.models.deletion
import django.utils.timezone
import pgvector.django
from django.conf import settings
from django.db import migrations, models
import uuid
from pgvector.django import HnswIndex


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Step 0: Enable pgvector extension
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="DROP EXTENSION IF EXISTS vector;"
        ),

        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True)),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True)),
                ('is_superuser', models.BooleanField(default=False)),
                ('username', models.CharField(max_length=150, unique=True)),
                ('first_name', models.CharField(blank=True, max_length=150)),
                ('last_name', models.CharField(blank=True, max_length=150)),
                ('is_staff', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now)),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('encrypted_email', models.TextField(blank=True, null=True, unique=True)),
                ('long_term_semantic', pgvector.django.VectorField(blank=True, dimensions=384, null=True)),
                ('long_term_acoustic', pgvector.django.VectorField(blank=True, dimensions=128, null=True)),
                ('groups', models.ManyToManyField(blank=True, related_name='user_set', to='auth.group')),
                ('user_permissions', models.ManyToManyField(blank=True, related_name='user_set', to='auth.permission')),
                ('following', models.ManyToManyField(blank=True, related_name='followers', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'user',
                'verbose_name_plural': 'users',
                'abstract': False},
            managers=[('objects', django.contrib.auth.models.UserManager())],
        ),
        migrations.CreateModel(
            name='AudioClip',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('title', models.CharField(max_length=255)),
                ('category', models.CharField(blank=True, max_length=50)),
                ('original_file', models.FileField(null=True, upload_to='uploads/%Y/%m/%d/')),
                ('hls_playlist_url', models.CharField(blank=True, max_length=500, null=True)),
                
                ('source_name', models.CharField(blank=True, max_length=100, null=True)),
                ('source_url', models.CharField(blank=True, max_length=500, null=True)),
                ('license', models.CharField(blank=True, max_length=100, null=True)),
                ('attribution_text', models.CharField(blank=True, max_length=500, null=True)),
                ('imported_via_scraper', models.BooleanField(default=False)),
                ('original_source_id', models.CharField(blank=True, max_length=255, null=True)),

                ('duration_ms', models.IntegerField(default=0)),
                ('avg_completion_rate', models.FloatField(default=0.0)),
                ('engagement_velocity', models.FloatField(default=0.0)),
                ('likes', models.BigIntegerField(default=0)),
                ('shares', models.BigIntegerField(default=0)),
                ('skips', models.BigIntegerField(default=0)),
                ('comment_count', models.BigIntegerField(default=0)),
                ('tags', models.JSONField(blank=True, default=list)),
                ('semantic_vector', pgvector.django.VectorField(blank=True, dimensions=384, null=True)),
                ('acoustic_vector', pgvector.django.VectorField(blank=True, dimensions=128, null=True)),
                ('status', models.CharField(default='processing', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('creator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audio_clips', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Comment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ('text', models.CharField(max_length=500)),
                ('likes', models.BigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('clip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='app_1.audioclip')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='app_1.comment')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='ShareEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('is_read', models.BooleanField(default=False)),
                ('clip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app_1.audioclip')),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_shares', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_shares', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='UserInteraction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True)),
                ('interaction_type', models.CharField(choices=[('like','Like'),('share','Share'),('skip','Skip'),('view','View')], max_length=10)),
                ('is_active', models.BooleanField(default=True)),
                ('watch_time_ms', models.IntegerField(default=0)),
                ('completion_rate', models.FloatField(default=0.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('clip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app_1.audioclip')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(fields=['clip', '-created_at'], name='app_1_comme_clip_id_d0c890_idx'),
        ),
        migrations.AddIndex(
            model_name='shareevent',
            index=models.Index(fields=['receiver', '-created_at', 'is_read'], name='app_1_share_receive_10ab8e_idx'),
        ),
        migrations.AddIndex(
            model_name='userinteraction',
            index=models.Index(fields=['user', 'interaction_type'], name='app_1_useri_user_id_046714_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='userinteraction',
            unique_together={('user', 'clip', 'interaction_type')},
        ),



        migrations.AddIndex(
            model_name='audioclip',
            index=models.Index(fields=['status', '-created_at'], name='audioclip_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='audioclip',
            index=models.Index(fields=['status', '-engagement_velocity'], name='audioclip_status_eng_vel_idx'),
        ),
        migrations.AddIndex(
            model_name='audioclip',
            index=models.Index(fields=['category', '-likes'], name='audioclip_cat_likes_idx'),
        ),
        ## chages for vector indexing
        migrations.AddIndex(
            model_name='audioclip',
            index=pgvector.django.HnswIndex(
                ef_construction=64, 
                m=16, 
                name='semantic_vector_index', 
                opclasses=['vector_cosine_ops'], 
                fields=['semantic_vector']
            ),
        ),
        migrations.AddIndex(
            model_name='audioclip',
            index=pgvector.django.HnswIndex(
                ef_construction=64, 
                m=16, 
                name='acoustic_vector_index', 
                opclasses=['vector_cosine_ops'], 
                fields=['acoustic_vector']
            ),
        ),

    ]