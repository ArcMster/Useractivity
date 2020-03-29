from django.shortcuts import render
from django.http import HttpResponse
from .models import Userdetails


# Create your views here.
def home(request):
    return render(request,'home.html')

def dates(request):
    stimes = []
    etimes = []
    stimes.append(POST['stime'])
    etimes.append(POST['etime'])
    return render(request,'home.html')

def data_input(request):
    stimes = []
    etimes = []
    uid = request.POST['id']
    name = request.POST['name']
    time_zone = request.POST['time_zone']
    stimes.append(request.POST['stime1'])
    stimes.append(request.POST['stime2'])
    stimes.append(request.POST['stime3'])
    etimes.append(request.POST['etime1'])
    etimes.append(request.POST['etime2'])
    etimes.append(request.POST['etime3'])
    

    userdata = Userdetails()
    userdata.uid = uid
    userdata.real_name = name
    count = 0
    for i in userdata.activity_periods:
        i['start time']= stimes[count]
        count+=1
    count = 0
    for i in userdata.activity_periods:
        i['end time']= etimes[count]
        count+=1
    userdata.save()
    #userdata.activity_periods['start time'].append(start_time)
    #userdata.activity_periods['end time'].append(end_time)


    return render(request,'fetch.html',{'name':name, 'time':userdata.activity_periods[0]['start time']})



def userdata(request):
    allusers = Userdetails.objects.all()
    l = []
    
    uid = request.GET['uid']
    for i in allusers:
        l.append(i.uid)
        #nid = i.uid
        #if  nid== uid:
         #   user = i.real_name
        
    return HttpResponse(l)

def userfetch(request):
    return render(request,'userfetch.html')