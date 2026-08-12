/* app/static/js/user/student_dashboard.js
 * Lógica del dashboard de estudiante (módulos Permanencia, Referencia Bancaria,
 * Documentos del Semestre, Solicitud de Baja Temporal).
 *
 * Depende de window.SIIAP_STUDENT_DASH = { upId: <int> } inyectado en la plantilla.
 */
(function () {
  'use strict';

  const CFG = window.SIIAP_STUDENT_DASH || {};
  const UP_ID = CFG.upId;
  if (!UP_ID) return;

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  // ── Utils ────────────────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function flashMsg(level, message) {
    if (typeof showFlash === 'function') showFlash(level, message);
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

  /** Estado vacío de error, con botón de reintento. */
  function errorState(title, detail, retryId) {
    return `
      <div class="empty-state empty-state--error empty-state--compact">
        <div class="empty-state__icon"><i class="bi bi-exclamation-octagon" aria-hidden="true"></i></div>
        <h3 class="empty-state__title">${escHtml(title)}</h3>
        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
        ${detail ? `<p class="empty-state__error-detail">${escHtml(detail)}</p>` : ''}
        <div class="empty-state__actions">
          <button type="button" id="${escHtml(retryId)}" class="btn btn-outline-primary">
            <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
          </button>
        </div>
      </div>`;
  }

  // ── Documentos del semestre ──────────────────────────────────────────────
  async function loadStudentDocs() {
    const container = document.getElementById('studentDocsContainer');
    if (!container) return;
    setBusy(container, true, 'Cargando documentos del semestre…');
    try {
      const res = await fetch(`/api/v1/permanence/user-program/${UP_ID}/documents`);
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      renderStudentDocs(json.data || []);
    } catch (e) {
      container.innerHTML = errorState(
        'No pudimos cargar tus documentos del semestre.', e.message, 'retryStudentDocs');
      document.getElementById('retryStudentDocs')?.addEventListener('click', loadStudentDocs);
      setBusy(container, false, 'No se pudieron cargar los documentos del semestre.');
    }
  }

  function renderStudentDocs(docs) {
    const container = document.getElementById('studentDocsContainer');
    if (!docs.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-folder-x" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin documentos solicitados</h3>
          <p class="empty-state__description">No hay documentos solicitados para este periodo.</p>
        </div>`;
      setBusy(container, false, 'No hay documentos solicitados para este periodo.');
      return;
    }

    const rows = docs.map(d => {
      const dl = d.deadline;
      const sub = d.submission;
      const archive = d.archive;

      const closesAt = dl.closes_at
        ? `<span class="small text-muted">Cierra: ${new Date(dl.closes_at).toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' })}</span>`
        : '';

      let statusHtml = '';
      let actionHtml = '';

      if (!sub) {
        if (dl.is_currently_open) {
          statusHtml = badge('pending', 'Pendiente');
          actionHtml = buildUploadForm(UP_ID, dl.id, dl.label);
        } else {
          statusHtml = badge('deferred', 'Ventana cerrada');
        }
      } else if (sub.status === 'review') {
        statusHtml = badge('review', 'En revisión');
      } else if (sub.status === 'approved') {
        statusHtml = badge('approved', 'Aprobado');
        // `file_url` lo construye el servidor y nombra la fila; `file_path` ya
        // sólo trae el nombre humano del archivo, no una ruta.
        if (sub.file_url) {
          actionHtml = `<a href="${escHtml(sub.file_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-success">
            <i class="bi bi-eye me-1" aria-hidden="true"></i>Ver documento
          </a>`;
        }
      } else if (sub.status === 'rejected') {
        statusHtml = badge('rejected', 'Rechazado');
        if (sub.reviewer_comment) {
          statusHtml += `<p class="small text-danger-strong mt-1 mb-0">Motivo: ${escHtml(sub.reviewer_comment)}</p>`;
        }
        if (dl.is_currently_open) {
          actionHtml = buildUploadForm(UP_ID, dl.id, dl.label);
        }
      }

      return `
        <li class="border-bottom py-3">
          <div class="d-flex align-items-start gap-3 flex-wrap">
            <div class="flex-grow-1">
              <p class="fw-semibold mb-0">${escHtml(dl.label)}</p>
              <p class="small text-muted mb-0">${escHtml(archive.name)} ${closesAt}</p>
              <div class="mt-1">${statusHtml}</div>
            </div>
            ${actionHtml ? `<div class="flex-shrink-0">${actionHtml}</div>` : ''}
          </div>
        </li>`;
    }).join('');

    container.innerHTML = `<ul class="list-unstyled mb-0">${rows}</ul>`;
    setBusy(container, false, `${docs.length} documento(s) del semestre cargados.`);

    container.querySelectorAll('.doc-upload-form').forEach(form => {
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const upId = form.dataset.upId;
        const dlId = form.dataset.dlId;
        const fileInput = form.querySelector('input[type=file]');
        if (!fileInput.files.length) return;
        const btn = form.querySelector('button[type=submit]');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span class="visually-hidden">Subiendo…</span>';

        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        try {
          const res = await fetch(`/api/v1/permanence/user-program/${upId}/documents/${dlId}`, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: fd,
          });
          const json = await res.json();
          if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));
          if (res.ok && !json.error) loadStudentDocs();
          else btn.disabled = false;
        } catch (err) {
          btn.disabled = false;
          flashMsg('danger', 'Error al subir el documento.');
        }
      });
    });
  }

  function buildUploadForm(upId, dlId, label) {
    const inputId = `docUpload-${upId}-${dlId}`;
    return `
      <form class="doc-upload-form" data-up-id="${escHtml(String(upId))}" data-dl-id="${escHtml(String(dlId))}">
        <label class="visually-hidden" for="${inputId}">Archivo para ${escHtml(label || 'el documento')}</label>
        <div class="input-group input-group-sm doc-upload-group">
          <input type="file" id="${inputId}" class="form-control doc-upload-input"
                 accept=".pdf,.doc,.docx,.jpg,.png" required>
          <button type="submit" class="btn btn-primary text-nowrap">
            <i class="bi bi-upload me-1" aria-hidden="true"></i>Subir
          </button>
        </div>
      </form>`;
  }

  // ── Pago de inscripción semestral ────────────────────────────────────────
  async function loadEnrollmentPayment() {
    const container = document.getElementById('enrollmentPaymentContainer');
    if (!container) return;
    setBusy(container, true, 'Verificando tu inscripción…');

    try {
      const res = await fetch('/api/v1/permanence/my-enrollment');
      const json = await res.json();

      if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));

      if (!res.ok || json.error) {
        if (res.status === 404) {
          container.innerHTML = `
            <div class="alert alert-info d-flex gap-2 align-items-center mb-0 py-2 small" role="note">
              <i class="bi bi-info-circle-fill fs-5" aria-hidden="true"></i>
              <span>No hay inscripción activa para este periodo.</span>
            </div>`;
          setBusy(container, false, 'No hay inscripción activa para este periodo.');
        } else {
          throw new Error(json.error?.message || 'Error');
        }
        return;
      }

      renderEnrollmentPayment(json.data);

    } catch (e) {
      container.innerHTML = errorState(
        'No pudimos cargar tu información de pago.', e.message, 'retryEnrollmentPayment');
      document.getElementById('retryEnrollmentPayment')?.addEventListener('click', loadEnrollmentPayment);
      setBusy(container, false, 'No se pudo cargar la información de pago.');
    }
  }

  function renderEnrollmentPayment(data) {
    const container = document.getElementById('enrollmentPaymentContainer');
    if (!container) return;
    const card = document.getElementById('cardEnrollmentPayment');

    if (!data) {
      if (card) card.classList.add('d-none');
      setBusy(container, false);
      return;
    }

    // Inscripción confirmada — ocultar la card completa
    if (data.enrollment_confirmed) {
      if (card) card.classList.add('d-none');
      setBusy(container, false);
      return;
    }

    // Inscripción pendiente de confirmación
    const paymentRef = data.payment_reference || {};
    const ref    = paymentRef.reference || null;
    const amount = paymentRef.amount    || data.payment_amount     || null;
    const due    = paymentRef.due_date  || data.payment_due_date   || null;
    // URL opaco servido por el backend; `payment_proof_path` ya sólo es el
    // nombre humano del archivo.
    const proof  = data.payment_proof_url || null;

    let refHtml = '';
    if (ref) {
      refHtml = `
        <div class="mb-2">
          <span class="text-muted small">Referencia bancaria:</span>
          <code class="ms-2 fs-6">${escHtml(ref)}</code>
        </div>`;
    } else {
      refHtml = `
        <div class="mb-2 text-muted small">
          <i class="bi bi-info-circle me-1"></i>
          Referencia bancaria: <span class="text-muted">— (disponible próximamente)</span>
        </div>`;
    }

    let amountHtml = '';
    if (amount) {
      amountHtml = `<div class="small text-muted mb-1">Monto: <strong>$${escHtml(String(amount))}</strong></div>`;
    }

    let dueHtml = '';
    if (due) {
      const dueFormatted = new Date(due + 'T00:00:00').toLocaleDateString('es-MX', { day: '2-digit', month: 'long', year: 'numeric' });
      dueHtml = `<div class="small text-muted mb-2">Fecha límite de pago: <strong>${escHtml(dueFormatted)}</strong></div>`;
    }

    let proofHtml = '';
    if (proof) {
      proofHtml = `
        <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
          ${badge('review', 'Pendiente de confirmación por el coordinador')}
          <a href="${escHtml(proof)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">
            <i class="bi bi-eye me-1" aria-hidden="true"></i>Ver comprobante actual
          </a>
        </div>`;
    }

    const uploadBtn = `
      <button type="button" class="btn btn-primary btn-sm"
              data-bs-toggle="modal" data-bs-target="#modalPaymentProof">
        <i class="bi bi-upload me-1" aria-hidden="true"></i>Subir comprobante de pago
      </button>`;

    container.innerHTML = `
      <div>
        ${refHtml}
        ${amountHtml}
        ${dueHtml}
        ${proofHtml}
        ${uploadBtn}
      </div>`;
    setBusy(container, false, 'Información de pago actualizada.');
  }

  function bindPaymentProofForm() {
    const form = document.getElementById('formPaymentProof');
    if (!form) return;

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const fileInput = document.getElementById('paymentProofFile');
      if (!fileInput || !fileInput.files.length) return;

      const btn = document.getElementById('btnSubmitPaymentProof');
      const originalHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Subiendo…';
      }

      const fd = new FormData();
      fd.append('payment_proof', fileInput.files[0]);

      try {
        const res = await fetch('/api/v1/permanence/my-enrollment/payment-proof', {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
          body: fd,
        });
        const json = await res.json();

        if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));

        if (res.ok && !json.error) {
          // Cerrar modal
          const modalEl = document.getElementById('modalPaymentProof');
          if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
          }
          // Recargar la tarjeta
          loadEnrollmentPayment();
        } else {
          if (!json.flash) flashMsg('danger', json.error?.message || 'Error al subir el comprobante.');
          if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
          }
        }
      } catch (err) {
        flashMsg('danger', 'Error de red al subir el comprobante.');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = originalHtml;
        }
      }
    });
  }

  // ── Solicitud de Baja Temporal ───────────────────────────────────────────
  async function loadLeaveRequest() {
    const container = document.getElementById('leaveRequestContainer');
    if (!container) return;
    setBusy(container, true, 'Verificando tu solicitud de baja temporal…');
    try {
      const res = await fetch(`/api/v1/permanence/user-program/${UP_ID}/leave-request`);
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      renderLeaveRequest(json.data);
    } catch (e) {
      container.innerHTML = errorState(
        'No pudimos cargar tu solicitud de baja temporal.', e.message, 'retryLeaveRequest');
      document.getElementById('retryLeaveRequest')?.addEventListener('click', loadLeaveRequest);
      setBusy(container, false, 'No se pudo cargar la solicitud de baja temporal.');
    }
  }

  function renderLeaveRequest(data) {
    const container = document.getElementById('leaveRequestContainer');
    if (!container) return;

    if (!data.archive_available) {
      container.innerHTML = `
        <p class="text-muted small mb-0">
          <i class="bi bi-info-circle me-1" aria-hidden="true"></i>
          La solicitud de baja temporal no está disponible para tu programa actualmente.
        </p>`;
      setBusy(container, false, 'La solicitud de baja temporal no está disponible.');
      return;
    }

    const sub = data.submission;

    if (!sub) {
      container.innerHTML = `
        <p class="text-muted small mb-2">
          Puedes solicitar una baja temporal subiendo el formulario firmado por tu director de tesis.
        </p>
        ${buildLeaveUploadForm()}`;
      bindLeaveUploadForm();
      setBusy(container, false, 'Puedes enviar tu solicitud de baja temporal.');
      return;
    }

    if (sub.status === 'review') {
      container.innerHTML = `
        <div class="d-flex align-items-center gap-2 flex-wrap">
          ${badge('review', 'En revisión')}
          <span class="small text-muted">Tu solicitud está siendo revisada por el coordinador.</span>
        </div>`;
      setBusy(container, false, 'Tu solicitud de baja temporal está en revisión.');
      return;
    }

    if (sub.status === 'approved') {
      container.innerHTML = `
        <div class="d-flex align-items-center gap-2 flex-wrap">
          ${badge('approved', 'Aprobada')}
          <span class="small text-muted">Tu solicitud de baja temporal fue aprobada.</span>
        </div>`;
      setBusy(container, false, 'Tu solicitud de baja temporal fue aprobada.');
      return;
    }

    if (sub.status === 'rejected') {
      const motivo = sub.reviewer_comment
        ? `<p class="small text-danger-strong mt-1 mb-0">Motivo: ${escHtml(sub.reviewer_comment)}</p>`
        : '';
      container.innerHTML = `
        <div class="mb-3">
          ${badge('rejected', 'Rechazada')}
          ${motivo}
        </div>
        <p class="text-muted small mb-2">Puedes volver a subir la solicitud corregida.</p>
        ${buildLeaveUploadForm()}`;
      bindLeaveUploadForm();
      setBusy(container, false, 'Tu solicitud de baja temporal fue rechazada.');
      return;
    }

    setBusy(container, false);
  }

  function buildLeaveUploadForm() {
    return `
      <form id="leaveUploadForm" class="d-flex align-items-center gap-2 flex-wrap">
        <label class="visually-hidden" for="leaveFile">Formulario de baja temporal firmado</label>
        <input type="file" class="form-control form-control-sm leave-upload-input" id="leaveFile"
               accept=".pdf,.doc,.docx,.jpg,.png" required>
        <button type="submit" class="btn btn-sm btn-outline-secondary text-nowrap">
          <i class="bi bi-upload me-1" aria-hidden="true"></i>Enviar solicitud
        </button>
      </form>`;
  }

  function bindLeaveUploadForm() {
    const form = document.getElementById('leaveUploadForm');
    if (!form) return;
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const file = document.getElementById('leaveFile')?.files[0];
      if (!file) return;
      const btn = form.querySelector('button[type=submit]');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span class="visually-hidden">Enviando…</span>';

      const fd = new FormData();
      fd.append('file', file);
      try {
        const res = await fetch(`/api/v1/permanence/user-program/${UP_ID}/leave-request`, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
          body: fd,
        });
        const json = await res.json();
        if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));
        if (res.ok && !json.error) loadLeaveRequest();
        else btn.disabled = false;
      } catch (err) {
        btn.disabled = false;
        flashMsg('danger', 'Error al enviar la solicitud.');
      }
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    loadEnrollmentPayment();
    bindPaymentProofForm();
    loadStudentDocs();
    loadLeaveRequest();
  });

  // ── Tiempo real ──────────────────────────────────────────────────────────
  function toast(level, message) {
    if (typeof showFlash === 'function') showFlash(level, message);
  }

  window.addEventListener('siiap:permanence:status_changed', (e) => {
    const data = e.detail || {};
    const messages = {
      semester_confirmed: { text: `Tu inscripción del semestre ${data.semester_number || ''} fue confirmada.`, level: 'success' },
      doc_reviewed:       { text: data.status === 'approved'
                               ? `Documento "${data.document_label || ''}" aprobado.`
                               : `Documento "${data.document_label || ''}" rechazado.`,
                            level: data.status === 'approved' ? 'success' : 'warning' },
      leave_decided:      { text: data.approved
                               ? 'Tu solicitud de baja temporal fue aprobada.'
                               : 'Tu solicitud de baja temporal fue rechazada.',
                            level: data.approved ? 'success' : 'warning' },
    };
    const m = messages[data.action];
    if (!m) return;
    toast(m.level, `${m.text} Actualizando...`);
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  window.addEventListener('siiap:submission:reviewed', (e) => {
    const data = e.detail || {};
    const action = data.status === 'approved' ? 'aprobado' : 'rechazado';
    toast(data.status === 'approved' ? 'success' : 'warning', `Un documento ha sido ${action}. Actualizando...`);
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  window.addEventListener('siiap:extension:decided', (e) => {
    const data = e.detail || {};
    const messages = {
      granted:   { text: 'Tu solicitud de prórroga fue aprobada.', level: 'success' },
      rejected:  { text: 'Tu solicitud de prórroga fue rechazada.', level: 'warning' },
      cancelled: { text: 'Tu solicitud de prórroga fue cancelada.', level: 'info' },
    };
    const m = messages[data.status];
    if (!m) return;
    toast(m.level, `${m.text} Actualizando...`);
    setTimeout(() => { window.location.reload(); }, 2000);
  });
})();
