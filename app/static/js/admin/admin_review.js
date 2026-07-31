// static/js/admin/admin_review.js - Versión con gestión de prórrogas
document.addEventListener('DOMContentLoaded', () => {
  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };
  const csrf = getCsrf();

  function emitFlash(level, message) {
    window.dispatchEvent(new CustomEvent('flash', { detail: { level, message } }));
  }
  
  function persistFlashes(flashes) {
    try { sessionStorage.setItem('flashQueue', JSON.stringify(flashes || [])); } catch (_) {}
  }

  // ==================== REVISIÓN DE DOCUMENTOS ====================
  const reviewForm = document.querySelector('[data-review-form="true"]');
  if (reviewForm) {
    let pendingAction = null;
    const decisionButtons = Array.from(
      reviewForm.querySelectorAll('button[type="submit"][data-action]')
    );
    const decisionStatus = document.getElementById('decisionStatus');

    // Captura qué botón se pulsó (approve/reject)
    decisionButtons.forEach(btn => {
      btn.addEventListener('click', () => { pendingAction = btn.getAttribute('data-action'); });
    });

    // Bloquea la doble decisión mientras el envío está en curso.
    function setDecisionBusy(busy, message) {
      reviewForm.setAttribute('aria-busy', busy ? 'true' : 'false');
      decisionButtons.forEach(btn => {
        btn.disabled = busy;
        const icon = btn.querySelector('i.bi');
        if (!icon) return;
        if (busy) {
          if (!icon.dataset.restIcon) icon.dataset.restIcon = icon.className;
          icon.className = 'bi bi-arrow-repeat bi-spin me-1';
        } else if (icon.dataset.restIcon) {
          icon.className = icon.dataset.restIcon;
        }
      });
      if (decisionStatus) decisionStatus.textContent = message || '';
      if (message && window.SIIAP?.announce) window.SIIAP.announce(message);
    }

    reviewForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const subId = reviewForm.getAttribute('data-sub-id');
      const nextUrl = reviewForm.getAttribute('data-next-url') || window.location.href;
      const commentField = reviewForm.comment;
      const comment = commentField?.value?.trim() || '';

      if (!pendingAction) return;

      if (pendingAction === 'reject') {
        // El motivo es obligatorio: sin él, el aspirante no sabe qué corregir.
        if (!comment) {
          commentField?.setAttribute('aria-invalid', 'true');
          commentField?.focus();
          emitFlash('warning', 'Escribe el motivo del rechazo: el aspirante lo verá para corregir su documento.');
          return;
        }
        commentField?.removeAttribute('aria-invalid');

        const ok = await siiapConfirm({
          type: 'danger',
          title: 'Rechazar documento',
          message: 'El aspirante recibirá el motivo y podrá subir una nueva versión del documento.',
          confirmLabel: 'Rechazar y notificar',
        });
        if (!ok) return;
      }

      setDecisionBusy(true, 'Enviando decisión…');

      try {
        const res = await fetch(`/api/v1/admin/review/submissions/${subId}/decision`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrf
          },
          body: JSON.stringify({ action: pendingAction, comment })
        });
        const json = await res.json().catch(() => ({}));

        if (!res.ok) {
          const msg = json?.error?.message || 'No se pudo aplicar la acción.';
          if (Array.isArray(json.flash) && json.flash.length) {
            json.flash.forEach(f => emitFlash(f.level || 'danger', f.message || msg));
          } else {
            emitFlash('danger', msg);
          }
          setDecisionBusy(false, 'No se pudo registrar la decisión.');
          return;
        }

        const flashes = Array.isArray(json.flash) && json.flash.length
          ? json.flash
          : [{ level: 'success', message: 'Acción realizada exitosamente.' }];

        persistFlashes(flashes);
        window.location.href = nextUrl;

      } catch (err) {
        console.error('review decision error:', err);
        emitFlash('danger', 'Error de red. Intenta de nuevo.');
        setDecisionBusy(false, 'Error de red al enviar la decisión.');
      }
    });
  }

  // Reemplaza el onchange inline de la plantilla (regla: sin JS en el HTML).
  const showAllProgramsToggle = document.getElementById('showAllPrograms');
  showAllProgramsToggle?.addEventListener('change', (e) => {
    window.SIIAP?.announce?.(
      e.target.checked
        ? 'Mostrando también documentos de otros programas. Actualizando resultados…'
        : 'Mostrando solo los documentos de tus programas. Actualizando resultados…'
    );
    e.target.form?.submit();
  });

  // ==================== GESTIÓN DE PRÓRROGAS ====================
  const extensionsTab = document.getElementById('extensions-tab');
  const extensionsTableBody = document.getElementById('extensionsTableBody');
  const extensionReviewModal = document.getElementById('extensionReviewModal');
  
  let extensionRequests = [];
  let currentExtension = null;

  if (extensionsTab) {
    // Cargar prórrogas cuando se activa la pestaña
    extensionsTab.addEventListener('shown.bs.tab', loadExtensions);
    
    // Si la pestaña ya está activa al cargar
    if (extensionsTab.classList.contains('active')) {
      loadExtensions();
    }
  }

  // Botón de filtrar
  document.getElementById('filterExtensionsBtn')?.addEventListener('click', loadExtensions);

  // Mapea el estado de la prórroga al modificador de status-badge del sistema.
  const EXTENSION_STATUS = {
    pending:   { key: 'pending',  label: 'Pendiente' },
    granted:   { key: 'approved', label: 'Concedida' },
    rejected:  { key: 'rejected', label: 'Rechazada' },
    cancelled: { key: 'deferred', label: 'Cancelada' }
  };

  function extensionBadge(status) {
    const meta = EXTENSION_STATUS[status];
    if (!meta) return window.SIIAP.statusBadge('pending', 'Desconocido', 'sm');
    return window.SIIAP.statusBadge(meta.key, meta.label, 'sm');
  }

  function setExtensionsBusy(busy, message) {
    if (extensionsTableBody) {
      extensionsTableBody.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
    if (message) window.SIIAP?.announce?.(message);
  }

  async function loadExtensions() {
    if (!extensionsTableBody) return;

    setExtensionsBusy(true, 'Cargando solicitudes de prórroga…');
    extensionsTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">Cargando…</td></tr>';

    try {
      const params = new URLSearchParams();
      const status = document.getElementById('extensionStatusFilter')?.value;
      const programId = document.getElementById('extensionProgramFilter')?.value;
      const userId = document.getElementById('extensionStudentFilter')?.value;

      if (status) params.append('status', status);
      if (programId) params.append('program_id', programId);
      if (userId) params.append('user_id', userId);

      const res = await fetch(`/api/v1/extensions/requests?${params}`, {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('Error al cargar solicitudes');

      const json = await res.json();
      
      extensionRequests = json.items || [];

      renderExtensions();
      updateExtensionCounts();

    } catch (err) {
      console.error('Error loading extensions:', err);
      extensionsTableBody.innerHTML = `
        <tr>
          <td colspan="7">
            <div class="empty-state empty-state--inline empty-state--error">
              <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
              <h3 class="empty-state__title">No se pudieron cargar las solicitudes</h3>
              <p class="empty-state__description">Revisa tu conexión y vuelve a intentarlo.</p>
              <div class="empty-state__actions">
                <button type="button" id="retryExtensionsBtn" class="btn btn-outline-primary">
                  <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>
                  <span class="btn-label">Reintentar</span>
                </button>
              </div>
            </div>
          </td>
        </tr>`;
      document.getElementById('retryExtensionsBtn')?.addEventListener('click', loadExtensions);
      setExtensionsBusy(false, 'No se pudieron cargar las solicitudes de prórroga.');
    }
  }

  function renderExtensions() {
    if (!extensionsTableBody) return;

    if (extensionRequests.length === 0) {
      extensionsTableBody.innerHTML = `
        <tr>
          <td colspan="7">
            <div class="empty-state empty-state--inline">
              <div class="empty-state__icon"><i class="bi bi-inbox" aria-hidden="true"></i></div>
              <h3 class="empty-state__title">Sin solicitudes de prórroga</h3>
              <p class="empty-state__description">No hay solicitudes que coincidan con los filtros seleccionados.</p>
            </div>
          </td>
        </tr>`;
      setExtensionsBusy(false, 'Sin solicitudes de prórroga para los filtros aplicados.');
      return;
    }

    const esc = (s) => String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    extensionsTableBody.innerHTML = extensionRequests.map(ext => {
      const requestedDate = window.SIIAP.formatDate(ext.requested_until, 'numeric');
      const createdDate = window.SIIAP.formatDate(ext.created_at, 'numeric');
      const statusBadge = extensionBadge(ext.status);

      return `
        <tr data-extension-id="${ext.id}">
          <th scope="row" class="fw-normal">${ext.id}</th>
          <td>
            <div class="fw-semibold">${esc(ext.user_name || 'Sin nombre')}</div>
            <small class="text-muted">${esc(ext.user_email || '')}</small>
          </td>
          <td>${esc(ext.archive_name)}</td>
          <td>${requestedDate}</td>
          <td>${createdDate}</td>
          <td>${statusBadge}</td>
          <td>
            ${ext.status === 'pending' ? `
              <button type="button" class="btn btn-sm btn-outline-primary btn-review-extension"
                      data-extension-id="${ext.id}"
                      aria-label="Revisar la solicitud de prórroga #${ext.id}">
                <i class="bi bi-eye me-1" aria-hidden="true"></i>Revisar
              </button>
            ` : `
              <button type="button" class="btn btn-sm btn-outline-secondary btn-view-extension"
                      data-extension-id="${ext.id}"
                      aria-label="Ver el detalle de la solicitud de prórroga #${ext.id}">
                <i class="bi bi-info-circle-fill me-1" aria-hidden="true"></i>Ver
              </button>
            `}
          </td>
        </tr>
      `;
    }).join('');

    setExtensionsBusy(false, `${extensionRequests.length} solicitudes de prórroga cargadas.`);

    // Event listeners para botones
    document.querySelectorAll('.btn-review-extension, .btn-view-extension').forEach(btn => {
      btn.addEventListener('click', () => {
        const extId = parseInt(btn.getAttribute('data-extension-id'));
        openExtensionModal(extId);
      });
    });
  }

  function updateExtensionCounts() {
    const pendingCount = extensionRequests.filter(e => e.status === 'pending').length;
    const counter = document.getElementById('pendingExtensionsCount');
    if (counter) counter.textContent = pendingCount;
  }

  function openExtensionModal(extId) {
    const ext = extensionRequests.find(e => e.id === extId);
    if (!ext) return;

    currentExtension = ext;

    // Llenar datos del modal
    document.getElementById('reviewExtensionId').value = ext.id;
    document.getElementById('extensionStudentName').textContent = ext.user_name || 'N/A';
    document.getElementById('extensionStudentEmail').textContent = ext.user_email || '';
    document.getElementById('extensionArchiveName').textContent = ext.archive_name;
    document.getElementById('extensionRequestedUntil').textContent =
      window.SIIAP.formatDate(ext.requested_until);
    document.getElementById('extensionReason').textContent = ext.reason || 'Sin motivo especificado';

    // Pre-llenar fecha concedida con la solicitada (en hora local, sin desfase)
    const requestedDate = window.SIIAP.parseDate(ext.requested_until);
    document.getElementById('extensionGrantedUntil').value = requestedDate
      ? `${requestedDate.getFullYear()}-${String(requestedDate.getMonth() + 1).padStart(2, '0')}-${String(requestedDate.getDate()).padStart(2, '0')}`
      : '';

    // Si ya fue revisada, mostrar decisión
    if (ext.status !== 'pending') {
      document.getElementById('extensionConditions').value = ext.condition_text || '';
      document.getElementById('extensionConditions').disabled = true;
      document.getElementById('extensionGrantedUntil').disabled = true;
      document.getElementById('approveExtensionBtn').style.display = 'none';
      document.getElementById('rejectExtensionBtn').style.display = 'none';
    } else {
      document.getElementById('extensionConditions').disabled = false;
      document.getElementById('extensionGrantedUntil').disabled = false;
      document.getElementById('approveExtensionBtn').style.display = 'inline-block';
      document.getElementById('rejectExtensionBtn').style.display = 'inline-block';
    }

    const modal = new bootstrap.Modal(extensionReviewModal);
    modal.show();
  }

  // Aprobar prórroga
  document.getElementById('approveExtensionBtn')?.addEventListener('click', async () => {
    const extId = document.getElementById('reviewExtensionId').value;
    const grantedUntil = document.getElementById('extensionGrantedUntil').value;
    const conditions = document.getElementById('extensionConditions').value.trim();

    if (!grantedUntil) {
      emitFlash('warning', 'Debes especificar una fecha');
      return;
    }

    await decideExtension(extId, 'granted', grantedUntil, conditions);
  });

  // Rechazar prórroga
  document.getElementById('rejectExtensionBtn')?.addEventListener('click', async () => {
    const ok = await siiapConfirm({
      type: 'danger',
      title: 'Rechazar solicitud',
      message: '¿Estás seguro de rechazar esta solicitud?',
      confirmLabel: 'Sí, rechazar',
    });
    if (!ok) return;

    const extId = document.getElementById('reviewExtensionId').value;
    const conditions = document.getElementById('extensionConditions').value.trim() || 
                      'Solicitud rechazada';

    await decideExtension(extId, 'rejected', null, conditions);
  });

  async function decideExtension(extId, status, grantedUntil, conditions) {
    try {
      const payload = {
        status,
        condition_text: conditions
      };

      if (status === 'granted' && grantedUntil) {
        payload.granted_until = grantedUntil;
      }

      const res = await fetch(`/api/v1/extensions/requests/${extId}/decision`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf
        },
        body: JSON.stringify(payload)
      });

      const json = await res.json();

      if (!res.ok || !json.ok) {
        emitFlash('danger', json.error || 'No se pudo procesar la decisión');
        return;
      }

      emitFlash('success', json.message || 'Decisión registrada exitosamente');

      // Cerrar modal y recargar
      const modal = bootstrap.Modal.getInstance(extensionReviewModal);
      modal.hide();
      
      await loadExtensions();

    } catch (err) {
      console.error('Extension decision error:', err);
      emitFlash('danger', 'Error al procesar la decisión');
    }
  }

  // ==================== TIEMPO REAL: NUEVO DOCUMENTO RECIBIDO ====================
  window.addEventListener('siiap:submission:new', (e) => {
    const data = e.detail;
    if (!data) return;
    emitFlash('info', `Nuevo documento recibido: "${data.archive_name || 'documento'}". Recarga para verlo.`);

    // Si hay una tabla de submissions visible, recargarla automáticamente
    const submissionsTable = document.querySelector('[data-submissions-list], .submissions-list, #submissionsList');
    if (submissionsTable) {
      setTimeout(() => { window.location.reload(); }, 3000);
    }
  });

  // ==================== TIEMPO REAL: DOCUMENTO REVISADO POR OTRO ADMIN ====================
  // Se emite a role:coordinator cuando cualquier revisor decide sobre una submission.
  window.addEventListener('siiap:submission:reviewed', (e) => {
    const data = e.detail;
    if (!data) return;

    // Caso 1: estamos en la página de detalle de la misma submission
    const decisionForm = document.querySelector('[data-review-form]');
    if (decisionForm) {
      const subId = parseInt(decisionForm.dataset.subId);
      if (subId === data.submission_id) {
        emitFlash('warning', 'Otro revisor ya decidió este documento. Redirigiendo al listado...');
        setTimeout(() => {
          const next = decisionForm.dataset.nextUrl || '/admin/submissions';
          window.location.href = next;
        }, 2500);
        return;
      }
    }

    // Caso 2: estamos en la lista → reload para eliminar la fila resuelta
    const submissionsTable = document.querySelector('[data-submissions-list], .submissions-list, #submissionsList');
    if (submissionsTable) {
      const action = data.status === 'approved' ? 'aprobado' : 'rechazado';
      emitFlash('info', `Un documento fue ${action} por otro revisor. Actualizando...`);
      setTimeout(() => { window.location.reload(); }, 1500);
    }
  });
});