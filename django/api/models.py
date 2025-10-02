import uuid
from django.db import models

class Evidence(models.Model):
    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("extracting", "Extracting"),
        ("ready", "Ready"),     # แตก zip แล้ว
        ("parsing", "Parsing"),
        ("parsed", "Parsed"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    uploaded_by = models.CharField(max_length=200, blank=True)
    source_system = models.CharField(max_length=200, blank=True)
    acquisition_tool = models.CharField(max_length=100, default="KAPE")
    notes = models.CharField(max_length=500, blank=True)

    original_name = models.CharField(max_length=300)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)

    zip_file = models.FileField(upload_to="evidence_zips/")
    extract_path = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="uploaded")

    # ผลลัพธ์ CSV (เก็บเป็น relative path ใต้ MEDIA_ROOT)
    mft_csv_path = models.CharField(max_length=500, blank=True)
    amcache_csv_path = models.CharField(max_length=500, blank=True)
    parse_log = models.TextField(blank=True)

    def __str__(self):
        return f"{self.original_name} ({self.id})"