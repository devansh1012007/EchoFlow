# Generated to resolve conflicting migration leaf nodes.
#
# Two migrations both depend on 0001_initial, creating two leaf nodes:
#   - 0002_user_revenuecat_fields
#   - 0002_merged_scraper_flags
# 0003_audioclip_segments depends on 0002_merged_scraper_flags, but
# 0002_user_revenuecat_fields is orphaned. This merge migration makes
# 0002_user_revenuecat_fields depend on 0002_merged_scraper_flags so
# there is a single linear chain: 0001 → 0002_merged → 0003_segments → 0004_merge.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0003_audioclip_segments'),
        ('app', '0002_user_revenuecat_fields'),
    ]

    operations = [
    ]
