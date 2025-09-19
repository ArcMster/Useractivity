from .models import Sample

def hi():
    Sample.objects.create(name='test')

    print('Printed from cron tab')
    f = open('/home/pssreenath/Useractivity/assets/j.txt','w')
    f.close()
    return ('Hi ran successfully')