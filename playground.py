a = "06:15"
condition = False
res = ""
indexvalue = a.index(':')
time = a[:indexvalue]
min = a[indexvalue:]
time= int(time)
if time>12:
    time = time-12
    condition = True
if condition == True:
    res += str(time)+min+ " PM"
else:
    res += a + " AM"
print(res)



