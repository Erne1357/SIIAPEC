/* app/static/js/admin/settings/permissions.js
 * Gestión de permisos por rol (catálogo, seed, overrides, auditoría, toast).
 */
(function () {
  'use strict';

  function getCsrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  }

  let currentRoleId = null;
  let catalogAll = [];

  // ── Inicialización ──────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    loadCatalog();

    // Delegación: botones de rol, botones de acción
    document.getElementById('roleList')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.list-group-item[data-role-id]');
      if (btn) loadRole(parseInt(btn.dataset.roleId), btn.dataset.roleName);
    });

    document.getElementById('btnRefreshAudit')?.addEventListener('click', loadAudit);
    document.getElementById('filterResource')?.addEventListener('change', filterCatalog);
    document.getElementById('btnSubmitOverride')?.addEventListener('click', submitOverride);
    document.getElementById('seedFilter')?.addEventListener('input', (e) =>
      filterTable(e.target.value, 'seedTable')
    );

    document.getElementById('overridesBody')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.js-revert-override');
      if (btn) revertOverride(btn.dataset.codename);
    });

    // Cargar primer rol por defecto
    const firstBtn = document.querySelector('#roleList .list-group-item');
    if (firstBtn) loadRole(parseInt(firstBtn.dataset.roleId), firstBtn.dataset.roleName);

    // Tiempo real: otro postgrad_admin agregó/revirtió un override
    window.addEventListener('siiap:role_permission:changed', (e) => {
      const d = e.detail || {};
      const verb = d.action === 'grant' ? 'agregado' : 'revertido';
      showToast(`Permiso ${verb} en el rol «${d.role_name}»: ${d.codename}. Actualizando…`, 'info');
      // Si el rol afectado es el actualmente cargado, refrescar su panel
      if (currentRoleId && d.role_id === currentRoleId) {
        loadRole(currentRoleId, document.getElementById('currentRoleName').textContent);
      } else {
        loadAudit();
      }
    });
  });

  // ── Catálogo ────────────────────────────────────────────────────────────
  async function loadCatalog() {
    const res = await fetch('/api/v1/permissions/catalog');
    const { data } = await res.json();
    catalogAll = data || [];
    populateCatalogSelect(catalogAll);
  }

  function populateCatalogSelect(perms) {
    const sel = document.getElementById('selectCodename');
    if (!sel) return;
    sel.innerHTML = perms.length
      ? perms.map(p => `<option value="${SIIAP.escapeAttr(p.codename)}">${
          SIIAP.escapeHtml(p.codename)} — ${SIIAP.escapeHtml(p.display_name)}</option>`).join('')
      : '<option disabled>Sin resultados</option>';
  }

  function filterCatalog() {
    const resource = document.getElementById('filterResource').value;
    const filtered = resource ? catalogAll.filter(p => p.resource === resource) : catalogAll;
    populateCatalogSelect(filtered);
  }

  // ── Cargar permisos de un rol ───────────────────────────────────────────
  async function loadRole(roleId, roleName) {
    currentRoleId = roleId;
    document.getElementById('currentRoleName').textContent = roleName;
    document.getElementById('btnAddOverride').disabled = false;

    document.querySelectorAll('#roleList .list-group-item').forEach(b => {
      b.classList.toggle('active', parseInt(b.dataset.roleId) === roleId);
    });

    const res = await fetch(`/api/v1/permissions/roles/${roleId}`);
    const { data } = await res.json();

    renderSeed(data.seed_permissions || []);
    renderOverrides(data.overrides || []);
    loadAudit();
  }

  function renderSeed(perms) {
    document.getElementById('seedCount').textContent = perms.length;
    const tbody = document.getElementById('seedBody');
    if (!perms.length) {
      tbody.innerHTML = `<tr><td colspan="3">
        <div class="empty-state empty-state--inline">
          <div class="empty-state__icon"><i class="bi bi-shield-slash" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin permisos base</p>
          <p class="empty-state__description">Este rol no tiene permisos del catálogo del sistema.</p>
        </div>
      </td></tr>`;
      return;
    }
    tbody.innerHTML = perms.map(p => `
      <tr>
        <th scope="row" class="fw-normal"><code class="small">${SIIAP.escapeHtml(p.codename)}</code></th>
        <td class="text-muted small">${SIIAP.escapeHtml(p.display_name)}</td>
        <td><span class="badge bg-primary-soft">${SIIAP.escapeHtml(p.perm_type)}</span></td>
      </tr>
    `).join('');
  }

  function renderOverrides(overrides) {
    const active = overrides.filter(o => o.is_active);
    document.getElementById('overrideCount').textContent = active.length;
    const tbody = document.getElementById('overridesBody');
    if (!overrides.length) {
      tbody.innerHTML = `<tr><td colspan="5">
        <div class="empty-state empty-state--inline">
          <div class="empty-state__icon"><i class="bi bi-plus-circle-dotted" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin permisos adicionales</p>
          <p class="empty-state__description">Este rol solo tiene los permisos base del sistema.</p>
        </div>
      </td></tr>`;
      return;
    }
    tbody.innerHTML = overrides.map(o => {
      const codeText = SIIAP.escapeHtml(o.permission_codename);
      const codeAttr = SIIAP.escapeAttr(o.permission_codename);
      return `
      <tr class="${o.is_active ? '' : 'table-secondary text-muted'}">
        <th scope="row" class="fw-normal"><code class="small">${codeText}</code>
          ${o.is_seed_duplicate ? '<span class="badge bg-info-soft ms-1">Ya está en el catálogo base</span>' : ''}
        </th>
        <td>
          ${o.is_active
            ? '<span class="status-badge status-badge--accepted status-badge--sm"><i class="bi bi-check-circle-fill" aria-hidden="true"></i><span>Activo</span></span>'
            : '<span class="status-badge status-badge--deferred status-badge--sm"><i class="bi bi-slash-circle" aria-hidden="true"></i><span>Revertido</span></span>'}
        </td>
        <td class="small">${fmtDate(o.created_at)}</td>
        <td class="small">${fmtDate(o.revoked_at)}</td>
        <td class="text-end">
          ${o.is_active
            ? `<button type="button" class="btn btn-sm btn-outline-danger js-revert-override tap-target"
                       data-codename="${codeAttr}"
                       aria-label="Revertir el permiso ${codeAttr}"
                       title="Revertir el permiso ${codeAttr}">
                 <i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i>
               </button>`
            : ''}
        </td>
      </tr>
    `;
    }).join('');
  }

  // ── Overrides (agregar / revertir) ──────────────────────────────────────
  async function submitOverride() {
    const codename = document.getElementById('selectCodename').value;
    const reason   = document.getElementById('overrideReason').value.trim();
    if (!codename) { showToast('Selecciona un permiso.', 'warning'); return; }

    const res = await fetch(`/api/v1/permissions/roles/${currentRoleId}/override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify({ codename, reason: reason || null }),
    });
    const body = await res.json();
    if (res.ok) {
      bootstrap.Modal.getInstance(document.getElementById('addOverrideModal')).hide();
      showToast(body.flash?.[0]?.[0] ?? 'Permiso agregado al rol.', 'success');
      loadRole(currentRoleId, document.getElementById('currentRoleName').textContent);
    } else {
      showToast(body.error ?? 'No se pudo agregar el permiso.', 'danger');
    }
  }

  async function revertOverride(codename) {
    const ok = await siiapConfirm({
      type: 'warning',
      title: 'Revertir permiso adicional',
      message: `¿Quitar el permiso «${codename}» de este rol? Volverá a tener solo sus permisos base.`,
      confirmLabel: 'Sí, revertir',
    });
    if (!ok) return;
    // Contexto URL, no HTML: el codename viaja en la ruta y debe ir codificado.
    const path = `/api/v1/permissions/roles/${currentRoleId}/override/${encodeURIComponent(codename)}`;
    const res = await fetch(path, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': getCsrf() },
    });
    const body = await res.json();
    if (res.ok) {
      showToast(body.flash?.[0]?.[0] ?? 'Permiso revertido.', 'success');
      loadRole(currentRoleId, document.getElementById('currentRoleName').textContent);
    } else {
      showToast(body.error ?? 'Error al revertir.', 'danger');
    }
  }

  // ── Auditoría ───────────────────────────────────────────────────────────
  async function loadAudit() {
    const params = currentRoleId ? `?role_id=${currentRoleId}&per_page=20` : '?per_page=20';
    const res = await fetch(`/api/v1/permissions/audit${params}`);
    const { data } = await res.json();
    const container = document.getElementById('auditLog');
    if (!data || !data.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin movimientos registrados</p>
        </div>`;
      return;
    }
    container.innerHTML = data.map(e => `
      <div class="border-bottom py-1 px-1 small">
        <span class="badge ${e.action === 'grant' ? 'bg-success-soft' : 'bg-warning-soft'}">${
          e.action === 'grant' ? 'Agregado' : 'Revertido'}</span>
        <code class="ms-1">${SIIAP.escapeHtml(e.permission_codename)}</code>
        <div class="text-muted audit-meta">${SIIAP.escapeHtml(e.performed_by_name)} · ${fmtDateTime(e.performed_at)}</div>
        ${e.reason ? `<div class="fst-italic audit-meta">«${SIIAP.escapeHtml(e.reason)}»</div>` : ''}
      </div>
    `).join('');
  }

  // ── Fechas en español ───────────────────────────────────────────────────
  function fmtDate(iso) {
    if (!iso) return '—';
    if (window.SIIAP && window.SIIAP.formatDate) return window.SIIAP.formatDate(iso, 'short');
    return new Date(iso).toLocaleDateString('es-MX');
  }

  function fmtDateTime(iso) {
    if (!iso) return '—';
    if (window.SIIAP && window.SIIAP.formatDateTime) return window.SIIAP.formatDateTime(iso, 'short');
    return new Date(iso).toLocaleString('es-MX');
  }

  // ── Filtrar tabla ───────────────────────────────────────────────────────
  function filterTable(query, tableId) {
    const q = query.toLowerCase();
    document.querySelectorAll(`#${tableId} tbody tr`).forEach(tr => {
      tr.classList.toggle('d-none', !tr.textContent.toLowerCase().includes(q));
    });
  }

  // ── Toast ───────────────────────────────────────────────────────────────
  // Fondos suaves de _tokens.css: .text-bg-success/.text-bg-warning ponen texto
  // blanco sobre un tono demasiado claro (3.49:1 y 3.26:1) y no cumplen AA.
  const TOAST_TONE = {
    success: 'bg-success-soft',
    danger:  'bg-danger-soft',
    warning: 'bg-warning-soft',
    info:    'bg-info-soft',
    primary: 'bg-primary-soft',
  };

  function showToast(msg, type = 'info') {
    const el = document.getElementById('permToast');
    el.className = `toast align-items-center ${TOAST_TONE[type] || TOAST_TONE.info}`;
    document.getElementById('permToastBody').textContent = msg;
    if (window.SIIAP && typeof window.SIIAP.announce === 'function') {
      window.SIIAP.announce(msg);
    }
    bootstrap.Toast.getOrCreateInstance(el).show();
  }
})();
