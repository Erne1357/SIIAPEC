// app/static/js/admin/settings/data_cleanup.js
//
// Pantalla de limpieza con respaldo ZIP previo.
//
// Flujo por categoría:
//   1. listCandidates(category) → tabla con checkboxes
//   2. start(purge_type, ids)   → POST /start → run_id + archive_url
//   3. download(archive_url)    → cliente baja ZIP, backend marca downloaded
//   4. confirmPurge(run_id)     → POST /confirm → borrado físico
//
// Se apoya en window.PURGE_API definido por la plantilla.

(function () {
  'use strict';

  const CATEGORIES = [
    'admission_expired_with_files',
    'admission_delta3_plus',
    'retention_policy',
  ];

  const selectionByCategory = {};
  let currentRunId = null;

  // ── Utils ──────────────────────────────────────────────────────────────
  function getCsrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  }

  function flash(level, message) {
    if (typeof showFlash === 'function') showFlash(level, message);
    else console.log(`[${level}]`, message);
  }

  function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = String(str == null ? '' : str);
    return div.innerHTML;
  }

  function formatBytes(bytes) {
    if (!bytes) return '—';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let v = bytes;
    while (v >= 1024 && i < u.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(1)} ${u[i]}`;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    if (window.SIIAP && window.SIIAP.formatDateTime) {
      return window.SIIAP.formatDateTime(iso, 'short');
    }
    return new Date(iso).toLocaleString('es-MX', {
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  }

  // ── Etiquetas en español ───────────────────────────────────────────────
  const PURGE_TYPE_LABEL = {
    admission_expired_with_files: 'Aspirantes expirados con archivos',
    admission_delta3_plus: 'Aspirantes de periodos antiguos',
    retention_policy: 'Política de retención',
    transition_snapshot: 'Respaldo de transición de periodo',
  };

  // Estado de un respaldo -> modificador del componente .status-badge.
  const RUN_STATUS_META = {
    pending_download: { key: 'pending',     label: 'Pendiente de descarga' },
    downloaded:       { key: 'in-progress', label: 'Descargado' },
    purged:           { key: 'accepted',    label: 'Purgado' },
    cancelled:        { key: 'deferred',    label: 'Cancelado' },
    expired:          { key: 'rejected',    label: 'Expirado' },
  };

  // Estados de admisión que el componente compartido ya conoce.
  const ADMISSION_KNOWN = new Set([
    'in_progress', 'interview_completed', 'deliberation',
    'accepted', 'rejected', 'deferred', 'enrolled', 'pending',
  ]);
  const ADMISSION_EXTRA_LABEL = { expired: 'Expirado' };

  function statusChip(key, label) {
    if (window.SIIAP && window.SIIAP.statusBadge) {
      return window.SIIAP.statusBadge(key, label);
    }
    return `<span class="status-badge status-badge--${key}"><span>${escHtml(label)}</span></span>`;
  }

  function admissionChip(status) {
    if (!status) return '<span class="text-muted">—</span>';
    if (ADMISSION_KNOWN.has(status)) return statusChip(status);
    return statusChip('deferred', ADMISSION_EXTRA_LABEL[status] || status);
  }

  function runStatusChip(status) {
    const meta = RUN_STATUS_META[status] || { key: 'pending', label: status };
    return statusChip(meta.key, meta.label);
  }

  // ── Render tabla candidatos ────────────────────────────────────────────
  function renderTable(category, items) {
    const container = document.querySelector(
      `[data-table-container="${category}"]`
    );
    if (!container) return;

    document.getElementById(`badge-${badgeKey(category)}`).textContent = items.length;

    if (!items.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-check-circle" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin candidatos</p>
          <p class="empty-state__description">
            Ningún expediente de esta categoría tiene archivos por respaldar ahora mismo.
          </p>
        </div>`;
      updateStartButton(category);
      return;
    }

    selectionByCategory[category] = new Set();

    const rows = items.map(it => {
      const who = escHtml(it.name || it.email || 'este expediente');
      return `
      <tr>
        <td>
          <input type="checkbox" class="form-check-input cleanup-row-check"
                 data-up-id="${it.user_program_id}"
                 aria-label="Seleccionar a ${who}">
        </td>
        <th scope="row" class="fw-normal">${escHtml(it.name || '')}</th>
        <td>${escHtml(it.email || '')}</td>
        <td>${escHtml(it.program_name || '')}</td>
        <td>${admissionChip(it.admission_status)}</td>
        <td>${escHtml(it.admission_period || '—')}</td>
        <td class="files-badge">${it.files_count || 0}</td>
        <td class="files-badge">${formatBytes(it.total_size_bytes)}</td>
      </tr>
    `;
    }).join('');

    container.innerHTML = `
      <div class="siiap-table-wrapper">
        <table class="table siiap-table mb-0 table-sm cleanup-table align-middle">
          <caption class="visually-hidden">
            Expedientes candidatos a respaldo y purga en esta categoría
          </caption>
          <thead>
            <tr>
              <th scope="col" class="w-px-50">
                <input type="checkbox" class="form-check-input cleanup-select-all"
                       aria-label="Seleccionar todos los expedientes de la categoría">
              </th>
              <th scope="col">Nombre</th>
              <th scope="col">Correo</th>
              <th scope="col">Programa</th>
              <th scope="col">Estado</th>
              <th scope="col">Periodo de admisión</th>
              <th scope="col">Archivos</th>
              <th scope="col">Tamaño</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    // La tabla se inyecta después de DOMContentLoaded: hay que activar la
    // pista de scroll del wrapper a mano.
    if (window.SIIAP && window.SIIAP.initDataTable) {
      container.querySelectorAll('.siiap-table-wrapper').forEach(window.SIIAP.initDataTable);
    }

    container.querySelector('.cleanup-select-all')?.addEventListener('change', (e) => {
      const checked = e.target.checked;
      container.querySelectorAll('.cleanup-row-check').forEach(cb => {
        cb.checked = checked;
        const id = parseInt(cb.dataset.upId, 10);
        if (checked) selectionByCategory[category].add(id);
        else selectionByCategory[category].delete(id);
      });
      updateStartButton(category);
    });

    container.querySelectorAll('.cleanup-row-check').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const id = parseInt(e.target.dataset.upId, 10);
        if (e.target.checked) selectionByCategory[category].add(id);
        else selectionByCategory[category].delete(id);
        updateStartButton(category);
      });
    });

    updateStartButton(category);
  }

  function badgeKey(category) {
    if (category === 'admission_expired_with_files') return 'expired-files';
    if (category === 'admission_delta3_plus') return 'delta3';
    if (category === 'retention_policy') return 'retention';
    return category;
  }

  function updateStartButton(category) {
    const btn = document.querySelector(
      `[data-action="start-purge"][data-category="${category}"]`
    );
    if (!btn) return;
    const count = (selectionByCategory[category] || new Set()).size;
    btn.disabled = count === 0;
    const label = count === 0
      ? 'Generar respaldo de los seleccionados'
      : (count === 1
        ? 'Generar respaldo de 1 expediente'
        : `Generar respaldo de ${count} expedientes`);
    btn.innerHTML = `<i class="bi bi-file-earmark-zip me-1" aria-hidden="true"></i>${label}`;
  }

  // ── Cargar candidatos ──────────────────────────────────────────────────
  async function loadCandidates(category) {
    try {
      const res = await fetch(`${window.PURGE_API.candidates}?category=${category}`, {
        headers: { 'Accept': 'application/json' },
      });
      const json = await res.json();
      if (!res.ok) {
        flash('danger', json?.error?.message || 'Error al cargar candidatos');
        return;
      }
      renderTable(category, json.data || []);
    } catch (e) {
      flash('danger', `Error de red: ${e.message}`);
    }
  }

  // ── Generar respaldo ZIP ───────────────────────────────────────────────
  async function startPurge(category, purgeType) {
    const ids = Array.from(selectionByCategory[category] || []);
    if (!ids.length) {
      flash('warning', 'Selecciona al menos un registro.');
      return;
    }

    const btn = document.querySelector(
      `[data-action="start-purge"][data-category="${category}"]`
    );
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Generando ZIP…`;
    }

    try {
      const res = await fetch(window.PURGE_API.start, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf(),
        },
        body: JSON.stringify({
          user_program_ids: ids,
          purge_type: purgeType,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        flash('danger', json?.error?.message || 'Error al generar respaldo');
        return;
      }

      (json.flash || []).forEach(f => flash(f.level, f.message));

      const run = json.data?.run;
      if (run) {
        currentRunId = run.run_id;
        triggerDownload(json.data.archive_url, `purge_${run.run_id}.zip`);
        // Mostrar modal de confirmación tras pequeño delay para asegurar que descarga inició
        setTimeout(() => openConfirmModal(run.run_id), 800);
        await loadCandidates(category);
        await loadRuns();
      }
    } catch (e) {
      flash('danger', `Error de red: ${e.message}`);
    } finally {
      updateStartButton(category);
    }
  }

  function triggerDownload(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || '';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 1000);
  }

  // ── Confirmar purga física ─────────────────────────────────────────────
  function openConfirmModal(run_id) {
    currentRunId = run_id;
    document.getElementById('confirmRunId').textContent = run_id;
    const check = document.getElementById('confirmDownloadedCheck');
    if (check) check.checked = false;
    const btn = document.getElementById('btnDoConfirmPurge');
    if (btn) btn.disabled = true;
    const modal = bootstrap.Modal.getOrCreateInstance(
      document.getElementById('modalConfirmPurge')
    );
    modal.show();
  }

  async function doConfirmPurge() {
    if (!currentRunId) return;
    const btn = document.getElementById('btnDoConfirmPurge');
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Borrando…`;
    try {
      const res = await fetch(window.PURGE_API.confirm(currentRunId), {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrf() },
      });
      const json = await res.json();
      (json.flash || []).forEach(f => flash(f.level, f.message));
      if (res.ok) {
        const modal = bootstrap.Modal.getInstance(
          document.getElementById('modalConfirmPurge')
        );
        modal?.hide();
        CATEGORIES.forEach(c => loadCandidates(c));
        loadRuns();
      } else {
        flash('danger', json?.error?.message || 'Error al confirmar purga');
      }
    } catch (e) {
      flash('danger', `Error de red: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<i class="bi bi-trash me-1" aria-hidden="true"></i>Borrar archivos del servidor`;
    }
  }

  // ── Cancelar run ───────────────────────────────────────────────────────
  async function cancelRun(run_id) {
    const ok = await siiapConfirm({
      type: 'warning',
      title: 'Cancelar respaldo',
      message: `Se borrará el ZIP del respaldo ${String(run_id).slice(0, 8)} del servidor. ` +
        'No se elimina ningún archivo de los expedientes.',
      confirmLabel: 'Sí, cancelar el respaldo',
    });
    if (!ok) return;
    try {
      const res = await fetch(window.PURGE_API.cancel(run_id), {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrf() },
      });
      const json = await res.json();
      (json.flash || []).forEach(f => flash(f.level, f.message));
      loadRuns();
    } catch (e) {
      flash('danger', `Error: ${e.message}`);
    }
  }

  // ── Lista de runs ──────────────────────────────────────────────────────
  async function loadRuns() {
    try {
      const res = await fetch(window.PURGE_API.runs);
      const json = await res.json();
      const container = document.getElementById('runsTableContainer');
      if (!container) return;

      const runs = json.data || [];
      if (!runs.length) {
        container.innerHTML = `
          <div class="empty-state empty-state--compact">
            <div class="empty-state__icon"><i class="bi bi-file-earmark-zip" aria-hidden="true"></i></div>
            <p class="empty-state__title">Sin respaldos generados</p>
            <p class="empty-state__description">
              Aquí aparecerán los ZIP que generes desde las otras pestañas.
            </p>
          </div>`;
        return;
      }

      const rows = runs.map(r => {
        const shortId = escHtml(r.run_id.slice(0, 8));
        const actions = [];
        if (r.status === 'pending_download' || r.status === 'downloaded') {
          actions.push(`<a class="btn btn-sm btn-outline-primary tap-target"
                           href="${window.PURGE_API.archive(r.run_id)}"
                           download="purge_${r.run_id}.zip"
                           aria-label="Descargar el ZIP del respaldo ${shortId}"
                           title="Descargar el ZIP del respaldo ${shortId}">
                          <i class="bi bi-download" aria-hidden="true"></i></a>`);
        }
        if (r.status === 'downloaded' && r.purge_type !== 'transition_snapshot') {
          actions.push(`<button type="button" class="btn btn-sm btn-danger tap-target"
                                data-action="open-confirm" data-run-id="${r.run_id}"
                                aria-label="Borrar del servidor los archivos del respaldo ${shortId}"
                                title="Borrar del servidor los archivos del respaldo ${shortId}">
                          <i class="bi bi-trash" aria-hidden="true"></i></button>`);
        }
        if (r.status === 'pending_download' || r.status === 'downloaded') {
          actions.push(`<button type="button" class="btn btn-sm btn-outline-secondary tap-target"
                                data-action="cancel-run" data-run-id="${r.run_id}"
                                aria-label="Cancelar el respaldo ${shortId}"
                                title="Cancelar el respaldo ${shortId}">
                          <i class="bi bi-x-lg" aria-hidden="true"></i></button>`);
        }
        return `
          <tr>
            <th scope="row" class="fw-normal"><code>${shortId}</code></th>
            <td>${escHtml(PURGE_TYPE_LABEL[r.purge_type] || r.purge_type)}</td>
            <td>${r.item_count}</td>
            <td>${formatBytes(r.archive_size_bytes)}</td>
            <td>${fmtDate(r.initiated_at)}</td>
            <td>${fmtDate(r.expires_at)}</td>
            <td>${runStatusChip(r.status)}</td>
            <td class="actions-col">${actions.join(' ')}</td>
          </tr>`;
      }).join('');

      container.innerHTML = `
        <div class="siiap-table-wrapper">
          <table class="table siiap-table mb-0 table-sm runs-table align-middle">
            <caption class="visually-hidden">Respaldos generados y su estado</caption>
            <thead>
              <tr>
                <th scope="col">Identificador</th>
                <th scope="col">Tipo</th>
                <th scope="col">Expedientes</th>
                <th scope="col">Tamaño</th>
                <th scope="col">Generado</th>
                <th scope="col">Expira</th>
                <th scope="col">Estado</th>
                <th scope="col" class="text-end">Acciones</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;

      if (window.SIIAP && window.SIIAP.initDataTable) {
        container.querySelectorAll('.siiap-table-wrapper').forEach(window.SIIAP.initDataTable);
      }
    } catch (e) {
      flash('danger', `Error al cargar respaldos: ${e.message}`);
    }
  }

  // ── Bindings ───────────────────────────────────────────────────────────
  function bindGlobalActions() {
    document.body.addEventListener('click', (e) => {
      const reload = e.target.closest('[data-action="reload"]');
      if (reload) {
        loadCandidates(reload.dataset.category);
        return;
      }
      const reloadRuns = e.target.closest('[data-action="reload-runs"]');
      if (reloadRuns) {
        loadRuns();
        return;
      }
      const start = e.target.closest('[data-action="start-purge"]');
      if (start) {
        startPurge(start.dataset.category, start.dataset.purgeType);
        return;
      }
      const openConfirm = e.target.closest('[data-action="open-confirm"]');
      if (openConfirm) {
        openConfirmModal(openConfirm.dataset.runId);
        return;
      }
      const cancel = e.target.closest('[data-action="cancel-run"]');
      if (cancel) {
        cancelRun(cancel.dataset.runId);
        return;
      }
    });

    document.getElementById('confirmDownloadedCheck')?.addEventListener('change', (e) => {
      const btn = document.getElementById('btnDoConfirmPurge');
      if (btn) btn.disabled = !e.target.checked;
    });

    document.getElementById('btnDoConfirmPurge')?.addEventListener('click', doConfirmPurge);
  }

  // ── Init ───────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    bindGlobalActions();
    CATEGORIES.forEach(c => loadCandidates(c));
    loadRuns();
  });
})();
