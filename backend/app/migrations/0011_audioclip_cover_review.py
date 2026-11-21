from django.db import migrations, models


def hold_existing_covers(apps, schema_editor):
    """Any cover that already exists predates review, so it goes back to pending."""
    AudioClip = apps.get_model("app", "AudioClip")
    (
        AudioClip.objects.exclude(cover_image="")
        .exclude(cover_image__isnull=True)
        .update(cover_status="pending")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0010_audioclip_moderation_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="audioclip",
            name="cover_status",
            field=models.CharField(
                choices=[
                    ("none", "No cover"),
                    ("pending", "Awaiting review"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="none",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="audioclip",
            name="cover_moderation_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="audioclip",
            name="cover_moderated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(hold_existing_covers, migrations.RunPython.noop),
    ]
