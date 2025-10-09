// static/js/result.js
(function () {
  // ===== Helpers =====
  function getEvidenceId() {
    const el = document.getElementById('evidenceId');
    return el ? el.textContent.trim() : null;
  }
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
  function toInt(x, defv) {
    const n = parseInt(x, 10);
    return Number.isFinite(n) ? n : defv;
  }
  function sumBadges(ids) {
    return ids
      .map(id => document.getElementById(id)?.textContent || "0")
      .map(t => toInt(String(t).replace(/,/g, ''), 0))
      .reduce((a,b)=>a+b,0);
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

  // ===== Renderers =====
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

  function renderSecurityRows(rows) {
    const tbody = document.getElementById('securityTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${r.Timestamp ?? ''}</td>
        <td>${r.EventID ?? ''}</td>
        <td>${r.Message ?? ''}</td>
        <td>${r.User ?? ''}</td>
        <td>${r.SourceIP ?? ''}</td>
        <td>${r.Computer ?? ''}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  // ===== State =====
  const state = {
    evId: null,
    mft: { page: 1, page_size: 50, q: '', type: '', size_bucket: '', sort: 'EntryNumber', order: 'asc' },
    amc: { page: 1, page_size: 50, q: '', publisher: '', sort: 'AppName', order: 'asc' },
    sec: { page: 1, page_size: 50, q: '', event_id: '', logon_type: '', sort: 'Timestamp', order: 'desc' },
  };

  // ===== Loaders =====
  async function loadMft() {
    const p = new URLSearchParams(state.mft).toString();
    const r = await fetch(`/api/evidence/${state.evId}/mft/?${p}`);
    if (!r.ok) return;
    const d = await r.json();

    renderMftRows(d.rows);
    setBadgeCount('mftCount', d.total);
    setText('mftRecords', numberWithCommas(d.total));

    buildPager('#mftPagination', state.mft.page, state.mft.page_size, d.total, (to) => {
      state.mft.page = to;
      loadMft();
    });

    // อัปเดตรวม
    const total = sumBadges(['mftRecords','amcacheRecords','eventLogRecords']);
    setText('totalRecords', numberWithCommas(total));
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

    // เติม publisher dropdown ครั้งแรก
    const sel = document.getElementById('publisherFilter');
    if (sel && sel.options.length <= 1 && Array.isArray(d.publishers)) {
      for (const pub of d.publishers) {
        const opt = document.createElement('option');
        opt.value = pub;
        opt.textContent = pub || '(blank)';
        sel.appendChild(opt);
      }
    }

    const total = sumBadges(['mftRecords','amcacheRecords','eventLogRecords']);
    setText('totalRecords', numberWithCommas(total));
  }

  async function loadSecurity() {
    const p = new URLSearchParams(state.sec).toString();
    const r = await fetch(`/api/evidence/${state.evId}/security/?${p}`);
    if (!r.ok) return;
    const d = await r.json();

    renderSecurityRows(d.rows);
    setBadgeCount('securityCount', d.total);
    setText('eventLogRecords', numberWithCommas(d.total));

    buildPager('#securityPagination', state.sec.page, state.sec.page_size, d.total, (to) => {
      state.sec.page = to;
      loadSecurity();
    });

    const total = sumBadges(['mftRecords','amcacheRecords','eventLogRecords']);
    setText('totalRecords', numberWithCommas(total));
  }

  // ===== Bindings =====
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

  function bindAmcacheFilters() {
    const amcacheSearch = document.getElementById('amcacheSearch');
    const publisherFilter = document.getElementById('publisherFilter');

    if (amcacheSearch) amcacheSearch.addEventListener('input', () => { state.amc.q = amcacheSearch.value; state.amc.page = 1; loadAmcache(); });
    if (publisherFilter) publisherFilter.addEventListener('change', () => { state.amc.publisher = publisherFilter.value; state.amc.page = 1; loadAmcache(); });
  }

  function bindSecurityFilters() {
    const q = document.getElementById('securitySearch');
    const eventIdFilter = document.getElementById('eventIdFilter');
    const logonTypeFilter = document.getElementById('logonTypeFilter');

    if (q) q.addEventListener('input', () => { state.sec.q = q.value; state.sec.page = 1; loadSecurity(); });
    if (eventIdFilter) eventIdFilter.addEventListener('change', () => {
      state.sec.event_id = eventIdFilter.value || '';
      state.sec.page = 1; loadSecurity();
    });
    if (logonTypeFilter) logonTypeFilter.addEventListener('change', () => {
      state.sec.logon_type = logonTypeFilter.value || '';
      state.sec.page = 1; loadSecurity();
    });
  }

  // ===== Exposed actions (onclick) =====
  window.clearFilters = function (section) {
    if (section === 'mft') {
      state.mft = { page: 1, page_size: 50, q: '', type: '', size_bucket: '', sort: 'EntryNumber', order: 'asc' };
      const mftSearch = document.getElementById('mftSearch');
      const mftTypeFilter = document.getElementById('mftTypeFilter');
      const mftSizeFilter = document.getElementById('mftSizeFilter');
      if (mftSearch) mftSearch.value = '';
      if (mftTypeFilter) mftTypeFilter.value = '';
      if (mftSizeFilter) mftSizeFilter.value = '';
      loadMft();
      return;
    }
    if (section === 'amcache') {
      state.amc = { page: 1, page_size: 50, q: '', publisher: '', sort: 'AppName', order: 'asc' };
      const amcacheSearch = document.getElementById('amcacheSearch');
      const publisherFilter = document.getElementById('publisherFilter');
      if (amcacheSearch) amcacheSearch.value = '';
      if (publisherFilter) publisherFilter.value = '';
      loadAmcache();
      return;
    }
    if (section === 'security') {
      state.sec = { page: 1, page_size: 50, q: '', event_id: '', logon_type: '', sort: 'Timestamp', order: 'desc' };
      const q = document.getElementById('securitySearch');
      const e = document.getElementById('eventIdFilter');
      const l = document.getElementById('logonTypeFilter');
      if (q) q.value = '';
      if (e) e.value = '';
      if (l) l.value = '';
      loadSecurity();
      return;
    }
  };

  // sortTable('mftTable' | 'amcacheTable' | 'securityTable', idx)
  window.sortTable = function (tableId, colIdx) {
    if (tableId === 'mftTable') {
      const map = { 0: 'EntryNumber', 1: 'FileName', 2: 'FullPath', 3: 'Size', 4: 'Created', 5: 'Modified' };
      const key = map[colIdx] || 'EntryNumber';
      if (state.mft.sort === key) state.mft.order = state.mft.order === 'asc' ? 'desc' : 'asc';
      else { state.mft.sort = key; state.mft.order = 'asc'; }
      state.mft.page = 1; loadMft(); return;
    }
    if (tableId === 'amcacheTable') {
      const map = { 0: 'AppName', 1: 'Version', 2: 'Publisher', 3: 'InstallDate', 4: 'FilePath' };
      const key = map[colIdx] || 'AppName';
      if (state.amc.sort === key) state.amc.order = state.amc.order === 'asc' ? 'desc' : 'asc';
      else { state.amc.sort = key; state.amc.order = 'asc'; }
      state.amc.page = 1; loadAmcache(); return;
    }
    if (tableId === 'securityTable') {
      // หมายเหตุ: Backend รองรับ sort = Timestamp, EventID, User, Computer (Message ไม่ได้แมปใน backend)
      const map = { 0: 'Timestamp', 1: 'EventID', 2: 'Message', 3: 'User', 4: 'Computer' };
      const key = map[colIdx] || 'Timestamp';
      if (state.sec.sort === key) state.sec.order = state.sec.order === 'asc' ? 'desc' : 'asc';
      else { state.sec.sort = key; state.sec.order = 'asc'; }
      state.sec.page = 1; loadSecurity(); return;
    }
  };

  // Export CSV เฉพาะ tab ปัจจุบัน
  window.exportResults = function () {
    const active = document.querySelector('.tab-pane.active.show');
    let table, fname = '';
    if (active && active.id === 'mft') { table = document.getElementById('mftTable'); fname = 'mft_view.csv'; }
    else if (active && active.id === 'amcache') { table = document.getElementById('amcacheTable'); fname = 'amcache_view.csv'; }
    else if (active && active.id === 'security') { table = document.getElementById('securityTable'); fname = 'security_view.csv'; }
    else return;

    const rows = [];
    const ths = table.querySelectorAll('thead th');
    const headers = Array.from(ths).map(th => th.textContent.replace(/\s+$/,'').replace(/\s*\u{f0dc}\s*$/u,'').trim());
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
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ===== Init =====
  document.addEventListener('DOMContentLoaded', async function () {
    state.evId = getEvidenceId();
    if (!state.evId) return;

    bindMftFilters();
    bindAmcacheFilters();
    bindSecurityFilters();

    await Promise.all([loadMft(), loadAmcache(), loadSecurity()]);

    // เติมสรุปจาก /api/evidence/<id>/ ถ้ามี
    try {
      const r0 = await fetch(`/api/evidence/${state.evId}/`);
      if (r0.ok) {
        const d0 = await r0.json();
        if (d0.summary) {
          if (typeof d0.summary.mft_rows === 'number') setText('mftRecords', d0.summary.mft_rows.toLocaleString());
          if (typeof d0.summary.amcache_rows === 'number') setText('amcacheRecords', d0.summary.amcache_rows.toLocaleString());
          const secCount = d0.summary.security_events_rows_db || d0.summary.security_events_rows || d0.summary.security_rows || 0;
          if (secCount) setText('eventLogRecords', secCount.toLocaleString());
          const total = sumBadges(['mftRecords','amcacheRecords','eventLogRecords']);
          if (total) setText('totalRecords', numberWithCommas(total));
        }
      }
    } catch (e) {
      // เงียบไว้ก็ได้
    }
  });
})();
