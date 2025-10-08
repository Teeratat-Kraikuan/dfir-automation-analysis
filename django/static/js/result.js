// static/js/result.js
(function () {
  // Helper: อ่าน evidence_id จาก DOM
  function getEvidenceId() {
    const el = document.getElementById('evidenceId');
    return el ? el.textContent.trim() : null;
  }

  // Helper: set text/badge
  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  function setBadgeCount(id, n) {
    const el = document.getElementById(id);
    if (el) el.textContent = `${(n || 0).toLocaleString()} records`;
  }

  function numberWithCommas(x) {
    try { return (+String(x).replace(/,/g,'')).toLocaleString(); }
    catch { return x || "0"; }
  }

  // สร้างปุ่ม Previous / เลขหน้า / Next ลงใน <nav id="...">
  function buildPager(containerSel, page, pageSize, total, onChange) {
    const el = document.querySelector(containerSel);
    if (!el) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const prevDis = page <= 1 ? ' disabled' : '';
    const nextDis = page >= pages ? ' disabled' : '';
    let html = `<ul class="pagination justify-content-end">`;
    html += `<li class="page-item${prevDis}"><a class="page-link" href="#" data-pg="${page-1}">Previous</a></li>`;
    const start = Math.max(1, page - 2);
    const end = Math.min(pages, page + 2);
    for (let p = start; p <= end; p++) {
      html += `<li class="page-item${p===page?' active':''}"><a class="page-link" href="#" data-pg="${p}">${p}</a></li>`;
    }
    html += `<li class="page-item${nextDis}"><a class="page-link" href="#" data-pg="${page+1}">Next</a></li>`;
    html += `</ul>`;
    el.innerHTML = html;
    el.querySelectorAll('a.page-link').forEach(a => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const to = +a.getAttribute('data-pg');
        if (to && to >= 1 && to <= pages && to !== page) onChange(to);
      });
    });
  }


  // Renderers
  function renderMftRows(rows) {
    const tbody = document.getElementById('mftTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${r.EntryNumber ?? ''}</td>
        <td>${r.FileName ?? ''}</td>
        <td>${r.FullPath ?? ''}</td>
        <td>${r.Size ?? ''}</td>
        <td>${r.Created ?? ''}</td>
        <td>${r.Modified ?? ''}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  function renderAmcacheRows(rows) {
    const tbody = document.getElementById('amcacheTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${r.AppName ?? ''}</td>
        <td>${r.Version ?? ''}</td>
        <td>${r.Publisher ?? ''}</td>
        <td>${r.InstallDate ?? ''}</td>
        <td>${r.FilePath ?? ''}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  // States (ถูกอ้างใน window ฟังก์ชันด้วย)
  const state = {
    evId: null,
    mft: { page: 1, page_size: 50, q: '', type: '', size_bucket: '', sort: 'EntryNumber', order: 'asc' },
    amc: { page: 1, page_size: 50, q: '', publisher: '', sort: 'AppName', order: 'asc' },
  };

  // Loaders
  async function loadMft() {
    const p = new URLSearchParams(state.mft).toString();
    const r = await fetch(`/api/evidence/${state.evId}/mft/?${p}`);
    if (!r.ok) return;
    const d = await r.json();

    renderMftRows(d.rows);
    setBadgeCount('mftCount', d.total);
    setText('mftRecords', numberWithCommas(d.total));

    // วาด pagination และผูกเปลี่ยนหน้า
    buildPager('#mftPagination', state.mft.page, state.mft.page_size, d.total, (to) => {
      state.mft.page = to;
      loadMft();
    });

    // อัปเดตรวมอย่างคร่าว ๆ (ถ้าอยากชัวร์ใช้ค่าจาก /api/evidence/<id>/ เหมือนเดิมก็ได้)
    const amcacheBadge = document.getElementById('amcacheCount');
    const amcacheNum = amcacheBadge ? parseInt(amcacheBadge.textContent) || 0 : 0;
    setText('totalRecords', numberWithCommas(d.total + amcacheNum));
  }


  async function loadAmcache() {
    const p = new URLSearchParams(state.amc).toString();
    const r = await fetch(`/api/evidence/${state.evId}/amcache/?${p}`);
    if (!r.ok) return;
    const d = await r.json();

    renderAmcacheRows(d.rows);
    setBadgeCount('amcacheCount', d.total);
    setText('amcacheRecords', numberWithCommas(d.total));

    buildPager('#amcachePagination', state.amc.page, state.amc.page_size, d.total, (to) => {
      state.amc.page = to;
      loadAmcache();
    });

    // เติม dropdown publisher (ครั้งแรกหรือจะรีเฟรชทุกครั้งก็ได้)
    const sel = document.getElementById('publisherFilter');
    if (sel && sel.options.length <= 1 && Array.isArray(d.publishers)) {
      for (const pub of d.publishers) {
        const opt = document.createElement('option');
        opt.value = pub;
        opt.textContent = pub || '(blank)';
        sel.appendChild(opt);
      }
    }

    const mftBadge = document.getElementById('mftCount');
    const mftNum = mftBadge ? parseInt(mftBadge.textContent) || 0 : 0;
    setText('totalRecords', numberWithCommas(d.total + mftNum));
  }


  // Binding filters (MFT)
  function bindMftFilters() {
    const mftSearch = document.getElementById('mftSearch');
    const mftTypeFilter = document.getElementById('mftTypeFilter');
    const mftSizeFilter = document.getElementById('mftSizeFilter');

    if (mftSearch) mftSearch.addEventListener('input', () => { state.mft.q = mftSearch.value; state.mft.page = 1; loadMft(); });

    if (mftTypeFilter) mftTypeFilter.addEventListener('change', () => {
      const v = (mftTypeFilter.value || '').toLowerCase();
      state.mft.type = (v === 'directory') ? 'dir' : (v === 'file') ? 'file' : '';
      state.mft.page = 1; loadMft();
    });

    if (mftSizeFilter) mftSizeFilter.addEventListener('change', () => {
      state.mft.size_bucket = mftSizeFilter.value || '';
      state.mft.page = 1; loadMft();
    });
  }

  // Binding filters (Amcache)
  function bindAmcacheFilters() {
    const amcacheSearch = document.getElementById('amcacheSearch');
    const publisherFilter = document.getElementById('publisherFilter');

    if (amcacheSearch) amcacheSearch.addEventListener('input', () => { state.amc.q = amcacheSearch.value; state.amc.page = 1; loadAmcache(); });
    if (publisherFilter) publisherFilter.addEventListener('change', () => { state.amc.publisher = publisherFilter.value; state.amc.page = 1; loadAmcache(); });
  }

  // Expose functions used by HTML onclicks
  window.clearFilters = function (section) {
    if (section === 'mft') {
      state.mft = { page: 1, page_size: 50, q: '', type: '', size_bucket: '', sort: 'EntryNumber', order: 'asc' };
      // reset UI controls
      const mftSearch = document.getElementById('mftSearch');
      const mftTypeFilter = document.getElementById('mftTypeFilter');
      const mftSizeFilter = document.getElementById('mftSizeFilter');
      if (mftSearch) mftSearch.value = '';
      if (mftTypeFilter) mftTypeFilter.value = '';
      if (mftSizeFilter) mftSizeFilter.value = '';
      loadMft();
    } else if (section === 'amcache') {
      state.amc = { page: 1, page_size: 50, q: '', publisher: '', sort: 'AppName', order: 'asc' };
      const amcacheSearch = document.getElementById('amcacheSearch');
      const publisherFilter = document.getElementById('publisherFilter');
      if (amcacheSearch) amcacheSearch.value = '';
      if (publisherFilter) publisherFilter.value = '';
      loadAmcache();
    }
  };

  // sortTable('mftTable', idx) / sortTable('amcacheTable', idx)
  window.sortTable = function (tableId, colIdx) {
    if (tableId === 'mftTable') {
      const map = { 0: 'EntryNumber', 1: 'FileName', 2: 'FullPath', 3: 'Size', 4: 'Created', 5: 'Modified' };
      const key = map[colIdx] || 'EntryNumber';
      if (state.mft.sort === key) {
        state.mft.order = state.mft.order === 'asc' ? 'desc' : 'asc';
      } else {
        state.mft.sort = key; state.mft.order = 'asc';
      }
      state.mft.page = 1;
      loadMft();
    } else if (tableId === 'amcacheTable') {
      const map = { 0: 'AppName', 1: 'Version', 2: 'Publisher', 3: 'InstallDate', 4: 'FilePath' };
      const key = map[colIdx] || 'AppName';
      if (state.amc.sort === key) {
        state.amc.order = state.amc.order === 'asc' ? 'desc' : 'asc';
      } else {
        state.amc.sort = key; state.amc.order = 'asc';
      }
      state.amc.page = 1;
      loadAmcache();
    }
  };

  // Export เป็น CSV ง่าย ๆ จาก rows ล่าสุดที่ render (front-end)
  window.exportResults = function () {
    // เตรียม export เฉพาะ tab ที่โชว์อยู่
    const active = document.querySelector('.tab-pane.active.show');
    let table;
    if (active && active.id === 'mft') table = document.getElementById('mftTable');
    else if (active && active.id === 'amcache') table = document.getElementById('amcacheTable');
    else return;

    const rows = [];
    const ths = table.querySelectorAll('thead th');
    const headers = Array.from(ths).map(th => th.textContent.replace(/\s+$/,'').replace(/\s*\u{f0dc}\s*$/u,'').trim()); // ตัดไอคอน sort
    rows.push(headers.join(','));

    const trs = table.querySelectorAll('tbody tr');
    trs.forEach(tr => {
      const cols = Array.from(tr.children).map(td => {
        const t = (td.textContent || '').replaceAll('"', '""');
        return `"${t}"`;
      });
      rows.push(cols.join(','));
    });

    const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (active && active.id === 'mft') ? 'mft_view.csv' : 'amcache_view.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Init
  document.addEventListener('DOMContentLoaded', async function () {
    state.evId = getEvidenceId();
    if (!state.evId) return;

    bindMftFilters();
    bindAmcacheFilters();

    // ยิงโหลดครั้งแรก
    await Promise.all([loadMft(), loadAmcache()]);

    // ดึง summary มาเติมสถิติ รวมเลขเร็วๆ
    try {
      const r0 = await fetch(`/api/evidence/${state.evId}/`);
      if (r0.ok) {
        const d0 = await r0.json();
        if (d0.summary) {
          if (typeof d0.summary.mft_rows === 'number') setText('mftRecords', d0.summary.mft_rows.toLocaleString());
          if (typeof d0.summary.amcache_rows === 'number') setText('amcacheRecords', d0.summary.amcache_rows.toLocaleString());
          if (typeof d0.summary.mft_rows === 'number' || typeof d0.summary.amcache_rows === 'number') {
            const a = d0.summary.mft_rows || 0, b = d0.summary.amcache_rows || 0;
            setText('totalRecords', (a + b).toLocaleString());
          }
        }
      }
    } catch (e) {
      // เงียบ ๆ ถ้า fail
    }
  });
})();
