from django.db import models

# Create your models here.
class Userdetails(models.Model):
    uid = models.CharField(max_length=7)
    real_name = models.CharField(max_length=7)
    tz = models.CharField(max_length=10)
    activity_periods = [{'start time':'','end time':''},{'start time':'','end time':''},{'start time':'','end time':''}]
    