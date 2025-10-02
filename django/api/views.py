# django/api/views.py
import os
import hashlib
import zipfile
import subprocess
import shutil
from pathlib import Path
from typing import Tuple, Optional, Set

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Case, Evidence, MFTEntry, AmcacheEntry


# ===== Config from ENV / settings =====
DOCKER_VOLUME_MEDIA = os.environ.get("DOCKER_VOLUME_MEDIA", "media")
DOCKER_VOLUME_MOUNTPOINT = os.environ.get("DOCKER_VOLUME_MOUNTPOINT", "/mnt/media")
PARSER_IMAGE = getattr(settings, "PARSER_IMAGE", os.environ.get("PARSER_IMAGE", "ez-parsers:latest"))
PARSER_PLATFORM = os.environ.get("PARSER_PLATFORM")  # e.g. "linux/amd64" on Mac/ARM


# ===== Helpers =====

def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_first_name(root: Path, target_lower: str, skip_dirs: Optional[Set[str]] = None) -> Optional[Path]:
    """
    เดินหาไฟล์ชื่อ = target_lower (case-insensitive) ใต้ root (recursive)
    skip_dirs: โฟลเดอร์ที่ไม่ต้องเดินลงไป (เช่น {'Parsed'})
    """
    skip_dirs = skip_dirs or set()
    for dirpath, dirnames, filenames in os.walk(root):
        # prune โฟลเดอร์ที่ไม่อยากเดิน
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.lower() == target_lower:
                return Path(dirpath) / fn
    return None


def _find_kape_artifacts(extracted_root: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """
    หาไฟล์ตามผัง KAPE:
      - พยายาม KAPE/Triage → Triage → ทั้งโฟลเดอร์
      - ข้ามโฟลเดอร์ 'Parsed'
    """
    mft_path = None
    amc_path = None

    candidates = [
        extracted_root / "KAPE" / "Triage",
        extracted_root / "Triage",
        extracted_root,  # fallback
    ]
    for base in candidates:
        if base.exists():
            if not mft_path:
                mft_path = _find_first_name(base, "$mft", skip_dirs={"Parsed"})
            if not amc_path:
                amc_path = _find_first_name(base, "amcache.hve", skip_dirs={"Parsed"})
    return mft_path, amc_path


def _docker_run(args: list[str]) -> tuple[int, str]:
    """รัน docker command และคืน (returncode, combined_output)"""
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = proc.communicate()
    return proc.returncode, out or ""


def _exists_nonempty(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except Exception:
        return False


# ===== Views =====

@csrf_exempt
@require_POST
def upload_evidence_api(request):
    """
    อัปโหลด ZIP ขนาดใหญ่แบบสตรีม + ผูกกับเคส
    form fields:
      - evidence_file (required)
      - case_id (optional) ถ้าไม่ส่งมาจะ auto-create เคสให้
      - uploaded_by (text) *ถ้ายังไม่ใช้ auth จะเก็บแนบลงใน notes
      - source_system, acquisition_tool, notes
    """
    f = request.FILES.get("evidence_file")
    if not f:
        return HttpResponseBadRequest("missing file")

    # --- Select/Create Case ---
    case_id = request.POST.get("case_id")
    if case_id:
        try:
            case = Case.objects.get(id=case_id)
        except Case.DoesNotExist:
            return HttpResponseBadRequest("case not found")
    else:
        from datetime import datetime
        case = Case.objects.create(
            case_number=f"CASE-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            title="Auto-created Case",
            description="Created by upload_evidence_api",
        )

    source_system = request.POST.get("source_system", "")
    acquisition_tool = request.POST.get("acquisition_tool", "KAPE")
    notes = request.POST.get("notes", "")
    uploaded_by_name = request.POST.get("uploaded_by", "").strip()
    if uploaded_by_name:
        notes = (notes + f"\n[UploadedBy:{uploaded_by_name}]").strip()

    uploaded_by_user = (
        request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    )

    with transaction.atomic():
        ev = Evidence.objects.create(
            case=case,
            original_filename=getattr(f, "name", "evidence.zip"),
            stored_path="",  # set ทีหลังเมื่อเขียนไฟล์เสร็จ
            size_bytes=getattr(f, "size", 0) or 0,
            source_system=source_system,
            acquisition_tool=acquisition_tool,
            uploaded_by=uploaded_by_user,
            notes=notes,
            parse_status=getattr(Evidence.ParseStatus, "PENDING", "PENDING"),
            parse_message="uploaded",
        )

        # เก็บไฟล์ลง MEDIA_ROOT/evidence_zips/<evidence_id>.zip
        target_rel = f"evidence_zips/{ev.id}.zip"
        target_abs = Path(settings.MEDIA_ROOT) / target_rel
        target_abs.parent.mkdir(parents=True, exist_ok=True)

        with open(target_abs, "wb") as dst:
            for chunk in f.chunks(1024 * 1024):
                dst.write(chunk)

        ev.sha256 = _sha256_of_file(target_abs)
        ev.stored_path = target_rel
        ev.save()

    return JsonResponse({
        "id": str(ev.id),
        "case_id": str(case.id),
        "case_number": case.case_number,
        "original_name": ev.original_filename,
        "size_bytes": ev.size_bytes,
        "sha256": ev.sha256,
        "status": ev.parse_status,  # ให้ frontend ใช้ key เดิมได้
    })


@csrf_exempt
@require_POST
def start_extract_api(request):
    """
    แตก ZIP → MEDIA_ROOT/extracted/<evidence_id>/
    """
    ev_id = request.POST.get("id")
    if not ev_id:
        return HttpResponseBadRequest("missing id")

    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    if not ev.stored_path:
        return HttpResponseBadRequest("zip file not registered")

    zip_path = Path(settings.MEDIA_ROOT) / ev.stored_path
    if not zip_path.exists():
        return HttpResponseBadRequest("zip file not found")

    out_dir = Path(settings.MEDIA_ROOT) / "extracted" / str(ev.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ev.parse_status = getattr(Evidence.ParseStatus, "RUNNING", "RUNNING")
        ev.parse_message = "extracting"
        ev.save()

        # ป้องกัน path traversal
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for m in zf.infolist():
                p = Path(m.filename)
                if p.is_absolute() or ".." in p.parts:
                    continue
                zf.extract(m, out_dir)

        # บันทึก path ที่แตกไฟล์แล้ว
        if hasattr(ev, "extracted_dir"):
            ev.extracted_dir = str(out_dir)
        else:
            # ถ้าโมเดลไม่มีฟิลด์นี้ ก็ถือว่าพร้อม (ยังใช้งานต่อได้ในฟังก์ชัน parse โดยคำนวณจาก MEDIA_ROOT)
            pass

        ev.parse_message = "ready"
        ev.save()

        return JsonResponse({"ok": True, "status": ev.parse_status, "extract_path": str(out_dir)})
    except Exception as e:
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = f"extract error: {e}"
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + f"\nextract error: {e}"
        ev.save()
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_POST
def start_parse_api(request):
    """
    หลังแตก ZIP:
      - หา $MFT และ Amcache.hve
      - docker run PARSER_IMAGE เพื่อแปลง CSV → MEDIA_ROOT/parsed/<id>/
      - ใช้ named volume 'media' และเมาท์ใน parser ที่ DOCKER_VOLUME_MOUNTPOINT
    ENV รองรับ:
      - DOCKER_VOLUME_MEDIA (default: "media")
      - DOCKER_VOLUME_MOUNTPOINT (default: "/mnt/media")
      - PARSER_IMAGE (default: "ez-parsers:latest")
      - PARSER_PLATFORM (optional, e.g., "linux/amd64")
    """
    ev_id = request.POST.get("id")
    if not ev_id:
        return HttpResponseBadRequest("missing id")

    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    # หาโฟลเดอร์ extracted
    if getattr(ev, "extracted_dir", None):
        extracted = Path(ev.extracted_dir)
    else:
        # เผื่อโมเดลไม่มีฟิลด์ extracted_dir ให้เดาจาก MEDIA_ROOT
        extracted = Path(settings.MEDIA_ROOT) / "extracted" / str(ev.id)
    if not extracted.exists():
        return HttpResponseBadRequest("extracted path not found")

    # เตรียม parsed dir
    parsed_dir = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    # หาไฟล์เป้าหมาย
    mft_path, amc_path = _find_kape_artifacts(extracted)

    ev.parse_status = getattr(Evidence.ParseStatus, "RUNNING", "RUNNING")
    ev.parse_message = "parsing"
    ev.save()

    log_lines: list[str] = []

    # docker CLI ต้องมีในคอนเทนเนอร์ django (เรามี /var/run/docker.sock ส่องออกไป host)
    if shutil.which("docker") is None:
        msg = "docker CLI not found in django container"
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = msg
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + "\n" + msg
        ev.save()
        return JsonResponse({"ok": False, "error": msg}, status=500)

    # มี image หรือยัง
    rc_img, out_img = _docker_run(["docker", "image", "inspect", PARSER_IMAGE])
    if rc_img != 0:
        log_lines.append(out_img)
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = f"parser image '{PARSER_IMAGE}' not found"
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
        ev.save()
        return JsonResponse({
            "ok": False,
            "error": ev.parse_message,
            "log_tail": out_img[-2000:],
        }, status=500)

    def run_parser(kind: str, in_abs: Path, out_csv_name: str) -> tuple[bool, str]:
        # path ที่ parser container จะเห็น (ผ่าน named volume 'media' → mountpoint)
        rel_in = in_abs.relative_to(extracted).as_posix()
        in_path = f"{DOCKER_VOLUME_MOUNTPOINT}/extracted/{ev.id}/{rel_in}"
        out_dir = f"{DOCKER_VOLUME_MOUNTPOINT}/parsed/{ev.id}"

        args = ["docker", "run", "--rm"]
        if PARSER_PLATFORM:
            args += ["--platform", PARSER_PLATFORM]
        args += [
            "-v", f"{DOCKER_VOLUME_MEDIA}:{DOCKER_VOLUME_MOUNTPOINT}",
            PARSER_IMAGE,
        ]

        if kind == "mft":
            args += ["mft", in_path, out_dir, out_csv_name]
        elif kind == "amcache":
            args += ["amcache", in_path, out_dir, out_csv_name]
        else:
            raise ValueError("unknown kind")

        rc, out = _docker_run(args)
        log_lines.append(f"$ {' '.join(args)}\n{out}\n(rc={rc})\n")
        return rc == 0, out

    # ==== รันตามที่พบไฟล์ ====
    mft_rel: Optional[str] = None
    amcache_focus_rel: Optional[str] = None
    amcache_all_rels: list[str] = []

    # -- MFT --
    if mft_path:
        ok, _ = run_parser("mft", mft_path, "mft.csv")
        mft_csv_abs = parsed_dir / "mft.csv"
        mft_listing_abs = parsed_dir / "mft_FileListing.csv"
        if ok and _exists_nonempty(mft_csv_abs):
            mft_rel = f"parsed/{ev.id}/mft.csv"
            if hasattr(ev, "mft_csv_path"):
                ev.mft_csv_path = mft_rel
        # เก็บไฟล์ listing ไว้ใน summary
        if _exists_nonempty(mft_listing_abs):
            summ = dict(getattr(ev, "summary", {}) or {})
            summ["mft_filelisting"] = f"parsed/{ev.id}/mft_FileListing.csv"
            ev.summary = summ
    else:
        log_lines.append("! $MFT not found under extracted path\n")

    # -- Amcache: โฟกัสที่ amcache_UnassociatedFileEntries.csv --
    if amc_path:
        ok, out_text = run_parser("amcache", amc_path, "amcache.csv")
        focus_abs = parsed_dir / "amcache_UnassociatedFileEntries.csv"
        if _exists_nonempty(focus_abs):
            amcache_focus_rel = f"parsed/{ev.id}/amcache_UnassociatedFileEntries.csv"
            if hasattr(ev, "amcache_csv_path"):
                ev.amcache_csv_path = amcache_focus_rel
        else:
            # fallback: เก็บลิสต์ amcache_*.csv ทั้งหมด
            for p in sorted(parsed_dir.glob("amcache*.csv")):
                if _exists_nonempty(p):
                    rel = f"parsed/{ev.id}/{p.name}"
                    amcache_all_rels.append(rel)
            if amcache_all_rels and not amcache_focus_rel:
                # ถ้าไม่มีไฟล์ focus ก็ชี้ไฟล์แรกไว้เป็น representative
                amcache_focus_rel = amcache_all_rels[0]
                if hasattr(ev, "amcache_csv_path"):
                    ev.amcache_csv_path = amcache_focus_rel
            if not amcache_all_rels and not amcache_focus_rel:
                log_lines.append("! Amcache parsed but no amcache*.csv found\n")
        # เก็บลิสต์ไฟล์ amcache ทั้งหมดลง summary
        if amcache_all_rels:
            summ = dict(getattr(ev, "summary", {}) or {})
            summ["amcache_csvs"] = amcache_all_rels
            ev.summary = summ
    else:
        log_lines.append("! Amcache.hve not found under extracted path\n")

    # ==== อัปเดตสถานะ + นับจำนวนเรคคอร์ดแบบเร็ว (optional) ====
    produced_any = bool(mft_rel or amcache_focus_rel)
    if produced_any:
        ev.parse_status = getattr(Evidence.ParseStatus, "DONE", "DONE")
        ev.parse_message = "parsed"
        summary = dict(getattr(ev, "summary", {}) or {})
        try:
            # นับแถวอย่างหยาบ (ระวังไฟล์ใหญ่มาก ๆ) — ถ้าไฟล์ > 200MB ให้ข้าม
            def _count_rows_if_small(path: Path) -> Optional[int]:
                try:
                    if path.exists() and path.stat().st_size <= 200 * 1024 * 1024:
                        with open(path, "r", errors="ignore") as r:
                            return max(0, sum(1 for _ in r) - 1)
                except Exception:
                    pass
                return None

            if mft_rel:
                cnt = _count_rows_if_small(Path(settings.MEDIA_ROOT) / mft_rel)
                if cnt is not None:
                    summary["mft_rows"] = cnt
            if amcache_focus_rel:
                cnt = _count_rows_if_small(Path(settings.MEDIA_ROOT) / amcache_focus_rel)
                if cnt is not None:
                    summary["amcache_rows"] = cnt
        except Exception:
            pass
        ev.summary = summary
    else:
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = "no artifact parsed"

    # เก็บ log
    if hasattr(ev, "parse_log"):
        ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
    ev.save()
    return JsonResponse({
        "ok": ev.parse_status == getattr(Evidence.ParseStatus, "DONE", "DONE"),
        "status": ev.parse_status,  # ให้ frontend อ่าน field เดิมได้
        "mft_csv": (settings.MEDIA_URL + mft_rel) if mft_rel else None,
        # ชี้ไปที่ amcache_UnassociatedFileEntries.csv โดยตรงถ้ามี, ไม่งั้น representative
        "amcache_csv": (settings.MEDIA_URL + amcache_focus_rel) if amcache_focus_rel else None,
        # รายการ amcache_*.csv ทั้งหมด (ถ้ามี)
        "amcache_all": [settings.MEDIA_URL + x for x in amcache_all_rels] if amcache_all_rels else [],
        # ถ้ามี mft listing
        "mft_filelisting": (
            settings.MEDIA_URL + ev.summary.get("mft_filelisting")
            if getattr(ev, "summary", None) and ev.summary.get("mft_filelisting") else None
        ),
        "log_tail": "\n".join(log_lines[-10:]),
        "summary": ev.summary,
    })


def evidence_detail_api(request, ev_id):
    """
    คืนรายละเอียด Evidence (ให้หน้า UI ไป poll ดูสถานะ/ลิงก์ CSV ได้)
    """
    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    zip_url = (settings.MEDIA_URL + ev.stored_path) if getattr(ev, "stored_path", None) else None

    # ถ้าโมเดลไม่มี mft_csv_path/amcache_csv_path ให้ derive จาก summary / โครงไดเรกทอรี
    mft_rel = getattr(ev, "mft_csv_path", None)
    amc_rel = getattr(ev, "amcache_csv_path", None)

    if not mft_rel:
        p = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id) / "mft.csv"
        if p.exists():
            mft_rel = f"parsed/{ev.id}/mft.csv"
    if not amc_rel:
        p = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id) / "amcache_UnassociatedFileEntries.csv"
        if p.exists():
            amc_rel = f"parsed/{ev.id}/amcache_UnassociatedFileEntries.csv"

    return JsonResponse({
        "id": str(ev.id),
        "case_id": str(ev.case_id),
        "status": ev.parse_status,
        "original_name": ev.original_filename,
        "size_bytes": ev.size_bytes,
        "sha256": ev.sha256,
        "zip_file": zip_url,
        "extract_path": getattr(ev, "extracted_dir", None) or None,
        "source_system": ev.source_system,
        "acquisition_tool": ev.acquisition_tool,
        "notes": ev.notes,
        "created_at": ev.created_at.isoformat(),
        "updated_at": ev.updated_at.isoformat(),
        "mft_csv": (settings.MEDIA_URL + mft_rel) if mft_rel else None,
        "amcache_csv": (settings.MEDIA_URL + amc_rel) if amc_rel else None,
        "summary": ev.summary,
    })