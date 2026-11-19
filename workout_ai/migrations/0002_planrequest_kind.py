from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workout_ai", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="planrequest",
            name="kind",
            field=models.CharField(db_index=True, default="plan", max_length=10),
        ),
    ]
