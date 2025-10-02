// django/static/js/upload.js
(function () {
  // ---- CSRF helper (อ่านจาก cookie) ----
  function getCookie(name) {
    const v = ('; ' + document.cookie).split('; ' + name + '=');
    if (v.length === 2) return v.pop().split(';').shift();
  }
  const csrftoken = getCookie('csrftoken');

  // รอ DOM พร้อม (เราก็ใส่ defer ไว้แล้ว เผื่อไว้เพิ่มความชัวร์)
  document.addEventListener('DOMContentLoaded', function () {
    // Elements
    const area = document.getElementById('uploadArea');
    const fileInput = document.getElementById('evidenceFile');
    const uploadBtn = document.getElementById('uploadBtn');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const progressBar = document.querySelector('.progress-bar');
    const form = document.getElementById('uploadForm');

    const analysisStatus = document.getElementById('analysisStatus');
    const analysisSpinner = document.getElementById('analysisSpinner');
    const analysisProgress = document.getElementById('analysisProgress');
    const statusText = document.getElementById('statusText');
    const csvLinks = document.getElementById('csvLinks');
    const mftLinkRow = document.getElementById('mftLinkRow');
    const mftLink = document.getElementById('mftLink');
    const amcacheLinkRow = document.getElementById('amcacheLinkRow');
    const amcacheLink = document.getElementById('amcacheLink');

    let selectedFile = null;
    let lastUploadResp = null;

    // ---- Drag & Drop ----
    if (area) {
      const highlight = (on) => {
        if (on) area.classList.add('drag-over');
        else area.classList.remove('drag-over');
      };
      ['dragenter', 'dragover'].forEach(evt => {
        area.addEventListener(evt, (e) => { e.preventDefault(); e.stopPropagation(); highlight(true); });
      });
      ;['dragleave', 'drop'].forEach(evt => {
        area.addEventListener(evt, (e) => { e.preventDefault(); e.stopPropagation(); highlight(false); });
      });
      area.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files && files.length) {
          selectedFile = files[0];
          if (uploadBtn) uploadBtn.disabled = false;
        }
      });
      // click ทั้งบล็อกเพื่อเปิดไฟล์
      area.addEventListener('click', () => fileInput && fileInput.click());
    }

    // ---- Browse ----
    if (fileInput) {
      fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) {
          selectedFile = e.target.files[0];
          if (uploadBtn) uploadBtn.disabled = false;
        }
      });
    }

    // ---- Submit Upload ----
    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (!selectedFile) {
          alert('Please select a ZIP file first.');
          return;
        }

        const formData = new FormData(form);
        formData.set('evidence_file', selectedFile);

        if (uploadBtn) uploadBtn.disabled = true;
        if (progressBar) {
          progressBar.style.width = '0%';
          progressBar.textContent = '';
        }
        if (analysisStatus) analysisStatus.style.display = 'none';
        if (csvLinks) csvLinks.style.display = 'none';

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload-evidence/', true);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        if (csrftoken) xhr.setRequestHeader('X-CSRFToken', csrftoken);

        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable && progressBar) {
            const percent = Math.round((e.loaded / e.total) * 100);
            progressBar.style.width = percent + '%';
            progressBar.textContent = percent + '%';
          }
        };

        xhr.onload = function () {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              lastUploadResp = JSON.parse(xhr.responseText);
            } catch (err) {
              alert('Upload succeeded but response is not valid JSON.');
              if (uploadBtn) uploadBtn.disabled = false;
              return;
            }
            if (progressBar) {
              progressBar.style.width = '100%';
              progressBar.textContent = '100%';
            }
            if (analyzeBtn) analyzeBtn.style.display = 'inline-block';
          } else {
            alert('Upload failed: ' + xhr.responseText);
            if (uploadBtn) uploadBtn.disabled = false;
          }
        };

        xhr.onerror = function () {
          alert('Network error during upload');
          if (uploadBtn) uploadBtn.disabled = false;
        };

        xhr.send(formData);
      });
    }

    // ---- Start Analysis = Extract -> Parse ----
    if (analyzeBtn) {
      analyzeBtn.addEventListener('click', async function () {
        if (!lastUploadResp) {
          alert('Please upload a ZIP first.');
          return;
        }
        const id = lastUploadResp.id;

        // เตรียม UI
        analyzeBtn.disabled = true;
        if (analysisStatus) analysisStatus.style.display = 'block';
        if (csvLinks) csvLinks.style.display = 'none';
        if (mftLinkRow) mftLinkRow.style.display = 'none';
        if (amcacheLinkRow) amcacheLinkRow.style.display = 'none';
        if (statusText) statusText.textContent = 'Extracting KAPE bundle...';
        if (analysisProgress) analysisProgress.style.width = '15%';

        // 1) start-extract
        const fd1 = new FormData();
        fd1.append('id', id);
        try {
          const r1 = await fetch('/api/start-extract/', {
            method: 'POST',
            body: fd1,
            headers: csrftoken ? {'X-CSRFToken': csrftoken} : {}
          });
          const d1 = await r1.json();
          if (!r1.ok || !d1.ok) {
            alert('Extraction failed: ' + (d1.error || r1.statusText));
            if (statusText) statusText.textContent = 'Extraction failed.';
            analyzeBtn.disabled = false;
            return;
          }
        } catch (e) {
          alert('Network error during extraction: ' + e);
          if (statusText) statusText.textContent = 'Extraction failed.';
          analyzeBtn.disabled = false;
          return;
        }

        // 2) start-parse
        if (statusText) statusText.textContent = 'Parsing to CSV (MFT, Amcache)...';
        if (analysisProgress) analysisProgress.style.width = '60%';

        const fd2 = new FormData();
        fd2.append('id', id);
        try {
          const r2 = await fetch('/api/start-parse/', {
            method: 'POST',
            body: fd2,
            headers: csrftoken ? {'X-CSRFToken': csrftoken} : {}
          });
          const d2 = await r2.json();

          const statusStr = String((d2 && d2.status) || '').toUpperCase();
          const ok = r2.ok && (d2.ok === true || statusStr === 'PARSED' || statusStr === 'DONE');

          if (!ok) {
            alert('Parse failed: ' + (d2.log_tail || d2.error || r2.statusText));
            if (statusText) statusText.textContent = 'Parse failed.';
            if (analysisProgress) analysisProgress.style.width = '100%';
            analyzeBtn.disabled = false;
            return;
          }

          // สำเร็จ
          if (statusText) statusText.textContent = 'Done.';
          if (analysisProgress) analysisProgress.style.width = '100%';
          if (csvLinks) csvLinks.style.display = 'block';

          if (d2.mft_csv) {
            if (mftLink) mftLink.href = d2.mft_csv;
            if (mftLinkRow) mftLinkRow.style.display = 'list-item';
          }
          if (d2.amcache_csv) {
            if (amcacheLink) amcacheLink.href = d2.amcache_csv;
            if (amcacheLinkRow) amcacheLinkRow.style.display = 'list-item';
          }
        } catch (e) {
          alert('Network error during parse: ' + e);
          if (statusText) statusText.textContent = 'Parse failed.';
        } finally {
          analyzeBtn.disabled = false;
        }
      });
    }
  });
})();