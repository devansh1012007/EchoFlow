# Merge migration to resolve conflicting leaf nodes.
# After updating 0002_user_revenuecat_fields to depend on
# 0002_merged_scraper_flags, the chain is linear and this
# file serves as the final node.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('app', '0003_audioclip_segments'),
    ]
    operations = []
