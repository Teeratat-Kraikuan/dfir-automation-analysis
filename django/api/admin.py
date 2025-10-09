from django.contrib import admin
from .models import Case, Evidence, MFTEntry, AmcacheEntry

@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "title", "status", "priority", "created_at")
    search_fields = ("case_number", "title", "tags")

@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "case", "original_filename", "parse_status", "created_at")
    search_fields = ("original_filename", "sha256", "source_system", "notes")
    list_filter = ("parse_status",)

admin.site.register(MFTEntry)
admin.site.register(AmcacheEntry)