from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mobile", "0001_personal_runtime")]
    operations = [migrations.AddField(
        model_name="mobilecommand", name="result_receipt_sha256",
        field=models.CharField(max_length=64, blank=True, editable=False),
    )]
