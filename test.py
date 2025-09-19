from mailer import Mailer

mail = Mailer(email='nfclub56@gmail.com', password='Red@1234')
mail.send(receiver='psnathsrt@gmail.com', subject='TEST', message='From Python!')