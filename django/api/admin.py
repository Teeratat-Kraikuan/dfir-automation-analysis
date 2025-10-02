from django.contrib import admin
from .models import Evidence

@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "uploaded_by", "source_system", "status", "uploaded_at")
    list_filter = ("status", "acquisition_tool", "uploaded_at")
    search_fields = ("original_name", "uploaded_by", "source_system", "sha256")