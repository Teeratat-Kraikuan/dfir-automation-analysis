import os
import hashlib
import zipfile
import subprocess
import shutil
import csv
from pathlib import Path
from typing import Tuple, Optional, Set
from datetime import datetime

from django.views.decorators.http import require_GET
import shutil as _shutil
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db.models import Q, F, Count
from django.shortcuts import get_object_or_404

from .models import Case, Evidence, MFTEntry, AmcacheEntry, SecurityEvent
from .utils.security_describer import describe_event


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


def _find_winevt_logs_dir(extracted_root: Path) -> Optional[Path]:
    """
    พยายามหาโฟลเดอร์ winevt/Logs จากเค้าโครง KAPE/Windows ปกติ
    ถ้าไม่เจอในตำแหน่งมาตรฐาน จะเดินหา dir ที่ลงท้าย 'winevt/Logs'
    """
    candidates = [
        extracted_root / "KAPE" / "Triage" / "Windows" / "System32" / "winevt" / "Logs",
        extracted_root / "Triage" / "Windows" / "System32" / "winevt" / "Logs",
        extracted_root / "Windows" / "System32" / "winevt" / "Logs",
        extracted_root / "Windows" / "winevt" / "Logs",
        extracted_root / "System32" / "winevt" / "Logs",
        extracted_root / "winevt" / "Logs",
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p

    # fallback เดินหา
    for dirpath, dirnames, _filenames in os.walk(extracted_root):
        # speed: prune 'Parsed'
        if "Parsed" in dirnames:
            dirnames.remove("Parsed")
        path = Path(dirpath)
        parts = [pp.lower() for pp in path.parts[-2:]]  # last 2 parts
        if len(parts) >= 2 and parts[-2] == "winevt" and parts[-1] == "logs":
            return path
    return None


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
      - หา winevt/Logs (Windows Event Logs)
      - docker run PARSER_IMAGE เพื่อแปลง CSV → MEDIA_ROOT/parsed/<id>/
      - จากนั้น ingest CSV → DB (MFTEntry / AmcacheEntry / SecurityEvent)
      - ทำแบบล้างชนิดต่อชนิดเพื่อลด peak memory
    """
    ev_id = request.POST.get("id")
    if not ev_id:
        return HttpResponseBadRequest("missing id")

    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    # หา extracted dir
    extracted = Path(ev.extracted_dir) if getattr(ev, "extracted_dir", None) \
        else Path(settings.MEDIA_ROOT) / "extracted" / str(ev.id)
    if not extracted.exists():
        return HttpResponseBadRequest("extracted path not found")

    parsed_dir = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    mft_path, amc_path = _find_kape_artifacts(extracted)
    evtx_dir = _find_winevt_logs_dir(extracted)

    ev.parse_status = getattr(Evidence.ParseStatus, "RUNNING", "RUNNING")
    ev.parse_message = "parsing"
    ev.save(update_fields=["parse_status", "parse_message"])

    log_lines: list[str] = []
    log_lines.append("[django] start_parse_api: begin")

    # ต้องมี docker CLI
    if shutil.which("docker") is None:
        msg = "docker CLI not found in django container"
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = msg
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + "\n" + msg
        ev.save(update_fields=["parse_status", "parse_message", "parse_log"])
        return JsonResponse({"ok": False, "error": msg}, status=500)

    # ตรวจ image
    rc_img, out_img = _docker_run(["docker", "image", "inspect", PARSER_IMAGE])
    if rc_img != 0:
        log_lines.append(out_img)
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = f"parser image '{PARSER_IMAGE}' not found"
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
        ev.save(update_fields=["parse_status", "parse_message", "parse_log"])
        return JsonResponse({
            "ok": False,
            "error": ev.parse_message,
            "log_tail": out_img[-2000:],
        }, status=500)

    def run_parser(kind: str, in_abs: Path | None, out_csv_name: str) -> tuple[bool, str]:
        """
        kind: 'mft' | 'amcache' | 'evtx-dir'
        in_abs: สำหรับ evtx-dir เป็น 'directory', ที่เหลือเป็นไฟล์
        """
        if in_abs is None:
            return False, "no input"

        # สำหรับ 'evtx-dir' เราจะส่งเป็นโฟลเดอร์ relative to extracted
        rel_in = in_abs.relative_to(extracted).as_posix()
        in_path = f"{DOCKER_VOLUME_MOUNTPOINT}/extracted/{ev.id}/{rel_in}"
        out_dir = f"{DOCKER_VOLUME_MOUNTPOINT}/parsed/{ev.id}"

        args = ["docker", "run", "--rm"]
        if PARSER_PLATFORM:
            args += ["--platform", PARSER_PLATFORM]
        args += ["-v", f"{DOCKER_VOLUME_MEDIA}:{DOCKER_VOLUME_MOUNTPOINT}", PARSER_IMAGE]

        if kind == "mft":
            args += ["mft", in_path, out_dir, out_csv_name]
        elif kind == "amcache":
            args += ["amcache", in_path, out_dir, out_csv_name]
        elif kind == "evtx-dir":
            args += ["evtx-dir", in_path, out_dir, out_csv_name]
        else:
            raise ValueError("unknown kind")

        rc, out = _docker_run(args)
        log_lines.append(f"$ {' '.join(args)}\n{out}\n(rc={rc})\n")
        return rc == 0, out

    # ==== รันตามที่พบไฟล์/โฟลเดอร์ ====
    mft_rel: Optional[str] = None
    amcache_focus_rel: Optional[str] = None
    amcache_all_rels: list[str] = []
    evtx_rel: Optional[str] = None

    # MFT
    if mft_path:
        ok, _ = run_parser("mft", mft_path, "mft.csv")
        mft_csv_abs = parsed_dir / "mft.csv"
        mft_listing_abs = parsed_dir / "mft_FileListing.csv"
        if ok and _exists_nonempty(mft_csv_abs):
            mft_rel = f"parsed/{ev.id}/mft.csv"
            if hasattr(ev, "mft_csv_path"):
                ev.mft_csv_path = mft_rel
        if _exists_nonempty(mft_listing_abs):
            summ = dict(getattr(ev, "summary", {}) or {})
            summ["mft_filelisting"] = f"parsed/{ev.id}/mft_FileListing.csv"
            ev.summary = summ
    else:
        log_lines.append("! $MFT not found under extracted path\n")

    # Amcache
    if amc_path:
        ok, _out = run_parser("amcache", amc_path, "amcache.csv")
        focus_abs = parsed_dir / "amcache_UnassociatedFileEntries.csv"
        if _exists_nonempty(focus_abs):
            amcache_focus_rel = f"parsed/{ev.id}/amcache_UnassociatedFileEntries.csv"
            if hasattr(ev, "amcache_csv_path"):
                ev.amcache_csv_path = amcache_focus_rel
        else:
            for p in sorted(parsed_dir.glob("amcache*.csv")):
                if _exists_nonempty(p):
                    rel = f"parsed/{ev.id}/{p.name}"
                    amcache_all_rels.append(rel)
            if amcache_all_rels and not amcache_focus_rel:
                amcache_focus_rel = amcache_all_rels[0]
                if hasattr(ev, "amcache_csv_path"):
                    ev.amcache_csv_path = amcache_focus_rel
            if not amcache_all_rels and not amcache_focus_rel:
                log_lines.append("! Amcache parsed but no amcache*.csv found\n")
        if amcache_all_rels:
            summ = dict(getattr(ev, "summary", {}) or {})
            summ["amcache_csvs"] = amcache_all_rels
            ev.summary = summ
    else:
        log_lines.append("! Amcache.hve not found under extracted path\n")

    # EVTX (Security/System/Application)
    if evtx_dir and evtx_dir.exists():
        ok, _out = run_parser("evtx-dir", evtx_dir, "evtx_all.csv")
        evtx_csv_abs = parsed_dir / "evtx_all.csv"
        if ok and _exists_nonempty(evtx_csv_abs):
            evtx_rel = f"parsed/{ev.id}/evtx_all.csv"
            summ = dict(getattr(ev, "summary", {}) or {})
            summ["evtx_csv"] = evtx_rel
            ev.summary = summ
    else:
        log_lines.append("! winevt/Logs directory not found under extracted path\n")

    # ==== อัปเดตสถานะ + สรุปเร็ว ๆ ====
    produced_any = bool(mft_rel or amcache_focus_rel or evtx_rel)
    if produced_any:
        ev.parse_status = getattr(Evidence.ParseStatus, "DONE", "DONE")
        ev.parse_message = "parsed"
        summary = dict(getattr(ev, "summary", {}) or {})
        try:
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
            if evtx_rel:
                cnt = _count_rows_if_small(Path(settings.MEDIA_ROOT) / evtx_rel)
                if cnt is not None:
                    summary["evtx_rows"] = cnt
        except Exception:
            pass
        ev.summary = summary
    else:
        ev.parse_status = getattr(Evidence.ParseStatus, "FAILED", "FAILED")
        ev.parse_message = "no artifact parsed"

    # ===== Ingest → DB (ทีละชนิด ลด peak memory) =====
    try:
        if mft_rel:
            with transaction.atomic():
                MFTEntry.objects.filter(evidence=ev).delete()
                mft_csv_abs = Path(settings.MEDIA_ROOT) / mft_rel
                inserted = ingest_mft_csv_to_db(ev, mft_csv_abs, chunk=1000)
                summary = dict(getattr(ev, "summary", {}) or {})
                summary["mft_rows_db"] = inserted
                ev.summary = summary
                ev.save(update_fields=["summary"])

        if amcache_focus_rel:
            with transaction.atomic():
                AmcacheEntry.objects.filter(evidence=ev).delete()
                amc_csv_abs = Path(settings.MEDIA_ROOT) / amcache_focus_rel
                inserted = ingest_amcache_csv_to_db(ev, amc_csv_abs, chunk=1000)
                summary = dict(getattr(ev, "summary", {}) or {})
                summary["amcache_rows_db"] = inserted
                ev.summary = summary
                ev.save(update_fields=["summary"])

        if evtx_rel:
            with transaction.atomic():
                SecurityEvent.objects.filter(evidence=ev).delete()
                evtx_csv_abs = Path(settings.MEDIA_ROOT) / evtx_rel
                inserted = ingest_evtx_csv_to_db(ev, evtx_csv_abs, chunk=2000)
                summary = dict(getattr(ev, "summary", {}) or {})
                summary["security_events_rows_db"] = inserted
                ev.summary = summary
                ev.save(update_fields=["summary"])
    except Exception as _ing_e:
        if hasattr(ev, "parse_log"):
            ev.parse_log = (ev.parse_log or "") + f"\ningest error: {repr(_ing_e)}"
            ev.save(update_fields=["parse_log"])

    # เก็บ log และตอบกลับ
    if hasattr(ev, "parse_log"):
        ev.parse_log = (ev.parse_log or "") + "\n".join(log_lines)
    ev.save()

    return JsonResponse({
        "ok": ev.parse_status == getattr(Evidence.ParseStatus, "DONE", "DONE"),
        "status": ev.parse_status,
        "mft_csv": (settings.MEDIA_URL + mft_rel) if mft_rel else None,
        "amcache_csv": (settings.MEDIA_URL + amcache_focus_rel) if amcache_focus_rel else None,
        "amcache_all": [settings.MEDIA_URL + x for x in amcache_all_rels] if amcache_all_rels else [],
        "evtx_csv": (settings.MEDIA_URL + evtx_rel) if evtx_rel else None,
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

    # ลิงก์ EVTX ถ้ามี
    evtx_rel = None
    p_ev = Path(settings.MEDIA_ROOT) / "parsed" / str(ev.id) / "evtx_all.csv"
    if p_ev.exists():
        evtx_rel = f"parsed/{ev.id}/evtx_all.csv"

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
        "evtx_csv": (settings.MEDIA_URL + evtx_rel) if evtx_rel else None,
        "summary": ev.summary,
    })


# ======== [RESULT FEEDERS] CSV → JSON สำหรับหน้า result ========

def _csv_path_or_404(ev, rel_path: str | None) -> Path:
    if not rel_path:
        raise Http404("csv path not found")
    p = Path(settings.MEDIA_ROOT) / rel_path
    if not p.exists():
        raise Http404("csv file not found")
    return p

def _str_to_int_default(s: str, default=0):
    try:
        return int((s or "0").replace(",", "").strip())
    except Exception:
        return default

def _parse_ts_guess(s: str):
    """
    แปลง string → datetime ให้เป็น timezone-aware เสมอเมื่อ USE_TZ=True
    รองรับฟอร์แมตพื้นฐาน; ถ้าแปลงไม่ได้คืน None
    """
    s = (s or "").strip()
    if not s:
        return None

    # ISO/มาตรฐานของ Django ก่อน (เร็วสุด)
    dt = parse_datetime(s)
    if dt:
        if settings.USE_TZ and dt.tzinfo is None:
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    # ฟอร์แมตที่พบได้บ่อย + เผื่อ microseconds
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if settings.USE_TZ:
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        except Exception:
            pass
    return None


def _as_bool(s: str) -> bool:
    return str(s or "").strip().lower() in ("1", "true", "yes")

# ---------- MFT (ORM) ----------
def mft_rows_api(request, ev_id):
    ev = Evidence.objects.filter(id=ev_id).first()
    if not ev:
        raise Http404("evidence not found")

    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 50)), 1000)
    q = (request.GET.get("q", "") or "").strip()
    type_filter = (request.GET.get("type", "") or "").lower()   # "", "file", "dir"
    size_bucket = (request.GET.get("size_bucket", "") or "").lower()
    sort_key = request.GET.get("sort", "EntryNumber")
    order = (request.GET.get("order", "asc") or "asc").lower()

    qs = MFTEntry.objects.filter(evidence=ev)

    if q:
        qs = qs.filter(Q(file_name__icontains=q) | Q(full_path__icontains=q))
    if type_filter == "file":
        qs = qs.filter(is_directory=False)
    elif type_filter == "dir":
        qs = qs.filter(is_directory=True)

    if size_bucket == "small":
        qs = qs.filter(size_bytes__lt=1*1024*1024)
    elif size_bucket == "medium":
        qs = qs.filter(size_bytes__gte=1*1024*1024, size_bytes__lte=100*1024*1024)
    elif size_bucket == "large":
        qs = qs.filter(size_bytes__gt=100*1024*1024)

    sort_map = {
        "EntryNumber": "entry_number",
        "FileName": "file_name",
        "FullPath": "full_path",
        "Size": "size_bytes",
        "Created": "created_ts",
        "Modified": "modified_ts",
    }
    sfield = sort_map.get(sort_key, "entry_number")
    if order == "desc":
        sfield = "-" + sfield
    qs = qs.order_by(sfield)

    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs.values(
        "entry_number","file_name","full_path","size_bytes","created_ts","modified_ts","is_directory"
    )[start:start + page_size])

    payload = []
    for r in rows:
        payload.append({
            "EntryNumber": r["entry_number"],
            "FileName": r["file_name"],
            "FullPath": r["full_path"],
            "Size": f'{r["size_bytes"]:,}',
            "Created": r["created_ts"].isoformat() if r["created_ts"] else "",
            "Modified": r["modified_ts"].isoformat() if r["modified_ts"] else "",
            "IsDirectory": r["is_directory"],
        })

    return JsonResponse({
        "page": page,
        "page_size": page_size,
        "total": total,
        "start_index": start + 1 if total else 0,
        "end_index": min(start + page_size, total),
        "rows": payload,
    })

# ---------- Amcache (ORM) ----------
def amcache_rows_api(request, ev_id):
    ev = Evidence.objects.filter(id=ev_id).first()
    if not ev:
        raise Http404("evidence not found")

    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 50)), 1000)
    q = (request.GET.get("q", "") or "").strip()
    publisher = (request.GET.get("publisher", "") or "").strip()
    sort_key = request.GET.get("sort", "AppName")
    order = (request.GET.get("order", "asc") or "asc").lower()

    qs = AmcacheEntry.objects.filter(evidence=ev)
    if q:
        qs = qs.filter(
            Q(app_name__icontains=q) |
            Q(version__icontains=q) |
            Q(publisher__icontains=q) |
            Q(file_path__icontains=q)
        )
    if publisher:
        qs = qs.filter(publisher__iexact=publisher)

    sort_map = {
        "AppName": "app_name",
        "Version": "version",
        "Publisher": "publisher",
        "InstallDate": "install_date",
        "FilePath": "file_path",
    }
    sfield = sort_map.get(sort_key, "app_name")
    if order == "desc":
        sfield = "-" + sfield
    qs = qs.order_by(sfield)

    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs.values("app_name","version","publisher","install_date","file_path")[start:start + page_size])

    payload = []
    for r in rows:
        payload.append({
            "AppName": r["app_name"],
            "Version": r["version"] or "",
            "Publisher": r["publisher"] or "",
            "InstallDate": r["install_date"].isoformat() if r["install_date"] else "",
            "FilePath": r["file_path"] or "",
        })

    publishers = list(
        AmcacheEntry.objects.filter(evidence=ev)
        .exclude(publisher__isnull=True)
        .exclude(publisher__exact="")
        .values_list("publisher", flat=True)
        .distinct()
        .order_by("publisher")
    )

    return JsonResponse({
        "page": page,
        "page_size": page_size,
        "total": total,
        "start_index": start + 1 if total else 0,
        "end_index": min(start + page_size, total),
        "rows": payload,
        "publishers": publishers,
    })

# === [ADD] helpers: normalizer + safe-int + ts parse ที่ใช้ซ้ำ ===
def _norm_key(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def _canon_row(row: dict) -> dict:
    return {_norm_key(k): v for k, v in row.items()}

def _pick(nrow: dict, *aliases: str) -> str:
    for a in aliases:
        v = nrow.get(_norm_key(a), "")
        if v not in (None, "", "NULL"):
            return v
    return ""

def _to_int(s: str, default=0) -> int:
    try:
        return int(str(s or "0").replace(",", "").strip())
    except Exception:
        return default

def _to_bool(s: str) -> bool:
    return str(s or "").strip().lower() in ("1","true","yes")

# === [ADD] Ingesters: อ่าน CSV ทีละบล็อกแล้ว bulk_create ลง DB ===
def ingest_mft_csv_to_db(ev: Evidence, csv_path: Path, chunk=1000) -> int:
    """
    อ่าน parsed/mft.csv → MFTEntry แบบ batch เล็กลง (default 1000)
    - ใช้ FileSize เป็นหลักตามโครง CSV ที่ให้มา
    - ถ้าไม่มี FullPath ให้ประกอบ Path จาก ParentPath + FileName
    - โฟลเดอร์ size เป็น 0 เสมอ
    """
    from .models import MFTEntry
    saved = 0
    batch = []

    def _join_path(parent: str, name: str) -> str:
        parent = (parent or "").strip()
        name = (name or "").strip()
        if not parent and not name:
            return "."
        if parent in (".", ""):
            return name or "."
        # ใช้สไตล์ Windows ให้สวยตา (กันซ้ำเครื่องหมายคั่น)
        sep = "\\" if "\\" in parent or "\\" in name else "\\"
        return parent.rstrip("\\/") + sep + name

    with transaction.atomic():
        with open(csv_path, "r", newline="", errors="ignore") as r:
            dr = csv.DictReader(r)
            for raw in dr:
                n = _canon_row(raw)

                entry_number = _to_int(_pick(n, "EntryNumber","Entry","RecordNumber"))

                file_name  = _pick(n, "FileName","Name")
                # ถ้า CSV ไม่มี FullPath (ตามตัวอย่าง) ให้สร้างจาก ParentPath + FileName
                full_path  = _pick(n, "FullPath","FilePath")
                if not full_path:
                    parent = _pick(n, "ParentPath","Path")
                    full_path = _join_path(parent, file_name)

                # โฟลเดอร์?
                is_dir = _to_bool(_pick(n, "IsDirectory","IsDir","Directory","Dir"))

                # ขนาดไฟล์: ใช้ FileSize เป็นหลัก ตามที่คุณยืนยันมา
                size_bytes = 0 if is_dir else _to_int(_pick(n, "FileSize","LogicalSize","Size"))

                # เวลา
                created  = _parse_ts_guess(_pick(n, "Created0x10","Created","CreationTime","CreationTimeUTC"))
                modified = _parse_ts_guess(_pick(n, "LastModified0x10","Modified0x10","Modified","ModifiedTime","LastWriteTime"))
                accessed = _parse_ts_guess(_pick(n, "LastAccess0x10","Accessed0x10","Accessed","AccessTime"))
                mftchg   = _parse_ts_guess(_pick(n, "LastRecordChange0x10","MFTChanged0x10","MFTChanged","EntryModifiedTime"))

                batch.append(MFTEntry(
                    evidence=ev,
                    entry_number=entry_number,
                    is_directory=is_dir,
                    file_name=file_name or "",
                    full_path=full_path or ".",
                    size_bytes=size_bytes,
                    created_ts=created,
                    modified_ts=modified,
                    accessed_ts=accessed,
                    mft_changed_ts=mftchg,
                ))

                if len(batch) >= chunk:
                    MFTEntry.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
                    saved += len(batch)
                    batch.clear()

        if batch:
            MFTEntry.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
            saved += len(batch)

    return saved



def ingest_amcache_csv_to_db(ev: Evidence, csv_path: Path, chunk=1000) -> int:
    """
    อ่าน amcache_UnassociatedFileEntries.csv → AmcacheEntry
    ใช้ batch เล็กลงเพื่อลด peak memory และทำเวลาให้ aware
    """
    from .models import AmcacheEntry
    saved = 0
    batch = []

    with transaction.atomic():
        with open(csv_path, "r", newline="", errors="ignore") as r:
            dr = csv.DictReader(r)
            for raw in dr:
                n = _canon_row(raw)

                app_name = _pick(n, "ProgramName","AppName","ProductName")
                if not app_name:
                    continue

                version    = _pick(n, "Version","FileVersion","ProductVersion")
                publisher  = _pick(n, "Publisher","Company","CompanyName")
                install_dt = _parse_ts_guess(_pick(n, "InstallDate","InstallDateTime","InstallDateUTC","FirstInserted","FirstTime"))
                file_path  = _pick(n, "Path","FilePath","FullPath","KeyPath")
                sha1       = _pick(n, "SHA1","FileSHA1","SHA1Hash")
                pe_hash    = _pick(n, "PEHash","PEHash32","PEHash64")

                batch.append(AmcacheEntry(
                    evidence=ev,
                    app_name=app_name,
                    version=version,
                    publisher=publisher,
                    install_date=install_dt,
                    file_path=file_path,
                    sha1=sha1,
                    pe_hash=pe_hash,
                    product_name=_pick(n, "ProductName"),
                    extra={k: v for k, v in raw.items() if v not in (None, "")},
                ))

                if len(batch) >= chunk:
                    AmcacheEntry.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
                    saved += len(batch)
                    batch.clear()

        if batch:
            AmcacheEntry.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
            saved += len(batch)

    return saved

def ingest_evtx_csv_to_db(ev: Evidence, csv_path: Path, chunk=2000) -> int:
    saved = 0
    batch: list[SecurityEvent] = []

    core_keys = {
        "timestamp","timecreated","created",
        "eventid","provider","channel","computer","user","userid","recordnumber","recordid",
        "level","task","opcode","keywords","processid","threadid","message","sid","pid","tid","log","source"
    }

    with transaction.atomic():
        with open(csv_path, "r", newline="", errors="ignore", encoding="utf-8-sig") as r:
            dr = csv.DictReader(r)
            for raw in dr:
                n = _canon_row(raw)

                eid = _to_int(_pick(n, "EventID"))
                if eid == 0:
                    continue

                ts = _pick(n, "Timestamp","TimeCreated","Created")
                dt = _parse_ts_guess(ts)

                # --- แยกดิบที่เหลือเก็บลง event_data ---
                ed = {k: v for k, v in raw.items()
                      if v not in (None, "")
                      and _norm_key(k) not in core_keys}

                # message ดิบจาก CSV (ถ้ามี)
                msg_raw = _pick(n, "Message")

                # --- สร้าง description ตาม EventID (เก็บเพิ่ม ไม่ทับของดิบ) ---
                desc, norm = describe_event(
                    eid,
                    {"event_id": eid, "message": msg_raw},
                    ed
                )
                if msg_raw and desc != msg_raw:
                    ed["MessageRaw"] = msg_raw  # เก็บของดิบไว้ด้วย
                ed["__desc"] = desc           # เก็บคำอธิบายประกอบ
                ed["__norm"] = norm           # เก็บ normalized fields เผื่อใช้ค้น/สรุปต่อ

                event = SecurityEvent(
                    evidence   = ev,
                    timestamp  = dt,
                    channel    = _pick(n, "Channel","Log"),
                    provider   = _pick(n, "Provider","Source"),
                    event_id   = eid,
                    level      = _pick(n, "Level"),
                    task       = _pick(n, "Task"),
                    opcode     = _pick(n, "Opcode"),
                    keywords   = _pick(n, "Keywords"),
                    record_id  = _to_int(_pick(n, "RecordNumber","RecordId")),
                    computer   = _pick(n, "Computer"),
                    user_sid   = _pick(n, "UserID","Sid"),
                    user_name  = _pick(n, "User"),
                    process_id = _to_int(_pick(n, "ProcessID","PID")),
                    thread_id  = _to_int(_pick(n, "ThreadID","TID")),
                    # เก็บ message ให้ “อ่านรู้เรื่อง” ก่อน (ถ้าไม่มีจะว่างก็ได้ แต่เรามี desc แล้ว)
                    message    = desc or msg_raw or "",
                    event_data = ed,
                )
                batch.append(event)

                if len(batch) >= chunk:
                    SecurityEvent.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
                    saved += len(batch)
                    batch.clear()

        if batch:
            SecurityEvent.objects.bulk_create(batch, ignore_conflicts=True, batch_size=chunk)
            saved += len(batch)

    return saved


@require_GET
def parser_preflight_api(request):
    """
    ตรวจความพร้อมก่อน run parser:
      - docker cli
      - image มีในเครื่อง
      - media dirs เขียนได้
      - เนื้อที่ดิสก์เหลือพอ
    """
    checks = {}

    # docker cli
    checks["docker_cli"] = _shutil.which("docker") is not None

    # image
    if checks["docker_cli"]:
        rc, out = _docker_run(["docker", "image", "inspect", PARSER_IMAGE])
        checks["parser_image_ok"] = (rc == 0)
        checks["parser_image_output_tail"] = out[-1000:]
    else:
        checks["parser_image_ok"] = False
        checks["parser_image_output_tail"] = ""

    # media dirs
    media_root = Path(settings.MEDIA_ROOT)
    parsed_root = media_root / "parsed"
    extracted_root = media_root / "extracted"
    for p in (media_root, parsed_root, extracted_root):
        p.mkdir(parents=True, exist_ok=True)

    def _writable(p: Path) -> bool:
        try:
            testfile = p / ".write_test"
            with open(testfile, "w") as w:
                w.write("ok")
            testfile.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    checks["media_root_writable"] = _writable(media_root)
    checks["parsed_writable"] = _writable(parsed_root)
    checks["extracted_writable"] = _writable(extracted_root)

    # disk usage
    total, used, free = _shutil.disk_usage(str(media_root))
    checks["disk_total_bytes"] = int(total)
    checks["disk_used_bytes"] = int(used)
    checks["disk_free_bytes"] = int(free)

    return JsonResponse({"ok": all([
        checks["docker_cli"],
        checks["parser_image_ok"],
        checks["media_root_writable"],
        checks["parsed_writable"],
        checks["extracted_writable"],
    ]), "checks": checks})


# ---------- NEW: Security Events (ORM APIs) ----------
@require_GET
def security_events_rows_api(request, ev_id: int):
    # --- ตรวจ evidence ---
    try:
        ev = Evidence.objects.get(id=ev_id)
    except Evidence.DoesNotExist:
        raise Http404("evidence not found")

    # --- รับพารามิเตอร์จาก query ---
    q          = (request.GET.get("q") or "").strip()
    event_id   = (request.GET.get("event_id") or "").strip()
    logon_type = (request.GET.get("logon_type") or "").strip()

    try:
        page = int(request.GET.get("page", "1"))
        page_size = int(request.GET.get("page_size", "50"))
    except ValueError:
        page, page_size = 1, 50
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))

    sort  = (request.GET.get("sort") or "Timestamp").strip()
    order = (request.GET.get("order") or "desc").strip().lower()

    # ชื่อฟิลด์สำหรับ order_by (รองรับ SourceIP จาก __norm.src_ip ด้วย)
    sortmap = {
        "Timestamp":   "timestamp",
        "EventID":     "event_id",
        "User":        "user_name",
        "Computer":    "computer",
        "Message":     "message",                 # UI เดิม
        "Description": "message",                 # UI ใหม่
        "SourceIP":    "event_data____norm__src_ip",
    }
    sort_field = sortmap.get(sort, "timestamp")
    if order == "desc":
        sort_field = f"-{sort_field}"

    # --- base queryset ---
    qs = SecurityEvent.objects.filter(evidence=ev).order_by(sort_field)

    # --- ค้นหาแบบกว้าง (มองทั้ง message, __desc, MessageRaw, user/computer/provider) ---
    if q:
        qs = qs.filter(
            Q(message__icontains=q) |
            Q(event_data____desc__icontains=q) |
            Q(event_data__MessageRaw__icontains=q) |
            Q(user_name__icontains=q) |
            Q(computer__icontains=q) |
            Q(provider__icontains=q)
        )

    # --- filter ตาม event_id ---
    if event_id:
        try:
            eid_int = int(event_id)
            qs = qs.filter(event_id=eid_int)
        except ValueError:
            pass

    # --- filter logon_type (ดูทั้งคีย์ดิบและคีย์ normalize) ---
    if logon_type:
        qs = qs.filter(
            Q(event_id__in=[4624, 4625]) &
            (
                Q(event_data__LogonType=logon_type) |
                Q(event_data__Logon_Type=logon_type) |
                Q(event_data____norm__logon_type=logon_type)
            )
        )

    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size

    rows = []
    # ดึงฟิลด์ที่ต้องใช้ + event_data (JSON)
    fields = ("timestamp", "event_id", "message", "user_name", "computer", "event_data")
    for r in qs.values(*fields)[start:end]:
        ts = r["timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else (str(ts) if ts else "")

        ed = r.get("event_data") or {}
        norm = (ed.get("__norm") or {}) if isinstance(ed, dict) else {}

        # Source IP: ใช้ของ normalize ก่อน แล้วค่อย fallback
        src_ip = (
            norm.get("src_ip") or
            ed.get("IpAddress") or ed.get("Ip") or ed.get("SourceIp") or
            ed.get("SourceIPAddress") or ed.get("SourceNetworkAddress") or
            ed.get("ClientAddress") or ed.get("RemoteHost") or ""
        )

        # Description: ใช้ message ที่เราประกอบตอน ingest ก่อน > __desc > MapDescription > Payload
        desc = (
            r.get("message") or
            ed.get("__desc") or
            ed.get("MapDescription") or
            ed.get("Payload") or
            ""
        )

        # ผู้ใช้: ใช้ค่าที่ normalize ก่อน แล้วค่อย fallback ไปยังคีย์ดิบ
        user = (norm.get("actor") or r.get("user_name") or
                ed.get("TargetUserName") or ed.get("SubjectUserName") or
                ed.get("AccountName") or "")
        domain = (norm.get("domain") or
                  ed.get("TargetDomainName") or ed.get("SubjectDomainName") or ed.get("DomainName") or "")
        user_display = f"{domain}\\{user}" if domain and user else (user or "")

        # รายละเอียดที่ forensic ใช้บ่อย (โชว์ในแถวขยาย)
        details = {
            "LogonType":         norm.get("logon_type") or ed.get("LogonType") or ed.get("Logon_Type"),
            "WorkstationName":   ed.get("WorkstationName") or ed.get("Workstation"),
            "ProcessName":       norm.get("process") or ed.get("ProcessName") or ed.get("NewProcessName") or ed.get("Image"),
            "CommandLine":       ed.get("CommandLine") or ed.get("ProcessCommandLine") or ed.get("CmdLine"),
            "FailureReason":     norm.get("failure_reason") or ed.get("FailureReason") or ed.get("Status") or ed.get("SubStatus"),
            "AuthPackage":       norm.get("auth_package") or ed.get("AuthenticationPackageName") or ed.get("PackageName"),
            "TargetUserName":    ed.get("TargetUserName"),
            "TargetDomainName":  ed.get("TargetDomainName"),
            "SubjectUserName":   ed.get("SubjectUserName"),
            "SubjectDomainName": ed.get("SubjectDomainName"),
            "ServiceName":       ed.get("ServiceName"),
            "ObjectName":        ed.get("ObjectName"),
        }

        rows.append({
            "Timestamp": ts_str,
            "EventID": r.get("event_id") or "",
            "Description": desc,
            "User": user_display,
            "SourceIP": src_ip or "",
            "Computer": r.get("computer") or "",
            "Details": details,     # สำหรับ UI แสดงเพิ่มเติม (แถวขยาย)
            "Raw": ed,              # JSON ดิบ เผื่อเปิดดู/คัดลอก
        })

    return JsonResponse({
        "page": page,
        "page_size": page_size,
        "total": total,
        "start_index": start + 1 if total else 0,
        "end_index": min(end, total),
        "rows": rows,
    })


def security_events_summary_api(request, ev_id: int):
    ev = get_object_or_404(Evidence, id=ev_id)
    total = SecurityEvent.objects.filter(evidence=ev).count()
    return JsonResponse({"ok": True, "total": total})

@require_GET
def dashboard_overview_api(request):
    """
    สรุปตัวเลขและรายการเคสล่าสุดสำหรับหน้า Dashboard
    - totals: cases, evidence, active_cases, completed_cases
    - recent_cases: id, case_number, title, evidence_count, investigator, status, created_at
    """
    # --- ตัวเลขรวม ---
    total_cases = Case.objects.count()
    total_evidence = Evidence.objects.count()

    # เคสที่ยังมี evidence กำลังประมวลผลอยู่ (PENDING/RUNNING)
    active_case_ids = set(
        Evidence.objects
        .filter(parse_status__in=[
            getattr(Evidence.ParseStatus, "PENDING", "PENDING"),
            getattr(Evidence.ParseStatus, "RUNNING", "RUNNING"),
        ])
        .values_list("case_id", flat=True)
        .distinct()
    )

    # เคสที่ evidence ทั้งหมดเป็น DONE (และต้องมี evidence อย่างน้อย 1)
    agg = (
        Evidence.objects
        .values("case_id")
        .annotate(
            total=Count("id"),
            done=Count("id", filter=Q(parse_status=getattr(Evidence.ParseStatus, "DONE", "DONE"))),
        )
        .filter(total__gt=0)
        .filter(done=F("total"))
    )
    completed_case_ids = set([row["case_id"] for row in agg])

    # --- Recent cases (8 อันดับล่าสุด) + นับ evidence ---
    recent_qs = (
        Case.objects
        .annotate(evidence_count=Count("evidence"))
        .order_by("-id")[:8]   # ใช้ -id เป็นค่า default ที่เสถียร
    )

    recent_cases = []
    for c in recent_qs:
        # เดา investigator: ดึง uploaded_by ของ evidence ล่าสุด (ถ้ามี)
        last_ev = (
            Evidence.objects
            .filter(case=c)
            .select_related("uploaded_by")
            .order_by("-id")
            .first()
        )
        if last_ev and getattr(last_ev, "uploaded_by", None):
            inv = getattr(last_ev.uploaded_by, "username", "") or str(last_ev.uploaded_by)
        else:
            inv = ""

        if c.id in active_case_ids:
            status = "Active"
            status_badge = "warning"
        elif c.id in completed_case_ids:
            status = "Completed"
            status_badge = "success"
        else:
            status = "Idle"
            status_badge = "secondary"

        created_str = ""
        if hasattr(c, "created_at") and c.created_at:
            created_str = c.created_at.strftime("%Y-%m-%d %H:%M:%S")

        recent_cases.append({
            "id": c.id,
            "case_number": getattr(c, "case_number", f"CASE-{c.id}"),
            "title": getattr(c, "title", "") or "",
            "evidence_count": c.evidence_count or 0,
            "investigator": inv,
            "status": status,
            "status_badge": status_badge,  # ใช้บน UI
            "created_at": created_str,
        })

    payload = {
        "totals": {
            "cases": total_cases,
            "evidence": total_evidence,
            "active_cases": len(active_case_ids),
            "completed_cases": len(completed_case_ids),
        },
        "recent_cases": recent_cases,
    }
    return JsonResponse(payload)