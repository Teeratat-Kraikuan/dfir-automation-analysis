from django.urls import path
from . import views

urlpatterns = [
    path("upload-evidence/", views.upload_evidence_api, name="upload_evidence_api"),
    path("start-extract/", views.start_extract_api, name="start_extract_api"),
    path("start-parse/", views.start_parse_api, name="start_parse_api"),  # <— ใหม่
    path("evidence/<uuid:ev_id>/", views.evidence_detail_api, name="evidence_detail_api"),
]