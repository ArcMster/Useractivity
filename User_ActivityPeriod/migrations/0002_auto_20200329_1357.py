# Generated by Django 3.0.2 on 2020-03-29 08:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User_ActivityPeriod', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userdetails',
            name='end_time1',
            field=models.CharField(default=5, max_length=15),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userdetails',
            name='end_time2',
            field=models.CharField(default=5, max_length=15),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userdetails',
            name='end_time3',
            field=models.CharField(default=51, max_length=15),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userdetails',
            name='start_time1',
            field=models.CharField(default=55, max_length=15),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userdetails',
            name='start_time2',
            field=models.CharField(default=565, max_length=15),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userdetails',
            name='start_time3',
            field=models.CharField(default=6556, max_length=15),
            preserve_default=False,
        ),
    ]