from django.urls import path
from . import views

urlpatterns = [
    path("upload-evidence/", views.upload_evidence_api, name="upload_evidence_api"),
    path("start-extract/", views.start_extract_api, name="start_extract_api"),
    path("start-parse/", views.start_parse_api, name="start_parse_api"),  # <— ใหม่
    path("evidence/<int:ev_id>/", views.evidence_detail_api, name="evidence_detail_api"),
    path("evidence/<int:ev_id>/mft/", views.mft_rows_api, name="mft_rows_api"),
    path("evidence/<int:ev_id>/amcache/", views.amcache_rows_api, name="amcache_rows_api"),
    path("evidence/<int:ev_id>/security/", views.security_events_rows_api, name="security_rows_api"),
    path("evidence/<int:ev_id>/security/rows", views.security_events_rows_api),  # alias no-trailing-slash
    path("evidence/<int:ev_id>/security/summary", views.security_events_summary_api, name="security_summary_api"),
    path("api/preflight/", views.parser_preflight_api, name="parser_preflight"),
]