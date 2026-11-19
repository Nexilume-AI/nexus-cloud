from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("providers", "0001_personal_models")]
    operations = [
        migrations.AddField(model_name="providerruntimemodeloffer", name="cache_write_price_per_1k_tokens", field=models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)),
        migrations.AddField(model_name="providerruntimemodeloffer", name="cache_write_30m_price_per_1k_tokens", field=models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)),
        migrations.AddField(model_name="providerruntimemodeloffer", name="cache_write_12h_price_per_1k_tokens", field=models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)),
    ]
