from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mobile", "0003_alter_mobilecommand_action_mobilevideosession")]

    operations = [
        migrations.RemoveConstraint(
            model_name="mobiledevice",
            name="unique_caller_mobile_device_name_status",
        ),
        migrations.AddConstraint(
            model_name="mobiledevice",
            constraint=models.UniqueConstraint(
                fields=("tenant", "owner_subject_hash", "name", "status"),
                condition=~models.Q(status="deleted"),
                name="unique_caller_mobile_live_name_status",
            ),
        ),
    ]
