// app/static/js/admin/settings/period_transition.js
//
// Gestiona el modal "Cerrar periodo y avanzar a siguiente":
//   1. Al abrir el modal, hace GET /transition/preview y rellena tabs.
//   2. Al confirmar, hace POST /transition/execute con doble confirmación.
//
// Depende de:
//   window.PERIOD_TRANSITION — inyectado por academic_periods.html (puede ser null)
//   showFlash()              — global de flash.js
//   AcademicPeriodsManager  — instancia en window (academicPeriodsManager)

(function () {
  'use strict';

  // ── Constantes ─────────────────────────────────────────────────────────────
  const API_PREVIEW = '/api/v1/permanence/transition/preview';
  const API_EXECUTE = '/api/v1/permanence/transition/execute';

  // ── Estado ─────────────────────────────────────────────────────────────────
  let previewData = null;       // último resultado del preview
  let sourcePeriodId = null;    // id del periodo activo (origen)
  let targetPeriodId = null;    // id del siguiente periodo (destino)
  let confirmStep = 0;          // 0 = primer click, 1 = esperando segundo click
  let modalInstance = null;     // Bootstrap Modal

  // ── Utils ──────────────────────────────────────────────────────────────────
  function getCsrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  }

  function flash(level, message) {
    if (typeof showFlash === 'function') showFlash(level, message);
  }

  function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = String(str == null ? '' : str);
    return div.innerHTML;
  }

  function fmtDate(isoStr) {
    if (!isoStr) return '—';
    if (window.SIIAP && window.SIIAP.formatDate) return window.SIIAP.formatDate(isoStr, 'short');
    return new Date(isoStr).toLocaleDateString('es-MX', {
      day: '2-digit', month: 'short', year: 'numeric'
    });
  }

  // ── Código legible para bloqueadores ───────────────────────────────────────
  const BLOCKER_LABELS = {
    enrollment_not_confirmed:  'Pago no confirmado',
    missing_documents:         'Documentos de permanencia pendientes',
    on_leave:                  'Baja temporal activa',
    conacyt_missing:           'Reportes SECIHTI pendientes',
    enrollment_missing:        'Sin inscripción en este periodo',
    status_not_active:         'Estado de inscripción no activo',
  };

  function blockerLabel(code) {
    return BLOCKER_LABELS[code] || escHtml(code);
  }

  // ── Conteo de items de preview ─────────────────────────────────────────────
  function count(arr) {
    return Array.isArray(arr) ? arr.length : 0;
  }

  // ── Inicialización del botón de transición ─────────────────────────────────
  // El botón se inyecta dinámicamente por renderPeriods() de AcademicPeriodsManager,
  // así que usamos delegación de eventos en el container.
  function bindTransitionButton() {
    const container = document.getElementById('periodsContainer');
    if (!container) return;

    container.addEventListener('click', function (e) {
      const btn = e.target.closest('.btn-period-transition');
      if (!btn) return;
      sourcePeriodId = btn.dataset.sourcePeriodId ? parseInt(btn.dataset.sourcePeriodId, 10) : null;
      targetPeriodId = btn.dataset.targetPeriodId ? parseInt(btn.dataset.targetPeriodId, 10) : null;
      openTransitionModal();
    });
  }

  // ── Apertura del modal ─────────────────────────────────────────────────────
  function openTransitionModal() {
    const modalEl = document.getElementById('modalPeriodTransition');
    if (!modalEl) return;

    if (!modalInstance) {
      modalInstance = new bootstrap.Modal(modalEl);
    }

    // Resetear estado de confirmación
    resetConfirmStep();

    // Actualizar encabezado con códigos de periodo
    updateModalHeader();

    // Mostrar spinner mientras carga preview
    showPreviewLoading();

    modalInstance.show();

    // Cargar preview (no bloqueante: el modal ya está abierto)
    loadPreview();
  }

  function updateModalHeader() {
    const srcEl = document.getElementById('transitionSourceCode');
    const tgtEl = document.getElementById('transitionTargetCode');

    // Leer códigos desde el botón activo
    const btn = document.querySelector(`.btn-period-transition[data-source-period-id="${sourcePeriodId}"]`);
    if (btn) {
      if (srcEl) srcEl.textContent = btn.dataset.sourcePeriodCode || '—';
      if (tgtEl) tgtEl.textContent = btn.dataset.targetPeriodCode || '—';
    }
  }

  // ── Preview ────────────────────────────────────────────────────────────────
  async function loadPreview() {
    if (!sourcePeriodId || !targetPeriodId) {
      renderNoNextPeriod();
      return;
    }

    try {
      const params = new URLSearchParams({
        source_period_id: sourcePeriodId,
        target_period_id: targetPeriodId,
      });
      const res = await fetch(`${API_PREVIEW}?${params}`, {
        headers: { 'X-CSRFToken': getCsrf() }
      });
      const json = await res.json();

      if (json.flash) json.flash.forEach(f => flash(f.level, f.message));

      if (!res.ok || json.error) {
        showPreviewError(json.error?.message || 'Error al cargar vista previa.');
        return;
      }

      previewData = json.data || {};
      renderPreview(previewData);

    } catch (err) {
      console.error('period_transition: preview error', err);
      showPreviewError('Error de red al cargar la vista previa.');
    }
  }

  function showPreviewLoading() {
    setTabContent('tabAdvance',           spinnerHtml());
    setTabContent('tabBlocked',           spinnerHtml());
    setTabContent('tabAdmitMigrate',      spinnerHtml());
    setTabContent('tabAdmitExpire',       spinnerHtml());
    setTabContent('tabAdmitAligned',      spinnerHtml());
    setTabContent('tabDeferred',          spinnerHtml());
    setTabContent('tabOnLeave',           spinnerHtml());
    updateTabBadges({ will_advance: [], will_block: [], admission_migrate: [], admission_expire: [], admission_already_aligned: [], deferred_reactivate: [], on_leave: [] });
    disableConfirm(true);
  }

  function showPreviewError(msg) {
    const html = `<div class="alert alert-danger m-3"><i class="bi bi-exclamation-triangle me-2"></i>${escHtml(msg)}</div>`;
    ['tabAdvance','tabBlocked','tabAdmitMigrate','tabAdmitExpire','tabAdmitAligned','tabDeferred','tabOnLeave'].forEach(id => setTabContent(id, html));
    disableConfirm(true);
  }

  function renderNoNextPeriod() {
    const html = `
      <div class="alert alert-warning m-3">
        <i class="bi bi-exclamation-circle me-2"></i>
        No existe un periodo siguiente configurado. Crea el próximo periodo académico antes de ejecutar esta operación.
      </div>`;
    ['tabAdvance','tabBlocked','tabAdmitMigrate','tabAdmitExpire','tabAdmitAligned','tabDeferred','tabOnLeave'].forEach(id => setTabContent(id, html));
    disableConfirm(true);
  }

  function renderPreview(data) {
    const willAdvance      = data.will_advance              || [];
    const willBlock        = data.will_block                || [];
    const admitMigrate     = data.admission_migrate         || [];
    const admitExpire      = data.admission_expire          || [];
    const admitAligned     = data.admission_already_aligned || [];
    const deferredReact    = data.deferred_reactivate       || [];
    const onLeave          = data.on_leave                  || [];

    updateTabBadges({
      will_advance: willAdvance,
      will_block: willBlock,
      admission_migrate: admitMigrate,
      admission_expire: admitExpire,
      admission_already_aligned: admitAligned,
      deferred_reactivate: deferredReact,
      on_leave: onLeave,
    });

    setTabContent('tabAdvance',      renderAdvanceTable(willAdvance));
    setTabContent('tabBlocked',      renderBlockedTable(willBlock));
    setTabContent('tabAdmitMigrate', renderSimpleTable(admitMigrate,    'Aspirantes que serán migrados al nuevo periodo activo.'));
    setTabContent('tabAdmitExpire',  renderSimpleTable(admitExpire,     'Aspirantes que serán marcados como expirados.'));
    setTabContent('tabAdmitAligned', renderSimpleTable(admitAligned,    'Aspirantes ya alineados al periodo destino — su admisión continuará allí sin cambios.'));
    setTabContent('tabDeferred',     renderSimpleTable(deferredReact,   'Aspirantes diferidos que serán reactivados en el nuevo periodo.'));
    setTabContent('tabOnLeave',      renderSimpleTable(onLeave,         'Estudiantes en baja temporal (no serán afectados por el avance).'));

    // Habilitar confirmar solo si hay algo que hacer (already_aligned no requiere acción)
    const total = willAdvance.length + admitMigrate.length + admitExpire.length + deferredReact.length;
    disableConfirm(total === 0);
  }

  // ── Renderizado de tablas ──────────────────────────────────────────────────
  function spinnerHtml() {
    return `<div class="text-center py-4">
      <div class="spinner-border text-primary" role="status"><span class="visually-hidden">Cargando...</span></div>
    </div>`;
  }

  function emptyState(msg) {
    return `<div class="empty-state empty-state--compact">
      <div class="empty-state__icon"><i class="bi bi-check2-circle" aria-hidden="true"></i></div>
      <p class="empty-state__title">${escHtml(msg)}</p>
    </div>`;
  }

  function userFullName(item) {
    const u = item.user || {};
    return escHtml([u.first_name, u.last_name].filter(Boolean).join(' ') || u.email || '—');
  }

  function userEmail(item) {
    const u = item.user || {};
    return escHtml(u.email || '—');
  }

  function programName(item) {
    // Prioridad: item.program (objeto poblado por el backend con id/name/slug)
    // → up.program_name → up.program → '—'
    const p = item.program || {};
    if (p.name) return escHtml(p.name);
    if (p.slug) return escHtml(p.slug);
    const up = item.user_program || {};
    return escHtml(up.program_name || up.program_slug || up.program || '—');
  }

  function semesterNum(item) {
    const up = item.user_program || {};
    return escHtml(String(up.current_semester || '—'));
  }

  function renderAdvanceTable(items) {
    if (!items.length) return emptyState('No hay estudiantes elegibles para avanzar en este momento.');
    const rows = items.map(it => `
      <tr>
        <td>${userFullName(it)}</td>
        <td class="text-muted small">${userEmail(it)}</td>
        <td>${programName(it)}</td>
        <td class="text-center">${semesterNum(it)}</td>
        <td class="text-center"><i class="bi bi-arrow-up-circle-fill text-success"></i> Avanzará</td>
      </tr>`).join('');
    return tableWrap(['Estudiante', 'Correo', 'Programa', 'Semestre', 'Acción'], rows);
  }

  function renderBlockedTable(items) {
    if (!items.length) return emptyState('No hay estudiantes bloqueados.');
    const rows = items.map((it, idx) => {
      const blockers = it.blockers || [];
      const blockerId = `blockers-${idx}`;
      const blockersHtml = blockers.map(b => {
        let detail = '';
        if (b.deadlines && b.deadlines.length) {
          const dls = b.deadlines.map(d => `<li class="small">${escHtml(d.label || d.id)}</li>`).join('');
          detail = `<ul class="mb-0 mt-1 ps-3">${dls}</ul>`;
        } else if (b.months && b.months.length) {
          detail = `<div class="small mt-1">Meses pendientes: ${b.months.map(m => escHtml(String(m))).join(', ')}</div>`;
        }
        return `<li>${blockerLabel(b.code)}${detail}</li>`;
      }).join('');

      return `
        <tr>
          <td>${userFullName(it)}</td>
          <td class="text-muted small">${userEmail(it)}</td>
          <td>${programName(it)}</td>
          <td class="text-center">${semesterNum(it)}</td>
          <td>
            <button class="btn btn-sm btn-outline-danger py-0"
                    type="button"
                    data-bs-toggle="collapse"
                    data-bs-target="#${blockerId}"
                    aria-expanded="false">
              <i class="bi bi-exclamation-triangle me-1"></i>${blockers.length} razón${blockers.length !== 1 ? 'es' : ''}
            </button>
            <div class="collapse mt-2" id="${blockerId}">
              <ul class="mb-0 small text-danger">${blockersHtml}</ul>
            </div>
          </td>
        </tr>`;
    }).join('');
    return tableWrap(['Estudiante', 'Correo', 'Programa', 'Semestre', 'Razones'], rows);
  }

  function renderSimpleTable(items, emptyMsg) {
    if (!items.length) return emptyState(emptyMsg);
    const rows = items.map(it => `
      <tr>
        <td>${userFullName(it)}</td>
        <td class="text-muted small">${userEmail(it)}</td>
        <td>${programName(it)}</td>
        <td class="text-center text-muted small">${escHtml(String((it.user_program || {}).admission_status || '—'))}</td>
      </tr>`).join('');
    return tableWrap(['Nombre', 'Correo', 'Programa', 'Estado'], rows);
  }

  function tableWrap(headers, rows, caption) {
    const ths = headers.map(h => `<th scope="col">${escHtml(h)}</th>`).join('');
    const html = `
      <div class="siiap-table-wrapper">
        <table class="table siiap-table table-sm table-hover align-middle mb-0">
          <caption class="visually-hidden">${escHtml(caption || 'Vista previa de la transición de periodo')}</caption>
          <thead class="table-light"><tr>${ths}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    return html;
  }

  // ── Actualizar badges de tabs ──────────────────────────────────────────────
  function updateTabBadges(data) {
    setBadge('badgeAdvance',      count(data.will_advance));
    setBadge('badgeBlocked',      count(data.will_block));
    setBadge('badgeAdmitMigrate', count(data.admission_migrate));
    setBadge('badgeAdmitExpire',  count(data.admission_expire));
    setBadge('badgeAdmitAligned', count(data.admission_already_aligned));
    setBadge('badgeDeferred',     count(data.deferred_reactivate));
    setBadge('badgeOnLeave',      count(data.on_leave));
  }

  function setBadge(id, n) {
    const el = document.getElementById(id);
    if (el) el.textContent = n;
  }

  function setTabContent(id, html) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = html;
    // El contenido se inyecta tras DOMContentLoaded: activar la pista de scroll.
    if (window.SIIAP && window.SIIAP.initDataTable) {
      el.querySelectorAll('.siiap-table-wrapper').forEach(window.SIIAP.initDataTable);
    }
  }

  // ── Ejecución con doble confirmación ──────────────────────────────────────
  function disableConfirm(disabled) {
    const btn = document.getElementById('btnConfirmTransition');
    if (!btn) return;
    btn.disabled = disabled;
  }

  function resetConfirmStep() {
    confirmStep = 0;
    const btn1 = document.getElementById('btnConfirmTransition');
    const btn2 = document.getElementById('btnConfirmTransition2');
    const hint = document.getElementById('confirmHint');
    if (btn1) {
      btn1.classList.remove('d-none');
      btn1.disabled = false;
      btn1.innerHTML = '<i class="bi bi-check-circle me-1"></i>Confirmar y aplicar';
    }
    if (btn2) btn2.classList.add('d-none');
    if (hint) hint.classList.add('d-none');
  }

  function bindConfirmButton() {
    const btn1 = document.getElementById('btnConfirmTransition');
    const btn2 = document.getElementById('btnConfirmTransition2');

    if (btn1) {
      btn1.addEventListener('click', function () {
        // Primer click: mostrar segundo botón de doble confirmación
        btn1.classList.add('d-none');
        if (btn2) btn2.classList.remove('d-none');
        const hint = document.getElementById('confirmHint');
        if (hint) hint.classList.remove('d-none');
        confirmStep = 1;
      });
    }

    if (btn2) {
      btn2.addEventListener('click', function () {
        executeTransition();
      });
    }

    // Al cerrar el modal, resetear estado
    const modalEl = document.getElementById('modalPeriodTransition');
    if (modalEl) {
      modalEl.addEventListener('hidden.bs.modal', resetConfirmStep);
    }
  }

  async function executeTransition() {
    const btn2 = document.getElementById('btnConfirmTransition2');
    if (btn2) {
      btn2.disabled = true;
      btn2.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Aplicando...';
    }

    try {
      const payload = {
        source_period_id: sourcePeriodId,
        target_period_id: targetPeriodId,
      };

      const res = await fetch(API_EXECUTE, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf(),
        },
        body: JSON.stringify(payload),
      });

      const json = await res.json();

      if (json.flash) json.flash.forEach(f => flash(f.level, f.message));

      if (!res.ok || json.error) {
        flash('danger', json.error?.message || 'Error al ejecutar la transición.');
        resetConfirmStep();
        return;
      }

      // Backend devuelve {advanced, blocked, ...} para program_id específico
      // o {total: {...}, programs: [...]} para ejecución global.
      const raw = json.data || {};
      const stats = raw.total ? raw.total : raw;
      const advanced  = stats.advanced            ?? 0;
      const blocked   = stats.blocked             ?? 0;
      const migrated  = stats.admission_migrated  ?? 0;
      const expired   = stats.admission_expired   ?? 0;
      const onLeave   = stats.on_leave            ?? 0;
      const reactivated = stats.deferred_reactivated ?? 0;

      flash('success',
        `Transición completada. Avanzaron: ${advanced} · Bloqueados: ${blocked} · `
        + `Baja temporal: ${onLeave} · Migrados: ${migrated} · `
        + `Expirados: ${expired} · Reactivados: ${reactivated}`);

      // ── Snapshot ZIP de aspirantes Δ=2 (si lo hubo) ──────────────────────
      const archive = (raw && raw.archive) || (stats && stats.archive);
      if (archive && archive.run_id) {
        const url = archive.archive_url || `/api/v1/admin/purge/${archive.run_id}/archive.zip`;
        flash('info',
          `Se generó respaldo preventivo de ${archive.item_count} aspirante(s) expirado(s). Descargando...`);
        try {
          const a = document.createElement('a');
          a.href = url;
          a.download = `purge_${archive.run_id}.zip`;
          document.body.appendChild(a);
          a.click();
          setTimeout(() => a.remove(), 1500);
        } catch (e) {
          flash('warning', `No se pudo iniciar la descarga automática. Recupera manualmente desde Configuración → Limpieza.`);
        }
      } else if (archive && archive.error) {
        flash('warning', `Snapshot preventivo falló: ${archive.error}`);
      }

      if (modalInstance) modalInstance.hide();

      // Refrescar lista de periodos
      if (window.academicPeriodsManager && typeof window.academicPeriodsManager.loadPeriods === 'function') {
        window.academicPeriodsManager.loadPeriods();
      }

    } catch (err) {
      console.error('period_transition: execute error', err);
      flash('danger', 'Error de red al ejecutar la transición.');
      resetConfirmStep();
    }
  }

  // ── Exponer hook para que AcademicPeriodsManager inyecte el botón ─────────
  //
  // AcademicPeriodsManager llama window.PeriodTransition.getButtonHtml(period, nextPeriod)
  // al renderizar la card del periodo activo. Devuelve '' si no hay nextPeriod.
  window.PeriodTransition = {
    /**
     * @param {object} activePeriod  — el periodo activo
     * @param {object|null} nextPeriod — el siguiente periodo cronológico (puede ser null)
     * @returns {string} HTML del botón
     */
    getButtonHtml(activePeriod, nextPeriod) {
      if (!nextPeriod) return '';
      return `
        <button type="button"
                class="btn btn-warning btn-sm btn-period-transition"
                data-source-period-id="${activePeriod.id}"
                data-source-period-code="${escHtml(activePeriod.code)}"
                data-target-period-id="${nextPeriod.id}"
                data-target-period-code="${escHtml(nextPeriod.code)}"
                title="Cerrar periodo ${escHtml(activePeriod.code)} y avanzar a ${escHtml(nextPeriod.code)}">
          <i class="bi bi-arrow-right-circle me-1" aria-hidden="true"></i>Cerrar periodo y avanzar
        </button>`;
    }
  };

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    const modalEl = document.getElementById('modalPeriodTransition');
    if (!modalEl) return;   // el modal solo existe si has_perm es true

    bindTransitionButton();
    bindConfirmButton();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
