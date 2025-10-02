import os
import subprocess
import zipfile
import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import Evidence

DOCKER_VOLUME_MEDIA = os.environ.get("DOCKER_VOLUME_MEDIA", "media")
MOUNT_POINT = "/mnt/media"  # จุดที่เราจะ mount volume 'media'

# ===== Helpers =====

def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _find_first_name(root: Path, target_lower: str, skip_dirs: set[str] | None = None) -> Path | None:
    """
    เดินหาไฟล์ชื่อ = target_lower (case-insensitive) ใต้ root (recursive)
    skip_dirs: โฟลเดอร์ที่ไม่ต้องเดินลงไป (เช่น 'Parsed')
    """
    skip_dirs = skip_dirs or set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]  # prune
        for fn in filenames:
            if fn.lower() == target_lower:
                return Path(dirpath) / fn
    return None

def _find_kape_artifacts(extracted_root: Path) -> tuple[Path | None, Path | None]:
    """
    หาไฟล์ตามผัง KAPE:
      - ลองใน 'Triage/' ก่อน
      - ถ้าไม่เจอ ค่อยหา recursive ทั้ง evidence โดยข้าม 'Parsed/'
    """
    triage = extracted_root / "Triage"
    mft_path = None
    amc_path = None

    if triage.exists():
        mft_path = _find_first_name(triage, "$mft")
        amc_path = _find_first_name(triage, "amcache.hve")

    if not mft_path:
        mft_path = _find_first_name(extracted_root, "$mft", skip_dirs={"Parsed"})
    if not amc_path:
        amc_path = _find_first_name(extracted_root, "amcache.hve", skip_dirs={"Parsed"})

    return mft_path, amc_path

def _docker_run(args: list[str]) -> tuple[int, str]:
    """รัน docker command และคืน (returncode, combined_output)"""
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = proc.communicate()
    return proc.returncode, out or ""

# ===== Views =====

@csrf_exempt
@require_POST
def upload_evidence_api(request):
    """
    รับไฟล์ ZIP ใหญ่แบบสตรีมลงดิสก์ + บันทึกฐานข้อมูล
    form fields: evidence_file, uploaded_by, source_system, acquisition_tool, notes
    """
    f = request.FILES.get("evidence_file")
    if not f:
        return HttpResponseBadRequest("missing file")

    uploaded_by = request.POST.get("uploaded_by", "")
    source_system = request.POST.get("source_system", "")
    acquisition_tool = request.POST.get("acquisition_tool", "KAPE")
    notes = request.POST.get("notes", "")

    with transaction.atomic():
        ev = Evidence.objects.create(
            uploaded_by=uploaded_by,
            source_system=source_system,
            acquisition_tool=acquisition_tool,
            notes=notes,
            original_name=getattr(f, "name", "evidence.zip"),
            size_bytes=getattr(f, "size", 0) or 0,
            status="uploaded",
        )

        target_rel = f"evidence_zips/{ev.id}.zip"
        target_abs = Path(settings.MEDIA_ROOT) / target_rel
        target_abs.parent.mkdir(parents=True, exist_ok=True)

        # เขียนเป็นสตรีม ไม่กิน RAM
        with open(target_abs, "wb") as dst:
            for chunk in f.chunks(chunk_size=1024 * 1024):
                dst.write(chunk)

        sha256 = _sha256_of_file(target_abs)
        ev.sha256 = sha256
        ev.zip_file.name = target_rel
        ev.save()

    return JsonResponse({
        "id": str(ev.id),
        "original_name": ev.original_name,
        "size_bytes": ev.size_bytes,
        "sha256": ev.sha256,
        "status": ev.status,
    })

@csrf_exempt
@require_POST
def start_extract_api(request):
    """แตก ZIP ไปไว้ ./media/extracted/<id>/"""
    ev_id = request.POST.get("id")
    if not ev_id:
        return HttpResponseBadRequest("missing id")

    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    zip_path = Path(ev.zip_file.path) if ev.zip_file else None
    if not zip_path or not zip_path.exists():
        return HttpResponseBadRequest("zip file not found")

    out_dir = Path(settings.MEDIA_ROOT) / "extracted" / str(ev.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ev.status = "extracting"
        ev.save()

        # แตกแบบป้องกัน path traversal
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for m in zf.infolist():
                p = Path(m.filename)
                if p.is_absolute() or ".." in p.parts:
                    continue
                zf.extract(m, out_dir)

        ev.extract_path = str(out_dir)
        ev.status = "ready"
        ev.save()
        return JsonResponse({"ok": True, "status": ev.status, "extract_path": ev.extract_path})
    except Exception as e:
        ev.status = "failed"
        ev.save()
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_POST
def start_parse_api(request):
    """
    หลังแตก ZIP:
    - หา $MFT และ Amcache.hve
    - ใช้ docker image ez-parsers แปลง CSV ไป ./media/parsed/<id>/
    - ใช้ named volume 'media' (หรือกำหนดผ่าน ENV DOCKER_VOLUME_MEDIA) เพื่อให้ docker บน host มองเห็นไฟล์
    ENV ที่อ่านได้:
      - DOCKER_VOLUME_MEDIA (default: "media")
      - DOCKER_VOLUME_MOUNTPOINT (default: "/mnt/media")
      - PARSER_IMAGE (default: "ez-parsers:latest")
      - PARSER_PLATFORM (optional, เช่น "linux/amd64")
    """
    ev_id = request.POST.get("id")
    if not ev_id:
        return HttpResponseBadRequest("missing id")

    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    if not ev.extract_path:
        return HttpResponseBadRequest("not extracted yet")

    extracted = Path(ev.extract_path)
    if not extracted.exists():
        return HttpResponseBadRequest("extracted path not found")

    # เตรียมโฟลเดอร์ปลายทาง (ใน MEDIA_ROOT ซึ่งแมปกับ volume เดียวกัน)
    parsed_dir = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    # หาไฟล์ตามผัง KAPE
    mft_path, amc_path = _find_kape_artifacts(extracted)

    # ค่าคอนฟิกสำหรับ docker run
    media_vol       = os.environ.get("DOCKER_VOLUME_MEDIA", "media")
    mount_point     = os.environ.get("DOCKER_VOLUME_MOUNTPOINT", "/mnt/media")
    parser_image    = getattr(settings, "PARSER_IMAGE", os.environ.get("PARSER_IMAGE", "ez-parsers:latest"))
    parser_platform = os.environ.get("PARSER_PLATFORM")  # เช่น "linux/amd64" (แนะนำบน Mac/ARM)

    ev.status = "parsing"
    ev.save()

    log_lines = []

    # เช็ค docker CLI ในคอนเทนเนอร์ django
    if shutil.which("docker") is None:
        ev.status = "failed"
        ev.parse_log = (ev.parse_log or "") + "\n docker CLI not found inside Django container."
        ev.save()
        return JsonResponse(
            {"ok": False, "status": "failed",
             "error": "docker CLI not found inside Django container. Install docker-cli and mount /var/run/docker.sock."},
            status=500
        )

    # เช็คว่ามี image parser แล้วหรือยัง
    rc_img, out_img = _docker_run(["docker", "image", "inspect", parser_image])
    if rc_img != 0:
        log_lines.append(out_img)
        ev.status = "failed"
        ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
        ev.save()
        return JsonResponse(
            {"ok": False, "status": "failed",
             "error": f"parser image '{parser_image}' not found; build it first",
             "log_tail": "\n".join(log_lines[-10:])},
            status=500
        )

    # helper เรียก parser ผ่าน docker โดย mount เป็น named volume 'media'
    def run_parser(kind: str, in_abs: Path, out_csv_name: str) -> bool:
        # relative path ภายใต้ extracted/<id> (เช่น 'KAPE/Triage/C/$MFT')
        rel_in = in_abs.relative_to(extracted).as_posix()

        # เส้นทางที่คอนเทนเนอร์ parser จะเห็น (ผ่าน mount_point ของ volume 'media')
        in_path = f"{mount_point}/extracted/{ev.id}/{rel_in}"
        out_dir = f"{mount_point}/parsed/{ev.id}"

        args = ["docker", "run", "--rm"]
        if parser_platform:  # บน Mac/ARM แนะนำตั้ง PARSER_PLATFORM=linux/amd64
            args += ["--platform", parser_platform]
        args += [
            "-v", f"{media_vol}:{mount_point}",  # ใช้ named volume media
            parser_image,
        ]

        if kind == "mft":
            args += ["mft", in_path, out_dir, out_csv_name]
        elif kind == "amcache":
            args += ["amcache", in_path, out_dir, out_csv_name]
        else:
            raise ValueError("unknown kind")

        rc, out = _docker_run(args)
        log_lines.append(f"$ {' '.join(args)}\n{out}\n(rc={rc})\n")
        return rc == 0

    # รันตามที่พบไฟล์
    if mft_path:
        ok = run_parser("mft", mft_path, "mft.csv")
        if ok:
            ev.mft_csv_path = f"parsed/{ev.id}/mft.csv"
    else:
        log_lines.append("! $MFT not found under extracted path\n")

    if amc_path:
        ok = run_parser("amcache", amc_path, "amcache.csv")
        if ok:
            ev.amcache_csv_path = f"parsed/{ev.id}/amcache.csv"
    else:
        log_lines.append("! Amcache.hve not found under extracted path\n")

    # อัปเดตสถานะตามผล
    if ev.mft_csv_path or ev.amcache_csv_path:
        ev.status = "parsed"
    else:
        ev.status = "failed"

    ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
    ev.save()

    return JsonResponse({
        "ok": ev.status == "parsed",
        "status": ev.status,
        "mft_csv": (settings.MEDIA_URL + ev.mft_csv_path) if ev.mft_csv_path else None,
        "amcache_csv": (settings.MEDIA_URL + ev.amcache_csv_path) if ev.amcache_csv_path else None,
        "log_tail": "\n".join(log_lines[-10:]),
    })

def evidence_detail_api(request, ev_id):
    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")
    return JsonResponse({
        "id": str(ev.id),
        "status": ev.status,
        "original_name": ev.original_name,
        "size_bytes": ev.size_bytes,
        "sha256": ev.sha256,
        "zip_file": ev.zip_file.url if ev.zip_file else None,
        "extract_path": ev.extract_path or None,
        "uploaded_by": ev.uploaded_by,
        "source_system": ev.source_system,
        "acquisition_tool": ev.acquisition_tool,
        "notes": ev.notes,
        "uploaded_at": ev.uploaded_at.isoformat(),
        "mft_csv": (settings.MEDIA_URL + ev.mft_csv_path) if ev.mft_csv_path else None,
        "amcache_csv": (settings.MEDIA_URL + ev.amcache_csv_path) if ev.amcache_csv_path else None,
    })