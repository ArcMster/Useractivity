from django.db import models

# Create your models here.
class Userdetails(models.Model):
    uid = models.CharField(max_length=7)
    real_name = models.CharField(max_length=7)
    tz = models.CharField(max_length=10)
    start_time1 = models.CharField(max_length=15)
    start_time2 = models.CharField(max_length=15)
    start_time3 = models.CharField(max_length=15)

    end_time1 = models.CharField(max_length=15)
    end_time2 = models.CharField(max_length=15)
    end_time3 = models.CharField(max_length=15)

    