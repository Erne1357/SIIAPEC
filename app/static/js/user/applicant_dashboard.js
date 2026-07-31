/* app/static/js/user/applicant_dashboard.js
 * Lógica del dashboard de aspirante (carta de aceptación en diferimiento +
 * flujo completo de aceptación: carta, tira de materias, boleta).
 *
 * Depende de window.SIIAP_APPLICANT_DASH = {
 *   userId, userProgramId, admissionStatus, programId
 * }.
 */
(function () {
  'use strict';

  const CFG = window.SIIAP_APPLICANT_DASH || {};
  const userId = CFG.userId;
  const userProgramId = CFG.userProgramId;
  if (!userId || !userProgramId) return;

  // ── Helpers ──────────────────────────────────────────────────────────────
  function buildDownloadUrl(filePath) {
    if (!filePath) return '#';
    const parts = filePath.split('/');
    return '/files/doc/' + parts[0] + '/' + parts[1] + '/' + parts.slice(2).join('/');
  }

  function getCsrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function escHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** Marca la región asíncrona como ocupada/lista y lo anuncia (WCAG 4.1.3). */
  function setBusy(el, busy, message) {
    if (window.SIIAP && typeof window.SIIAP.setBusy === 'function') {
      window.SIIAP.setBusy(el, busy, message ? { message } : undefined);
    } else if (el) {
      el.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
  }

  /** Chip de estado del sistema de diseño (nunca `badge` de Bootstrap). */
  function badge(status, label) {
    if (window.SIIAP && typeof window.SIIAP.statusBadge === 'function') {
      return window.SIIAP.statusBadge(status, label, 'sm');
    }
    return `<span class="status-badge status-badge--${escHtml(status)} status-badge--sm">${escHtml(label || status)}</span>`;
  }

  // ── Carta de aceptación en estado diferido ───────────────────────────────
  async function loadDeferralLetter() {
    const container = document.getElementById('deferralAcceptanceLetterSection');
    if (!container) return;
    setBusy(container, true, 'Buscando tu carta de aceptación…');
    try {
      const resp = await fetch(`/api/v1/acceptance/user/${userId}/program/${userProgramId}/status`);
      const result = await resp.json();
      if (result.error) {
        container.innerHTML = '';
        setBusy(container, false);
        return;
      }
      const letter = result.data?.acceptance_letter;
      if (letter && letter.file_path) {
        const url = buildDownloadUrl(letter.file_path);
        container.innerHTML = `
          <a href="${escHtml(url)}" target="_blank" rel="noopener" class="btn btn-outline-success btn-sm">
            <i class="bi bi-download me-1" aria-hidden="true"></i>Descargar carta de aceptación
          </a>`;
        setBusy(container, false, 'Tu carta de aceptación está disponible para descarga.');
      } else {
        container.innerHTML = '';
        setBusy(container, false);
      }
    } catch (e) {
      container.innerHTML = '';
      setBusy(container, false);
    }
  }

  // ── Flujo de aceptación (aspirante aceptado) ─────────────────────────────
  function acceptanceErrorState(detail) {
    return `
      <div class="empty-state empty-state--error empty-state--compact">
        <div class="empty-state__icon"><i class="bi bi-exclamation-octagon" aria-hidden="true"></i></div>
        <h3 class="empty-state__title">No pudimos cargar tus documentos de aceptación</h3>
        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
        ${detail ? `<p class="empty-state__error-detail">${escHtml(detail)}</p>` : ''}
        <div class="empty-state__actions">
          <button type="button" id="retryAcceptanceDocs" class="btn btn-outline-primary">
            <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
          </button>
        </div>
      </div>`;
  }

  async function loadAcceptanceDocs() {
    const container = document.getElementById('acceptanceDocsContent');
    if (!container) return;
    setBusy(container, true, 'Cargando tus documentos de aceptación…');
    try {
      const resp = await fetch(`/api/v1/acceptance/user/${userId}/program/${userProgramId}/status`);
      const result = await resp.json();

      if (result.error) {
        container.innerHTML = acceptanceErrorState(result.error.message);
        document.getElementById('retryAcceptanceDocs')?.addEventListener('click', loadAcceptanceDocs);
        setBusy(container, false, 'No se pudieron cargar los documentos de aceptación.');
        return;
      }

      const docs = result.data;
      const letter = docs.acceptance_letter;
      const schedule = docs.course_schedule;
      const receipt = docs.enrollment_receipt;

      const hasLetter   = letter   && letter.file_path;
      const hasSchedule = schedule && schedule.file_path;
      const receiptApproved = receipt && receipt.status === 'approved';
      const receiptRejected = receipt && receipt.status === 'rejected';
      const receiptUploaded = receipt && receipt.status === 'uploaded';

      const letterCard = buildDocCard(
        'Carta de Aceptación', 'bi-file-earmark-text',
        hasLetter, 'Para formalizar tu admisión al programa',
        hasLetter ? buildDownloadBtn(buildDownloadUrl(letter.file_path)) : ''
      );

      const scheduleCard = buildDocCard(
        'Tira de Materias', 'bi-list-check',
        hasSchedule, 'Materias que cursarás este semestre',
        hasSchedule ? buildDownloadBtn(buildDownloadUrl(schedule.file_path)) : ''
      );

      let receiptAction = '';
      let receiptStatus = 'pending';
      let receiptNote = 'Pendiente de subir';

      if (receiptApproved) {
        receiptStatus = 'approved';
        receiptNote = 'Aprobada por el coordinador';
        receiptAction = badge('approved', 'Aprobada');
      } else if (receiptUploaded) {
        receiptStatus = 'review';
        receiptNote = 'Subida, en revisión por el coordinador';
        receiptAction = badge('review', 'En revisión');
      } else if (receiptRejected) {
        receiptStatus = 'rejected';
        receiptNote = 'Rechazada: ' + (receipt.review_notes || 'sin especificar');
        if (hasLetter && hasSchedule) {
          receiptAction = '<button type="button" class="btn btn-primary btn-sm" id="uploadReceiptBtn"><i class="bi bi-upload me-1" aria-hidden="true"></i>Volver a subir</button>';
        }
      } else if (hasLetter && hasSchedule) {
        receiptAction = '<button type="button" class="btn btn-primary btn-sm" id="uploadReceiptBtn"><i class="bi bi-upload me-1" aria-hidden="true"></i>Subir boleta</button>';
      } else {
        receiptAction = '<small class="text-muted">Disponible cuando el coordinador suba la carta y la tira de materias</small>';
      }

      const receiptCard = buildReceiptCard(receiptStatus, receiptNote, receiptAction);
      container.innerHTML = '<div class="row g-3">' + letterCard + scheduleCard + receiptCard + '</div>';
      setBusy(container, false, 'Documentos de aceptación actualizados.');

      const btn = document.getElementById('uploadReceiptBtn');
      if (btn) btn.addEventListener('click', triggerReceiptUpload);

    } catch (err) {
      console.error('Error loading acceptance docs:', err);
      container.innerHTML = acceptanceErrorState(err.message);
      document.getElementById('retryAcceptanceDocs')?.addEventListener('click', loadAcceptanceDocs);
      setBusy(container, false, 'No se pudieron cargar los documentos de aceptación.');
    }
  }

  /* Tarjeta de documento del flujo de aceptación. El tono va como fondo suave
     de 1px completo (nunca border-left de color) y el estado siempre lleva
     chip con texto, no solo color (WCAG 1.4.1). */
  function buildAcceptanceCard(icon, title, note, chipHtml, toneClass, actionHtml) {
    return `
      <div class="col-md-4">
        <div class="acceptance-doc-card ${toneClass} h-100">
          <i class="bi ${escHtml(icon)} icon-2xl" aria-hidden="true"></i>
          <p class="acceptance-doc-card__title">${escHtml(title)}</p>
          <p class="acceptance-doc-card__note">${escHtml(note)}</p>
          <p class="mb-2">${chipHtml}</p>
          ${actionHtml}
        </div>
      </div>`;
  }

  function buildDocCard(title, icon, isAvailable, subtitle, actionHtml) {
    return buildAcceptanceCard(
      icon, title, subtitle,
      isAvailable ? badge('approved', 'Disponible') : badge('pending', 'Pendiente'),
      isAvailable ? 'acceptance-doc-card--success' : '',
      actionHtml
    );
  }

  function buildReceiptCard(status, note, actionHtml) {
    const tones = {
      approved: 'acceptance-doc-card--success',
      review: 'acceptance-doc-card--info',
      rejected: 'acceptance-doc-card--danger',
      pending: 'acceptance-doc-card--warning',
    };
    return buildAcceptanceCard(
      'bi-receipt', 'Boleta de inscripción', note, '',
      tones[status] || '', actionHtml
    );
  }

  function buildDownloadBtn(url) {
    return `<a href="${escHtml(url)}" target="_blank" rel="noopener" class="btn btn-success btn-sm">
      <i class="bi bi-download me-1" aria-hidden="true"></i>Descargar
    </a>`;
  }

  function triggerReceiptUpload() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf,.doc,.docx';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const formData = new FormData();
      formData.append('file', file);

      const btn = document.getElementById('uploadReceiptBtn');
      if (btn) { btn.disabled = true; btn.textContent = 'Subiendo...'; }

      try {
        const resp = await fetch(
          `/api/v1/acceptance/user/${userId}/program/${userProgramId}/submit-receipt`,
          {
            method: 'POST',
            headers: { 'X-CSRFToken': getCsrf() },
            body: formData,
          }
        );
        const result = await resp.json();
        if (result.flash) result.flash.forEach(f => showSimpleToast(f.message, f.level));
        if (!result.error) loadAcceptanceDocs();
      } catch (err) {
        showSimpleToast('Error al subir la boleta', 'danger');
        if (btn) { btn.disabled = false; btn.textContent = 'Subir Boleta'; }
      }
    };
    input.click();
  }

  // ── Toast ad-hoc (se conserva del inline original) ───────────────────────
  function showSimpleToast(message, level) {
    let cont = document.getElementById('toast-container');
    if (!cont) {
      cont = document.createElement('div');
      cont.id = 'toast-container';
      cont.className = 'toast-container position-fixed top-0 end-0 p-3 siiap-toast-stack';
      document.body.appendChild(cont);
    }
    const id = 'toast-' + Date.now();
    cont.insertAdjacentHTML('beforeend', `
      <div id="${id}" class="toast align-items-center text-bg-${escHtml(level)} border-0" role="alert" aria-live="assertive" aria-atomic="true">
        <div class="d-flex">
          <div class="toast-body">${escHtml(message)}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto tap-target" data-bs-dismiss="toast" aria-label="Cerrar aviso"></button>
        </div>
      </div>`);
    const el = document.getElementById(id);
    new bootstrap.Toast(el, { autohide: true, delay: 4000 }).show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    loadDeferralLetter();   // sólo actúa si existe #deferralAcceptanceLetterSection
    loadAcceptanceDocs();   // sólo actúa si existe #acceptanceDocsContent
  });

  // Tiempo real: recargar documentos de aceptación cuando hay cambios
  window.addEventListener('siiap:acceptance:updated', () => {
    loadAcceptanceDocs();
  });

  const DASH_CTX = window.SIIAP_APPLICANT_DASH || {};

  // Tiempo real: documento revisado → progreso y contadores cambian
  window.addEventListener('siiap:submission:reviewed', (e) => {
    const data = e.detail || {};
    const action = data.status === 'approved' ? 'aprobado' : 'rechazado';
    showSimpleToast(`Un documento ha sido ${action}. Actualizando...`, data.status === 'approved' ? 'success' : 'warning');
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  // Tiempo real: decisión de prórroga
  window.addEventListener('siiap:extension:decided', (e) => {
    const data = e.detail || {};
    const messages = {
      granted:   { text: 'Tu solicitud de prórroga fue aprobada.', type: 'success' },
      rejected:  { text: 'Tu solicitud de prórroga fue rechazada.', type: 'warning' },
      cancelled: { text: 'Tu solicitud de prórroga fue cancelada.', type: 'info' },
    };
    const m = messages[data.status];
    if (!m) return;
    showSimpleToast(`${m.text} Actualizando...`, m.type);
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  // Tiempo real: cambio de estado de admisión → banners y timeline cambian
  window.addEventListener('siiap:admission:status_changed', (e) => {
    const data = e.detail || {};
    if (DASH_CTX.programId && data.program_id && DASH_CTX.programId !== data.program_id) return;
    const messages = {
      interview_completed: { text: 'Tu entrevista fue marcada como completada.', type: 'info' },
      deliberation:        { text: 'Tu expediente entró en deliberación.',        type: 'warning' },
      accepted:            { text: '¡Has sido aceptado al programa!',             type: 'success' },
      rejected:            { text: 'Se actualizó el estado de tu admisión.',      type: 'warning' },
      in_progress:         { text: 'Tu expediente fue reabierto para correcciones.', type: 'info' },
    };
    const m = messages[data.new_status];
    if (!m) return;
    showSimpleToast(`${m.text} Actualizando...`, m.type);
    setTimeout(() => { window.location.reload(); }, 2500);
  });
})();
