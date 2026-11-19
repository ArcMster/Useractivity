from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PlanRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.CharField(db_index=True, max_length=128)),
                ("created", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("ok", models.BooleanField(default=False)),
            ],
        ),
    ]
