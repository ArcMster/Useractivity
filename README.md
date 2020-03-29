# Useractivity
Name of application: User_ActivityPeriod

When we load the home page, you will be redirected to the html page: Database_Portal

Where we can choose whether to add a user to the database or to fetch a user from the database

If we choose to add user to the data base, we will redirected to another html page: home.html where we can enter the details of a user. 

Once we click submit, we will be redirected to the next page: fetch.html which gives a confirmation that our data is saved and provides options: whether to add another user
or to fetch the details of a user. If we select the first one we will be redirected to the next page, or if we select the second option, we will be
redirected to another html page: userfetch.html.

In userfetch.html, we have 2 major option, either we can fetch a single user by searching with member id or we can fetch details of all the uses saved in the database.

If we fetch the details of a single user, the details of that specific user will be fetched in the next page: redirect.html. If the member id doesn't exists in the database,
another page: redirecterror.html will be loaded with an error message, this page will give options to go to the previous pages i.e., to search another user or to add a new user to the database

In userfetch.html, if we select the 2nd option i.e., to fetch the details of all the users, data of all the users in the database will be fetched to JSON file named user.json.

Please try using this application and let me know your oppinion.

Thanks for reading...!

