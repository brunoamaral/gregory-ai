from django.urls import path
from . import views

app_name = 'gregory'

urlpatterns = [
	path('acknowledgements/', views.acknowledgements, name='acknowledgements'),
]