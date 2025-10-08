from django.shortcuts import render, get_object_or_404, redirect
from api.models import Evidence  # ใช้ดึงรายการหลักฐาน

def dashboard(req):
    return render(req, 'dashboard.html')

def upload_evidence(req):
    return render(req, 'upload.html')

# หน้า index: แสดงรายการ evidence ล่าสุด แล้วกดไป result/<uuid> ได้
def results_index(req):
    items = Evidence.objects.order_by('-created_at')[:50]
    return render(req, 'results_index.html', {'items': items})

# หน้า result ของ evidence รายตัว
def view_result(req, ev_id):
    ev = get_object_or_404(Evidence, id=ev_id)
    return render(req, 'result.html', {'evidence_id': ev.id})
