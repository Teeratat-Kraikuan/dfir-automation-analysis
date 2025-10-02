from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from pathlib import Path

User = get_user_model()


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Case(TimeStamped):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        ON_HOLD = "ON_HOLD", "On Hold"
        CLOSED = "CLOSED", "Closed"

    class Priority(models.IntegerChoices):
        LOW = 1
        MEDIUM = 2
        HIGH = 3
        CRITICAL = 4

    case_number = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    lead_investigator = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    investigator_name = models.CharField(max_length=255, blank=True)
    investigator_email = models.EmailField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.IntegerField(choices=Priority.choices, default=Priority.MEDIUM)

    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    tags = models.JSONField(default=list, blank=True)

    def __str__(self) -> str:
        return f"{self.case_number} - {self.title}"


class Evidence(TimeStamped):
    class ParseStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="evidence")

    # อัปโหลด
    original_filename = models.CharField(max_length=255)
    stored_path = models.CharField(max_length=512)  # path แบบ relative ใต้ MEDIA_ROOT (เช่น evidence_zips/<id>.zip)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True, blank=True)

    source_system = models.CharField(max_length=128, blank=True)
    acquisition_tool = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)

    # ขั้น extract/parse
    extracted_dir = models.CharField(max_length=512, blank=True)     # path แบบ absolute หรือ relative ก็ได้ (เราจะบันทึก absolute ที่ MEDIA_ROOT)
    mft_csv_path = models.CharField(max_length=512, blank=True)      # เช่น parsed/<evidence_id>/mft.csv (relative ใต้ MEDIA_ROOT)
    amcache_csv_path = models.CharField(max_length=512, blank=True)
    parse_log = models.TextField(blank=True)

    parse_status = models.CharField(max_length=8, choices=ParseStatus.choices, default=ParseStatus.PENDING)
    parse_progress = models.PositiveSmallIntegerField(default=0)  # 0..100
    parse_message = models.TextField(blank=True)

    # summary หลัง parse (เอาไว้โชว์ผลเบื้องต้น)
    summary = models.JSONField(default=dict, blank=True)  # {"mft":12345,"amcache":678,"events":321}

    def __str__(self) -> str:
        return self.original_filename

    # --------- helpers สำหรับ path ----------
    @property
    def zip_abspath(self) -> Path | None:
        if not self.stored_path:
            return None
        return Path(settings.MEDIA_ROOT) / self.stored_path

    @property
    def extracted_abspath(self) -> Path | None:
        return Path(self.extracted_dir) if self.extracted_dir else None

    @property
    def parsed_dir_abspath(self) -> Path:
        # เราใช้ parsed/<evidence_id>/ เสมอ
        return Path(settings.MEDIA_ROOT) / "parsed" / str(self.id)


# ---------- Artifacts (เผื่อ ingest เข้า DB ภายหลัง) ----------

class MFTEntry(models.Model):
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, related_name="mft_entries")
    entry_number = models.BigIntegerField()
    sequence = models.IntegerField(null=True, blank=True)
    is_directory = models.BooleanField(default=False)
    file_name = models.CharField(max_length=512, db_index=True)
    full_path = models.TextField(db_index=True)
    size_bytes = models.BigIntegerField(default=0)

    created_ts = models.DateTimeField(null=True, blank=True)
    modified_ts = models.DateTimeField(null=True, blank=True)
    accessed_ts = models.DateTimeField(null=True, blank=True)
    mft_changed_ts = models.DateTimeField(null=True, blank=True)

    attributes = models.JSONField(default=dict, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["evidence", "entry_number"]),
            models.Index(fields=["evidence", "is_directory"]),
            models.Index(fields=["evidence", "created_ts"]),
            models.Index(fields=["evidence", "modified_ts"]),
        ]


class AmcacheEntry(models.Model):
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, related_name="amcache_entries")
    app_name = models.CharField(max_length=512, db_index=True)
    version = models.CharField(max_length=128, blank=True)
    publisher = models.CharField(max_length=256, blank=True, db_index=True)
    install_date = models.DateTimeField(null=True, blank=True)
    file_path = models.TextField(db_index=True)
    sha1 = models.CharField(max_length=40, blank=True)
    pe_hash = models.CharField(max_length=64, blank=True)
    product_name = models.CharField(max_length=256, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["evidence", "install_date"]),
        ]