from django.shortcuts import render

import random

# Create your views here.
def dashboard(req):
	return render(req, 'dashboard.html')

def upload_evidence(req):
	return render(req, 'upload.html')

def view_result(req):
	return render(req, 'result.html', {'evidence_id': 1})