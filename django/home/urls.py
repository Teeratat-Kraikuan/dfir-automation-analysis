from django.urls import path
from . import views

urlpatterns = [
	path('', views.dashboard, name='dashboard'),
	path('upload/', views.upload_evidence, name='upload_evidence'),
	path('results/', views.view_result, name='view_result'),
]