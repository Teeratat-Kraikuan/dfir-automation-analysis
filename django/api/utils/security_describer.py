# django/api/utils/security_describer.py
from __future__ import annotations
from typing import Dict, Any, Tuple
import json

def _pick(d: Dict[str, Any], *ks, default: str = "") -> str:
    for k in ks:
        v = d.get(k)
        if v not in (None, "", "NULL"):
            return str(v)
    return default

def _ip(ed: Dict[str, Any]) -> str:
    return _pick(ed, "IpAddress","Ip","SourceIp","SourceIPAddress",
                 "SourceNetworkAddress","ClientAddress","RemoteHost")

def _maybe_parse_payload(ed: Dict[str, Any]) -> Dict[str, Any]:
    """
    EvtxECmd บางทีจะยัด JSON ไว้ในคีย์ 'Payload' หรือสาดเป็น PayloadData1..n
    ดึงออกมาใส่ ed เพิ่ม (ไม่ทับของเดิม)
    """
    out = dict(ed)
    p = ed.get("Payload")
    if isinstance(p, str) and p.strip().startswith("{"):
        try:
            j = json.loads(p)
            evd = j.get("EventData") or {}
            # ปกติ j["EventData"] อาจเป็น dict หรือ {"Data":"..."} แล้วแต่ log
            if isinstance(evd, dict):
                for k, v in evd.items():
                    out.setdefault(k, v)
            elif isinstance(evd, str):
                out.setdefault("EventDataString", evd)
        except Exception:
            pass
    # เผื่อ parser ออกเป็น PayloadData1..n (ไม่รู้ order แน่ชัด)
    # ถ้าต้อง mapping เพิ่มภายหลังค่อยมาเติม logic ตรงนี้
    return out

def describe_4624(ed0: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    ed = _maybe_parse_payload(ed0)
    lt  = _pick(ed, "LogonType","Logon_Type")
    who = _pick(ed, "TargetUserName","UserName","AccountName","SubjectUserName")
    dom = _pick(ed, "TargetDomainName","DomainName","SubjectDomainName")
    src = _ip(ed)
    ws  = _pick(ed, "WorkstationName","Workstation")
    pkg = _pick(ed, "AuthenticationPackageName","PackageName")
    proc= _pick(ed, "ProcessName","NewProcessName","Image")

    user_disp = f"{dom}\\{who}" if dom and who else (who or "(unknown)")
    from_str  = src or ws or "-"
    desc = f"Logon success (4624) type={lt or '?'} user={user_disp} from={from_str}"
    if pkg:  desc += f" pkg={pkg}"
    if proc: desc += f" proc={proc}"

    norm = {
        "actor": who or "",
        "domain": dom or "",
        "src_ip": src or "",
        "logon_type": lt or "",
        "workstation": ws or "",
        "auth_package": pkg or "",
        "process": proc or "",
        "status": "Success",
    }
    return desc, norm

def describe_4625(ed0: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    ed = _maybe_parse_payload(ed0)
    lt   = _pick(ed, "LogonType","Logon_Type")
    who  = _pick(ed, "TargetUserName","UserName","AccountName")
    dom  = _pick(ed, "TargetDomainName","DomainName")
    src  = _ip(ed)
    rsn  = _pick(ed, "FailureReason","Status","SubStatus","ErrorCode")
    ws   = _pick(ed, "WorkstationName","Workstation")

    user_disp = f"{dom}\\{who}" if dom and who else (who or "(unknown)")
    from_str  = src or ws or "-"
    desc = f"Logon failure (4625) type={lt or '?'} user={user_disp} from={from_str}"
    if rsn: desc += f" reason={rsn}"

    norm = {
        "actor": who or "",
        "domain": dom or "",
        "src_ip": src or "",
        "logon_type": lt or "",
        "workstation": ws or "",
        "status": "Failure",
        "failure_reason": rsn or "",
    }
    return desc, norm

def describe_generic(core: Dict[str, Any], ed0: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    ed = _maybe_parse_payload(ed0)
    msg = core.get("message") or _pick(ed, "MapDescription")
    if not msg:
        msg = " ".join(filter(None, [
            _pick(ed,"Provider"), _pick(ed,"Channel"), _pick(ed,"Level")
        ])).strip() or "(no message)"
    norm = {
        "actor": _pick(ed,"UserName","TargetUserName","SubjectUserName","AccountName"),
        "domain": _pick(ed,"TargetDomainName","SubjectDomainName","DomainName"),
        "src_ip": _ip(ed),
    }
    return msg, norm

def describe_event(event_id: int, core: Dict[str, Any], ed: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if event_id == 4624:
        return describe_4624(ed)
    if event_id == 4625:
        return describe_4625(ed)
    return describe_generic(core, ed)
