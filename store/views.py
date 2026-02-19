from django.http import HttpResponse

def home(request):
    return HttpResponse("Marketplace App is working 🚀 ")