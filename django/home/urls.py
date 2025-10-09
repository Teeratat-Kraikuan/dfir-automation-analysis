from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload_evidence, name='upload_evidence'),

    # ผลลัพธ์แบบเลือกหลักฐาน (index)
    path('results/', views.results_index, name='results_index'),

    # หน้าผลลัพธ์ของ evidence รายตัว
    path('result/<int:ev_id>/', views.view_result, name='view_result'),
]
