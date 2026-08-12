// static/js/program/admission.js - Flash corregido
document.addEventListener('DOMContentLoaded', () => {

  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };
  const csrf = getCsrf();

  // FUNCIÓN FLASH CORRECTA
  function flash(message, level = 'success') {
    window.dispatchEvent(new CustomEvent('flash', {
      detail: { level: level, message: message }
    }));
  }

  function persistFlashes(flashes) {
    try {
      const arr = Array.isArray(flashes) ? flashes : [];
      sessionStorage.setItem('flashQueue', JSON.stringify(arr));
    } catch (_) { }
  }

  function closeModal(modalEl) {
    if (!modalEl) return;
    let inst = bootstrap.Modal.getInstance(modalEl);
    if (!inst) inst = new bootstrap.Modal(modalEl);
    inst.hide();
  }

  const uploadModal = document.getElementById('uploadModal');
  const extensionModal = document.getElementById('extensionModal');
  let currentStepForHash = null;

  // ==================== INICIALIZAR TOOLTIPS ====================
  const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  const tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl);
  });

  // ==================== MODAL DE SUBIDA MEJORADO ====================
  if (uploadModal) {
    uploadModal.addEventListener('show.bs.modal', event => {
      const btn = event.relatedTarget;
      const archId = btn?.getAttribute('data-archive');
      const stepId = btn?.getAttribute('data-step');
      const archiveName = btn?.getAttribute('data-archive-name');
      const hasExisting = btn?.getAttribute('data-has-existing') === 'true';
      const existingFilename = btn?.getAttribute('data-existing-filename');
      const existingDate = btn?.getAttribute('data-existing-date');

      const inputArchive = uploadModal.querySelector('[name="archive_id"]');
      if (inputArchive) inputArchive.value = archId || '';
      currentStepForHash = stepId || null;

      const existingInfo = document.getElementById('existingFileInfo');
      const uploadBtnText = document.getElementById('uploadBtnText');
      const uploadSubmitBtn = document.getElementById('uploadSubmitBtn');

      // SIEMPRE empezar ocultando la información de archivo existente
      existingInfo.style.display = 'none';

      // DEBUG: Mostrar todos los valores para diagnosticar
      
      
      
      
      

      // Solo mostrar información de archivo existente si realmente hay uno Y tiene datos válidos
      const shouldShow = hasExisting === true 
        && existingFilename 
        && typeof existingFilename === 'string' 
        && existingFilename.trim() !== '' 
        && existingDate 
        && typeof existingDate === 'string' 
        && existingDate.trim() !== '';
      
      
      
      if (shouldShow) {
        
        document.getElementById('existingArchiveName').textContent = archiveName || '';
        document.getElementById('existingFilename').textContent = existingFilename;
        document.getElementById('existingDate').textContent = existingDate;
        // Mostrar el contenedor
        existingInfo.style.display = 'block';
        existingInfo.classList.remove('d-none');
        uploadBtnText.textContent = 'Reemplazar';
        uploadSubmitBtn.className = 'btn btn-warning';
      } else {
        
        existingInfo.style.display = 'none';
        existingInfo.classList.add('d-none');
        uploadBtnText.textContent = 'Subir';
        uploadSubmitBtn.className = 'btn btn-primary';
      }
    });

    uploadModal.addEventListener('hidden.bs.modal', () => {
      const fileInput = uploadModal.querySelector('input[type="file"]');
      
      if (fileInput) fileInput.value = '';
      
      // Siempre ocultar la información de archivo existente al cerrar
      const existingInfo = document.getElementById('existingFileInfo');
      existingInfo.style.display = 'none';
      existingInfo.classList.add('d-none');
      
      // Limpiar contenido para evitar que se muestre información residual
      document.getElementById('existingArchiveName').textContent = '';
      document.getElementById('existingFilename').textContent = '';
      document.getElementById('existingDate').textContent = '';
    });
  }

  // ==================== MODAL DE PRÓRROGA ====================
  if (extensionModal) {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowString = tomorrow.toISOString().split('T')[0];
    document.getElementById('extensionRequestedUntil').min = tomorrowString;
  }

  // ==================== SOLICITAR PRÓRROGA ====================
  document.addEventListener('click', (e) => {
    if (e.target.closest('.btn-request-extension')) {
      const button = e.target.closest('.btn-request-extension');
      const archiveId = button.getAttribute('data-archive-id');
      const archiveName = button.getAttribute('data-archive-name');

      if (archiveId && archiveName) {
        document.getElementById('extensionArchiveId').value = archiveId;
        document.getElementById('extensionArchiveName').textContent = archiveName;

        document.getElementById('extensionRequestedUntil').value = '';
        document.getElementById('extensionReason').value = '';

        const modal = new bootstrap.Modal(extensionModal);
        modal.show();
      }
    }
  });

  // ==================== ENVIAR SOLICITUD DE PRÓRROGA ====================
  document.getElementById('extensionForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const archiveId = document.getElementById('extensionArchiveId').value;
    const requestedUntil = document.getElementById('extensionRequestedUntil').value;
    const reason = document.getElementById('extensionReason').value.trim();

    if (!archiveId || !requestedUntil || !reason) {
      flash('Completa todos los campos requeridos', 'warning');
      return;
    }

    const submitBtn = e.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<i class="bi bi-arrow-repeat bi-spin" aria-hidden="true"></i> Enviando...';
    submitBtn.disabled = true;

    try {
      const payload = {
        // Identificador público del archivo (UUID): cadena, no entero.
        archive_id: archiveId,
        requested_until: requestedUntil,
        reason: reason
      };

      const res = await fetch('/api/v1/extensions/requests', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf
        },
        body: JSON.stringify(payload)
      });

      const json = await res.json().catch(() => ({}));

      if (!res.ok) {
        const msg = json?.error || 'No se pudo enviar la solicitud de prórroga.';
        flash(msg, 'danger');
        return;
      }

      flash('Solicitud de prórroga enviada exitosamente. Recibirás una respuesta pronto', 'success');
      closeModal(extensionModal);

      setTimeout(() => window.location.reload(), 1500);

    } catch (err) {
      console.error('Extension request error:', err);
      flash('Error de red al enviar la solicitud', 'danger');
    } finally {
      submitBtn.innerHTML = originalText;
      submitBtn.disabled = false;
    }
  });

  // Restaurar pestaña activa desde hash
  const hash = window.location.hash;
  if (hash && hash.startsWith('#pane-')) {
    const btn = document.querySelector(`button[data-bs-target="${hash}"]`);
    if (btn) new bootstrap.Tab(btn).show();
  }

  // Actualizar hash cuando cambie de tab
  document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(btn => {
    btn.addEventListener('shown.bs.tab', (e) => {
      const target = e.target.getAttribute('data-bs-target');
      if (target) {
        window.location.hash = target;
      }
    });
  });

  // ==================== SUBIR ARCHIVOS CON VALIDACIONES ====================
  document.querySelectorAll('form[data-admission-upload="true"]').forEach(form => {
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(form);
      const archiveId = fd.get('archive_id');
      const file = fd.get('file');

      if (!archiveId || !file || (file instanceof File && !file.name)) {
        flash('Selecciona un archivo válido', 'danger');
        return;
      }

      if (file.size > 3 * 1024 * 1024) {
        flash('El archivo no puede superar los 3MB', 'danger');
        return;
      }

      if (!file.name.toLowerCase().endsWith('.pdf')) {
        flash('Solo se permiten archivos PDF', 'danger');
        return;
      }

      const submitBtn = form.querySelector('button[type="submit"]');
      const originalText = submitBtn.innerHTML;
      submitBtn.innerHTML = '<i class="bi bi-arrow-repeat bi-spin" aria-hidden="true"></i> Subiendo...';
      submitBtn.disabled = true;

      try {
        const res = await fetch('/api/v1/submissions', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrf },
          body: fd
        });
        const json = await res.json().catch(() => ({}));

        if (!res.ok) {
          const msg = json?.error?.message || 'No se pudo subir el documento.';
          if (Array.isArray(json.flash) && json.flash.length) {
            json.flash.forEach(f => flash(f.message || msg, f.level || 'danger'));
          } else {
            flash(msg, 'danger');
          }
          return;
        }

        closeModal(uploadModal);

        const flashes = Array.isArray(json.flash) && json.flash.length
          ? json.flash
          : [{ level: 'success', message: 'Documento subido exitosamente.' }];

        if (currentStepForHash) {
          // Buscar si este step está en un tab combinado
          const combinedTab = document.querySelector(`[data-bs-target*="pane-combined-"][data-bs-target*="${currentStepForHash}"]`);
          if (combinedTab) {
            const target = combinedTab.getAttribute('data-bs-target');
            window.location.hash = target;
          } else {
            window.location.hash = `#pane-${currentStepForHash}`;
          }
        }
        persistFlashes(flashes);
        window.location.reload();

      } catch (err) {
        console.error('Upload error:', err);
        flash('Error de red al subir', 'danger');
      } finally {
        submitBtn.innerHTML = originalText;
        submitBtn.disabled = false;
      }
    });
  });

  // ==================== ELIMINAR ARCHIVOS ====================
  document.querySelectorAll('[data-delete-submission]').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const subId = btn.getAttribute('data-delete-submission');
      const stepId = btn.getAttribute('data-step');
      if (!subId) return;

      const ok = await siiapConfirm({
        type: 'danger',
        title: 'Eliminar archivo',
        message: '¿Eliminar este archivo?',
        confirmLabel: 'Sí, eliminar',
      });
      if (!ok) return;

      try {
        const res = await fetch(`/api/v1/submissions/${subId}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrf }
        });
        const json = await res.json().catch(() => ({}));

        if (!res.ok) {
          const msg = json?.error?.message || 'No se pudo eliminar.';
          if (Array.isArray(json.flash) && json.flash.length) {
            json.flash.forEach(f => flash(f.message || msg, f.level || 'danger'));
          } else {
            flash(msg, 'danger');
          }
          return;
        }

        const flashes = Array.isArray(json.flash) && json.flash.length
          ? json.flash
          : [{ level: 'success', message: 'Archivo eliminado.' }];

        if (stepId) window.location.hash = `#pane-${stepId}`;
        persistFlashes(flashes);
        window.location.reload();

      } catch (err) {
        console.error('Delete error:', err);
        flash('Error de red al eliminar', 'danger');
      }
    });
  });

  // ==================== VALIDACIÓN DE ARCHIVOS EN TIEMPO REAL ====================
  document.querySelectorAll('input[type="file"]').forEach(input => {
    input.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
      let feedbackEl = e.target.parentNode.querySelector('.file-feedback');

      if (!feedbackEl) {
        feedbackEl = document.createElement('div');
        feedbackEl.className = 'file-feedback small mt-1';
        e.target.parentNode.appendChild(feedbackEl);
      }

      const fileName = SIIAP.escapeHtml(file.name);

      if (file.size > 3 * 1024 * 1024) {
        feedbackEl.className = 'file-feedback small mt-1 text-danger';
        feedbackEl.innerHTML = `<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> Archivo: ${fileName} (${sizeMB} MB) - EXCEDE EL LÍMITE`;
      } else if (!file.name.toLowerCase().endsWith('.pdf')) {
        feedbackEl.className = 'file-feedback small mt-1 text-danger';
        feedbackEl.innerHTML = `<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> Solo se permiten archivos PDF`;
      } else {
        feedbackEl.className = 'file-feedback small mt-1 text-success';
        feedbackEl.innerHTML = `<i class="bi bi-check-lg" aria-hidden="true"></i> Archivo: ${fileName} (${sizeMB} MB)`;
      }
    });
  });

  // ==================== CARGAR INFORMACIÓN DE CITA ASIGNADA ====================
  loadInterviewInfo();

  // Pinta el panel de entrevista conservando role/aria-live y apagando aria-busy.
  // Antes se reasignaba className a secas y el panel se quedaba en blanco
  // (y anunciándose como "cargando") en cuanto la petición fallaba.
  function renderInterviewPanel(extraClass, html) {
    const panel = document.querySelector('.interview-info');
    if (!panel) return null;
    panel.className = `interview-info ${extraClass}`.trim();
    panel.setAttribute('aria-busy', 'false');
    panel.innerHTML = html;
    return panel;
  }

  async function loadInterviewInfo() {
    const panel = document.querySelector('.interview-info');
    if (!panel) return;

    try {
      const res = await fetch('/api/v1/appointments/mine/active', {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('appointments-request-failed');

      const json = await res.json();
      if (json.ok && json.appointments && json.appointments.length > 0) {
        renderInterviewPanel('', '');
        json.appointments.forEach(appt => showInterviewCard(appt));
      } else if (json.ok && !json.appointments.length) {
        // Sin cita asignada: informar si falta completar el perfil.
        const meRes = await fetch('/api/v1/users/me', { credentials: 'same-origin' });
        const userInfo = await meRes.json();

        if (meRes.ok && !userInfo.data.user.profile_completed) {
          renderInterviewPanel('alert alert-warning', `
            <i class="bi bi-person-fill-gear me-2" aria-hidden="true"></i>
            Completa tu perfil para ser elegible para entrevista.
            <a href="/user/profile" class="alert-link">Ir a mi perfil</a>
          `);
        } else {
          renderInterviewPanel('alert alert-info', `
            <i class="bi bi-info-circle-fill me-2" aria-hidden="true"></i>
            Eres elegible para entrevista. Pronto recibirás una notificación con la fecha y hora asignada.
          `);
        }
      } else {
        throw new Error('unexpected-payload');
      }
    } catch (err) {
      console.error('Interview info error:', err);
      renderInterviewPanel('alert alert-danger', `
        <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
        No pudimos cargar la información de tu entrevista.
        <button type="button" class="btn btn-sm btn-outline-danger ms-2" id="retryInterviewInfo">
          Reintentar
        </button>
      `);
      document.getElementById('retryInterviewInfo')?.addEventListener('click', () => {
        const p = document.querySelector('.interview-info');
        if (p) {
          p.className = 'interview-info';
          p.setAttribute('aria-busy', 'true');
          p.innerHTML = '<div class="skeleton skeleton-card skeleton-card--sm"></div>';
        }
        loadInterviewInfo();
      });
    }
  }

  function showInterviewCard(appointment) {
    const interviewCard = document.createElement('div');
    interviewCard.className = 'alert alert-success shadow-sm mb-4';
    interviewCard.id = `interviewCard-${appointment.id}`;

    const startDate = new Date(appointment.starts_at);
    const endDate = new Date(appointment.ends_at);
    const dateStr = startDate.toLocaleDateString('es-MX', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    });
    const timeStr = `${startDate.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })} - ${endDate.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}`;

    interviewCard.innerHTML = `
      <div class="row align-items-center">
        <div class="col-12 col-md-8">
          <h3 class="h5 mb-2">
            <i class="bi bi-calendar-check me-2" aria-hidden="true"></i>
            ${SIIAP.escapeHtml(appointment.event_title)}
          </h3>
          <p class="mb-2">
            <strong><i class="bi bi-clock me-1" aria-hidden="true"></i> Fecha y hora:</strong> ${SIIAP.escapeHtml(dateStr)}<br>
            <strong><i class="bi bi-hourglass-split me-1" aria-hidden="true"></i> Horario:</strong> ${SIIAP.escapeHtml(timeStr)}<br>
            <strong><i class="bi bi-geo-alt-fill me-1" aria-hidden="true"></i> Lugar:</strong> ${SIIAP.escapeHtml(appointment.location || 'Por confirmar')}
          </p>
          ${appointment.notes ? `
            <div class="alert alert-info py-2 mb-0">
              <small><strong>Notas:</strong> ${SIIAP.escapeHtml(appointment.notes)}</small>
            </div>
          ` : ''}
        </div>
        <div class="col-12 col-md-4 text-md-end mt-3 mt-md-0">
          ${appointment.pending_change_request ? `
            <div class="alert alert-warning py-2 mb-2 text-start">
              <small><i class="bi bi-clock me-1" aria-hidden="true"></i><strong>Solicitud en revisión</strong><br>
              Tu solicitud de cambio está pendiente de respuesta.</small>
            </div>
            <button class="btn btn-outline-secondary btn-sm w-100 w-md-auto btn-request-change"
                    data-appointment-id="${SIIAP.escapeAttr(appointment.id)}"
                    data-pending-reason="${SIIAP.escapeAttr(appointment.pending_change_request.reason || '')}"
                    data-pending-suggestions="${SIIAP.escapeAttr(appointment.pending_change_request.suggestions || '')}">
              <i class="bi bi-pencil-square me-1" aria-hidden="true"></i>
              Editar Solicitud
            </button>
          ` : `
            <button class="btn btn-outline-warning btn-sm w-100 w-md-auto btn-request-change"
                    data-appointment-id="${SIIAP.escapeAttr(appointment.id)}">
              <i class="bi bi-arrow-left-right me-1" aria-hidden="true"></i>
              Solicitar Cambio
            </button>
          `}
        </div>
      </div>
    `;

    const processAlert = document.querySelector('.interview-info');
    if (processAlert) {
      processAlert.parentNode.insertBefore(interviewCard, processAlert.nextSibling);
    }
  }

  // ==================== SOLICITAR CAMBIO DE CITA ====================
  document.addEventListener('click', (e) => {
    if (e.target.closest('.btn-request-change')) {
      const button = e.target.closest('.btn-request-change');
      const appointmentId = button.getAttribute('data-appointment-id');
      const pendingReason = button.getAttribute('data-pending-reason') || '';
      const pendingSuggestions = button.getAttribute('data-pending-suggestions') || '';

      if (appointmentId) {
        openChangeRequestModal(appointmentId, pendingReason, pendingSuggestions);
      }
    }
  });

  const changeRequestModalEl = document.getElementById('changeRequestModal');
  const changeRequestModal = changeRequestModalEl ? new bootstrap.Modal(changeRequestModalEl) : null;
  const changeRequestForm = document.getElementById('changeRequestForm');

  changeRequestForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const appointmentId = document.getElementById('changeReqAppointmentId').value;
    const reason = document.getElementById('changeReqReason').value.trim();
    const suggestions = document.getElementById('changeReqSuggestions').value.trim();

    await requestAppointmentChange(appointmentId, reason, suggestions || null);
    changeRequestModal?.hide();
    changeRequestForm.reset();
  });

  function openChangeRequestModal(appointmentId, pendingReason = '', pendingSuggestions = '') {
    document.getElementById('changeReqAppointmentId').value = appointmentId;
    document.getElementById('changeReqReason').value = pendingReason;
    document.getElementById('changeReqSuggestions').value = pendingSuggestions;

    if (changeRequestModal) {
      changeRequestModal.show();
    }
  }

  async function requestAppointmentChange(appointmentId, reason, suggestions) {
    try {
      const payload = { reason };
      if (suggestions) payload.suggestions = suggestions;

      const res = await fetch(`/api/v1/appointments/${appointmentId}/change-requests`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf
        },
        body: JSON.stringify(payload)
      });

      const json = await res.json();

      if (!res.ok || !json.ok) {
        flash(json.error || 'No se pudo enviar la solicitud', 'danger');
        return;
      }

      flash('Solicitud de cambio enviada. El coordinador la revisará pronto', 'success');

    } catch (err) {
      console.error('Change request error:', err);
      flash('Error al enviar la solicitud de cambio', 'danger');
    }
  }

  // ==================== TIEMPO REAL: ACTUALIZAR AL RECIBIR REVISIÓN ====================
  window.addEventListener('siiap:submission:reviewed', (e) => {
    const data = e.detail;
    if (!data) return;

    // Buscar la fila del documento revisado y actualizar el badge de estado
    const archiveId = data.archive_id;
    const newStatus = data.status; // 'approved' o 'rejected'

    // Buscar el botón de upload que corresponde a este archive_id
    const uploadBtn = document.querySelector(`[data-archive="${archiveId}"]`);
    if (!uploadBtn) return;

    // Buscar el contenedor del step
    const stepCard = uploadBtn.closest('.step-card, .card, .accordion-item, [data-step]');
    if (!stepCard) return;

    // Actualizar el chip de estado con el componente compartido
    const statusBadge = stepCard.querySelector('.status-badge');
    if (statusBadge && (newStatus === 'approved' || newStatus === 'rejected')) {
      statusBadge.replaceWith(SIIAP.statusBadgeEl(newStatus));
    }

    // Mostrar toast informativo
    const action = newStatus === 'approved' ? 'aprobado' : 'rechazado';
    flash(`Un documento ha sido ${action}. La página se actualizará.`, newStatus === 'approved' ? 'success' : 'warning');

    // Recargar la página después de un breve delay para mostrar los cambios completos
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  // ==================== TIEMPO REAL: DECISIÓN DE PRÓRROGA ====================
  window.addEventListener('siiap:extension:decided', (e) => {
    const data = e.detail || {};
    const messages = {
      granted:   { text: 'Tu solicitud de prórroga fue aprobada.', level: 'success' },
      rejected:  { text: 'Tu solicitud de prórroga fue rechazada.', level: 'warning' },
      cancelled: { text: 'Tu solicitud de prórroga fue cancelada.', level: 'info' },
    };
    const m = messages[data.status];
    if (!m) return;
    flash(`${m.text} La página se actualizará.`, m.level);
    setTimeout(() => { window.location.reload(); }, 2000);
  });

  // ==================== TIEMPO REAL: CAMBIO DE ESTADO DE ADMISIÓN ====================
  window.addEventListener('siiap:admission:status_changed', (e) => {
    const data = e.detail;
    if (!data) return;

    const ctx = window.SIIAP_ADMISSION || {};
    if (ctx.programId && data.program_id && ctx.programId !== data.program_id) return;

    const messages = {
      interview_completed: { text: 'Tu entrevista fue marcada como completada.', level: 'info' },
      deliberation:        { text: 'Tu expediente entró en deliberación.',        level: 'warning' },
      accepted:            { text: '¡Has sido aceptado al programa!',             level: 'success' },
      rejected:            { text: 'Se actualizó el estado de tu admisión.',      level: 'warning' },
      in_progress:         { text: 'Tu expediente fue reabierto para correcciones.', level: 'info' },
    };

    const m = messages[data.new_status];
    if (!m) return;

    flash(`${m.text} La página se actualizará.`, m.level);
    setTimeout(() => { window.location.reload(); }, 2500);
  });

  // ── Subida de carta de asignación de número de control ────────────────────
  const receiptBtn = document.getElementById('receiptUploadBtn');
  if (receiptBtn && window.SIIAP_ADMISSION) {
    receiptBtn.addEventListener('click', () => submitEnrollmentReceipt());
  }

  function submitEnrollmentReceipt() {
    const cfg = window.SIIAP_ADMISSION || {};
    const fileInput = document.getElementById('receiptFileInput');
    const statusDiv = document.getElementById('receiptUploadStatus');
    const btn = document.getElementById('receiptUploadBtn');
    if (!cfg.userId || !cfg.programId) {
      statusDiv.innerHTML = '<div class="alert alert-danger py-1 small">Configuración inválida — recarga la página.</div>';
      return;
    }
    if (!fileInput.files.length) {
      statusDiv.innerHTML = '<div class="alert alert-warning py-1 small">Selecciona un archivo primero.</div>';
      return;
    }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.innerHTML = '<i class="bi bi-arrow-repeat bi-spin me-1" aria-hidden="true"></i>Subiendo…';
    statusDiv.innerHTML = '<p class="small mb-0">Subiendo la carta de asignación…</p>';

    const restoreButton = () => {
      btn.disabled = false;
      btn.setAttribute('aria-busy', 'false');
      btn.innerHTML = '<i class="bi bi-upload me-1" aria-hidden="true"></i>Subir carta';
    };

    fetch(`/api/v1/acceptance/user/${cfg.userId}/program/${cfg.programId}/submit-receipt`, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf },
      body: formData
    })
      .then(r => r.json())
      .then(res => {
        if (res.error) {
          statusDiv.innerHTML = '<div class="alert alert-danger py-1 small mb-0">' + SIIAP.escapeHtml(res.error.message || 'No se pudo subir el archivo.') + '</div>';
          restoreButton();
        } else {
          statusDiv.innerHTML = '<div class="alert alert-success py-1 small mb-0">Documento subido. El coordinador lo revisará pronto.</div>';
          setTimeout(() => location.reload(), 2000);
        }
      })
      .catch(() => {
        statusDiv.innerHTML = '<div class="alert alert-danger py-1 small mb-0">Error de red. Intenta de nuevo.</div>';
        restoreButton();
      });
  }
});