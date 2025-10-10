function fmtInt(n) {
  try {
    const x = parseInt(n, 10);
    if (isNaN(x)) return "-";
    return x.toLocaleString();
  } catch {
    return String(n ?? "-");
  }
}

function badgeHTML(text, kind) {
  const map = {
    success: "bg-success",
    warning: "bg-warning text-dark",
    secondary: "bg-secondary",
    info: "bg-info",
    danger: "bg-danger"
  };
  const cls = map[kind] || "bg-secondary";
  return `<span class="badge ${cls}">${text}</span>`;
}

async function loadDashboard() {
  try {
    // 1) โหลด totals + recent cases
    const r = await fetch("/api/dashboard/overview");
    const d = await r.json();

    // --- Totals ---
    document.getElementById("totalCases").textContent      = fmtInt(d?.totals?.cases ?? 0);
    document.getElementById("totalEvidence").textContent   = fmtInt(d?.totals?.evidence ?? 0);
    document.getElementById("activeCases").textContent     = fmtInt(d?.totals?.active_cases ?? 0);
    document.getElementById("completedCases").textContent  = fmtInt(d?.totals?.completed_cases ?? 0);

    // --- Recent Cases Table ---
    const tbody = document.getElementById("recentCasesTable");
    tbody.innerHTML = "";

    const rows = d?.recent_cases || [];
    if (rows.length === 0) {
      tbody.innerHTML = `
        <tr><td colspan="7" class="text-center text-muted">No cases yet.</td></tr>
      `;
    } else {
      for (const c of rows) {
        const urlToResults = `/results?case_id=${encodeURIComponent(c.id)}`;  // ปรับตาม route ของคุณ (มี results_index แล้ว)
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="text-nowrap">${c.case_number}</td>
          <td>${escapeHtml(c.title || "")}</td>
          <td>${fmtInt(c.evidence_count)}</td>
          <td>${escapeHtml(c.investigator || "")}</td>
          <td>${badgeHTML(c.status, c.status_badge)}</td>
          <td class="text-nowrap">${c.created_at || ""}</td>
          <td>
            <div class="btn-group btn-group-sm">
              <!-- <a class="btn btn-outline-primary" href="/upload?case_id=${encodeURIComponent(c.id)}">
                <i class="fas fa-upload"></i>
              </a> -->
              <a class="btn btn-outline-secondary" href="${urlToResults}">
                <i class="fas fa-chart-bar"></i>
              </a>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      }
    }

    // 2) ตรวจ System Status
    await checkSystemStatus();
  } catch (e) {
    console.error("loadDashboard error:", e);
    const tbody = document.getElementById("recentCasesTable");
    if (tbody) {
      tbody.innerHTML = `
        <tr><td colspan="7" class="text-danger">Failed to load dashboard data</td></tr>
      `;
    }
    const box = document.getElementById("systemStatus");
    if (box) {
      box.innerHTML = `<div class="text-danger">Failed to check system status.</div>`;
    }
  }
}

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, (ch) => {
    switch (ch) {
      case "&": return "&amp;";
      case "<": return "&lt;";
      case ">": return "&gt;";
      case '"': return "&quot;";
      case "'": return "&#039;";
      default: return ch;
    }
  });
}

async function checkSystemStatus() {
  const box = document.getElementById("systemStatus");
  if (!box) return;

  try {
    const r = await fetch("/api/api/preflight/"); // ระวัง path: จาก main urls -> 'api/' + 'api/preflight/'
    const d = await r.json();

    const ok = !!d?.ok;
    const checks = d?.checks || {};

    const items = [
      statusItem("Docker CLI", checks.docker_cli),
      statusItem("Parser image", checks.parser_image_ok),
      statusItem("MEDIA writable", checks.media_root_writable),
      statusItem("/parsed writable", checks.parsed_writable),
      statusItem("/extracted writable", checks.extracted_writable),
      diskItem(checks)
    ];

    box.innerHTML = `
      <ul class="list-unstyled mb-0">
        ${items.join("")}
      </ul>
      ${!ok ? `<div class="mt-2 text-danger"><i class="fas fa-triangle-exclamation"></i> Not ready</div>` : ""}
    `;
  } catch (e) {
    console.error("checkSystemStatus error:", e);
    box.innerHTML = `<div class="text-danger">Unable to query preflight.</div>`;
  }
}

function statusItem(label, pass) {
  const icon = pass ? `<i class="fas fa-check-circle text-success"></i>` :
                      `<i class="fas fa-times-circle text-danger"></i>`;
  return `<li class="d-flex align-items-center mb-1">${icon}<span class="ms-2">${label}</span></li>`;
}

function diskItem(checks) {
  const total = Number(checks.disk_total_bytes || 0);
  const free  = Number(checks.disk_free_bytes  || 0);
  if (!total) return `<li class="mb-1"><i class="fas fa-hdd"></i><span class="ms-2">Disk: unknown</span></li>`;

  const pctFree = Math.round((free / total) * 100);
  const warn = pctFree < 10; // เหลือน้อยกว่า 10%
  const icon = warn ? `<i class="fas fa-exclamation-triangle text-warning"></i>` :
                      `<i class="fas fa-hdd text-secondary"></i>`;
  return `<li class="mb-1">${icon}<span class="ms-2">Disk free: ${pctFree}%</span></li>`;
}

// ปุ่ม Refresh บนหน้า
async function refreshDashboard() {
  const btns = document.querySelectorAll('button[onclick="refreshDashboard()"]');
  btns.forEach(b => b.disabled = true);
  await loadDashboard();
  btns.forEach(b => b.disabled = false);
}

// โหลดอัตโนมัติเมื่อเปิดหน้า
document.addEventListener("DOMContentLoaded", loadDashboard);
