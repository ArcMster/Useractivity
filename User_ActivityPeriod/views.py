from django.shortcuts import render
from django.http import HttpResponse
from .models import Userdetails
import json


# Create your views here.

def firstpage(request):
    return render(request,'Database_Portal.html')
def home(request):
    return render(request,'home.html')

def manualfetch(request):
    return render(request,'userfetch.html')

def time_format(data):
    condition = False
    res = ""
    indexvalue = data.index(':')
    time = data[:indexvalue]
    min = data[indexvalue:]
    time= int(time)
    if time>12:
        time = time-12
        condition = True
    if condition == True:
        res += str(time)+min+ " PM"
    else:
        res += data + " AM"
    return res

def data_input(request):
    timesheet = {'Day1':{'Start time':"",'End time': ""},'Day2':{'Start time':"",'End time': ""},'Day3':{'Start time':"",'End time': ""}}
    timesheet['Day1']['Start time']+= (request.POST['stime1m'] +"  " + request.POST['stime1d'] + " " + request.POST['stime1y'] + " " + time_format(request.POST['stime1t']))
    timesheet['Day2']['Start time']+= (request.POST['stime2m'] + " " + request.POST['stime2d'] + " " + request.POST['stime2y'] + " " + time_format(request.POST['stime2t']))
    timesheet['Day3']['Start time']+= (request.POST['stime3m'] + " " + request.POST['stime3d'] + " " + request.POST['stime3y'] + " " + time_format(request.POST['stime3t']))

    timesheet['Day1']['End time']+= (request.POST['etime1m'] + " " + request.POST['etime1d'] + " " + request.POST['etime1y'] + " " + time_format(request.POST['etime1t']))
    timesheet['Day2']['End time']+= (request.POST['etime2m'] + " " + request.POST['etime2d'] + " " + request.POST['etime2y'] + " " + time_format(request.POST['etime2t']))
    timesheet['Day3']['End time']+= (request.POST['etime3m'] + " " + request.POST['etime3d'] + " " + request.POST['etime3y'] + " " + time_format(request.POST['etime3t']))
    uid = request.POST['id']
    name = request.POST['name']
    time_zone = request.POST['time_zone']
    
    

    userdata = Userdetails()
    userdata.uid = uid
    userdata.real_name = name
    userdata.tz = time_zone
    userdata.start_time1 = timesheet['Day1']['Start time']
    userdata.start_time2 = timesheet['Day2']['Start time']
    userdata.start_time3 = timesheet['Day3']['Start time']

    userdata.end_time1 = timesheet['Day1']['End time']
    userdata.end_time2 = timesheet['Day2']['End time']
    userdata.end_time3 = timesheet['Day3']['End time']
    
    userdata.save()
    #userdata.activity_periods['start time'].append(start_time)
    #userdata.activity_periods['end time'].append(end_time)


    return render(request,'fetch.html')



def userdata(request):
    allusers = Userdetails.objects.all()
    
    fetchall(request)
    condition = False
    uid = request.GET['uid']
    for i in allusers:
        if i.uid == uid:
            condition = True
            name = i.real_name
            day1 = "Logged in at " + i.start_time1 + " and logged out at " + i.end_time1
            day2 = "Logged in at " + i.start_time2 + " and logged out at " + i.end_time2
            day3 = "Logged in at " + i.start_time3 + " and logged out at " + i.end_time3
    if condition == True:
        return render(request,'redirect.html',{'name':name,'day1':day1,'day2':day2,'day3':day3})
    else:
        return render(request,'redirecterror.html')

def fetchall(request):
    allusers = Userdetails.objects.all()
    members = {"id":"",
            "real_name":"",
            "tz":"",
            "activity_periods":[{"start_time":"","end_time":""},
            {"start_time":"","end_time":""},
            {"start_time":"","end_time":""}]}
    members_sample = members
    json_data = {"ok": True,
        "members":[]}
    for i in allusers:
        members['id']=i.uid
        members['real_name']=i.real_name
        members['tz']=i.tz
        members['activity_periods'][0]['start_time']=i.start_time1
        members['activity_periods'][0]['end_time']=i.end_time1
        members['activity_periods'][1]['start_time']=i.start_time2
        members['activity_periods'][1]['end_time']=i.end_time2
        members['activity_periods'][2]['start_time']=i.start_time3
        members['activity_periods'][2]['end_time']=i.end_time3
        json_data['members'].append(members)
        members = members_sample
        print(members_sample)
    
    
    j = json.dumps(json_data)
    with open('C:\\Users\\PSSRE\\Djangoproject\\Useractivity\\user.json','w') as f:
        f.write(j)
        f.close()
    return render(request,'home.html')

def userfetch(request):
    return render(request,'userfetch.html')