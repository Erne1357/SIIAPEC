/* Student Record (Expediente) page logic. */
(function () {
  'use strict';

  const CFG = window.SIIAP_RECORD || {};
  const USER_ID = CFG.userId;
  if (!USER_ID) return;

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let RECORD = null;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* Delegan en los helpers compartidos para que la salida sea idéntica a la
     de los filtros Jinja |fecha_es / |fechahora_es. */
  function fmtDate(iso, style) {
    return SIIAP.formatDateTime(iso, style || 'short', '—');
  }

  function fmtDateOnly(iso) {
    return SIIAP.formatDate(iso, 'long', '—');
  }

  function flashMsg(level, msg) {
    if (typeof showFlash === 'function') showFlash(level, msg);
  }

  // ── Spanish label maps ─────────────────────────────────────────────────
  const ROLE_LABELS = {
    applicant: 'Aspirante',
    program_admin: 'Coord. de Programa',
    postgraduate_admin: 'Admin. de Posgrado',
    social_service: 'Servicio Social',
    student: 'Estudiante',
  };
  const STATUS_LABELS = {
    // admission_status
    in_progress: 'En Proceso',
    interview_completed: 'Entrevista Completada',
    deliberation: 'En Deliberación',
    accepted: 'Aceptado',
    rejected: 'Rechazado',
    deferred: 'Diferido',
    enrolled: 'Inscrito',
    // semester enrollment
    pending: 'Pendiente',
    active: 'Activo',
    completed: 'Completado',
    on_leave: 'Baja Temporal',
    dropped: 'Baja Definitiva',
    // submission/document
    approved: 'Aprobado',
    // deferral
    used: 'Usado',
    expired: 'Expirado',
    // attendance / event
    registered: 'Inscrito',
    attended: 'Asistió',
    absent: 'Ausente',
    cancelled: 'Cancelado',
    confirmed: 'Confirmado',
    scheduled: 'Programado',
  };
  function tStatus(s) { return s ? (STATUS_LABELS[s] || s.replace(/_/g, ' ')) : '—'; }
  function tRole(r) { return r ? (ROLE_LABELS[r] || r) : '—'; }

  // ── Loaders ──────────────────────────────────────────────────────────────
  async function loadRecord() {
    try {
      const res = await fetch(`/api/v1/students/${USER_ID}/record`);
      const json = await res.json();
      if (!res.ok || json.error) {
        // El servidor responde 404 tanto si el expediente no existe como si
        // queda fuera del alcance: distinguirlos publicaba qué ids existen.
        // El front tampoco debe inventar la diferencia.
        if (res.status === 404 || res.status === 403) {
          flashMsg('danger', 'No se encontró este expediente o no tienes acceso a él.');
        }
        throw new Error(json.error?.message || 'Error');
      }
      RECORD = json.data;
      renderHeader();
      renderInfo();
      renderAcademic();
      renderDocuments();
      renderAcceptance();
      renderSemesters();
      renderInterview();
      renderEvents();
      renderDeferrals();
      renderHistory();
    } catch (e) {
      flashMsg('danger', `Error al cargar el expediente: ${e.message}`);
      const info = document.getElementById('recordPersonalInfo');
      if (info) {
        info.innerHTML = `
          <div class="empty-state empty-state--compact empty-state--error">
            <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
            <h3 class="empty-state__title">No se pudo cargar el expediente</h3>
            <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
            <p class="empty-state__error-detail">${escHtml(e.message)}</p>
            <div class="empty-state__actions">
              <button type="button" class="btn btn-outline-primary" id="recordRetryBtn">
                <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
              </button>
            </div>
          </div>`;
        document.getElementById('recordRetryBtn')?.addEventListener('click', loadRecord);
      }
    }
  }

  // ── Header ──────────────────────────────────────────────────────────────
  function renderHeader() {
    const u = RECORD.user;
    const programs = RECORD.programs || [];
    const headerName = document.getElementById('recordHeaderName');
    const headerMeta = document.getElementById('recordHeaderMeta');
    if (headerName) {
      headerName.textContent = `${u.first_name} ${u.last_name} ${u.mother_last_name || ''}`.trim();
    }
    if (headerMeta) {
      const parts = [];
      parts.push(u.email);
      if (u.control_number) parts.push(`N° Control: ${u.control_number}`);
      if (programs.length) parts.push(programs.map(p => p.program_name).join(', '));
      headerMeta.textContent = parts.join(' · ');
    }
  }

  // ── Info tab ────────────────────────────────────────────────────────────
  function renderInfo() {
    const u = RECORD.user;
    document.getElementById('recordAvatar').src = u.avatar_url + '?t=' + Date.now();
    document.getElementById('recordFullName').textContent =
      `${u.first_name} ${u.last_name} ${u.mother_last_name || ''}`.trim();
    document.getElementById('recordRole').textContent = tRole(u.role);

    const fields = [
      ['Teléfono', u.phone], ['Celular', u.mobile_phone],
      ['Correo', u.email], ['Usuario', u.username],
      ['CURP', u.curp], ['RFC', u.rfc],
      ['NSS', u.nss], ['Cédula profesional', u.cedula_profesional],
      ['Fecha de nacimiento', u.birth_date ? fmtDateOnly(u.birth_date) : null],
      ['Lugar de nacimiento', u.birth_place],
      ['Dirección', u.address],
      ['Contacto emergencia', u.emergency_contact_name],
      ['Tel. emergencia', u.emergency_contact_phone],
      ['Parentesco', u.emergency_contact_relationship],
      ['Registro', fmtDate(u.registration_date)],
      ['Último acceso', fmtDate(u.last_login)],
    ];

    const grid = `
      <div class="info-grid">
        ${fields.map(([l, v]) => `
          <div class="info-cell">
            <span class="label">${escHtml(l)}</span>
            <div class="value">${escHtml(v || '—')}</div>
          </div>`).join('')}
      </div>
      <p class="mt-3 small text-secondary mb-0">
        Estado del perfil:
        ${u.profile_completed
          ? SIIAP.statusBadge('approved', 'Completo', 'sm')
          : SIIAP.statusBadge('pending', 'Incompleto', 'sm')}
      </p>`;

    document.getElementById('recordPersonalInfo').innerHTML = grid;

    // Photo actions
    const photoActionsEl = document.getElementById('recordPhotoActions');
    let photoHtml = '';
    if (u.photo_change_requested_at && !u.photo_change_allowed) {
      photoHtml += `
        <div class="alert alert-warning small mb-2 py-2">
          <i class="bi bi-clock-history me-1" aria-hidden="true"></i>
          Solicitud pendiente desde ${escHtml(fmtDate(u.photo_change_requested_at))}.
        </div>
        <button type="button" class="btn btn-sm btn-success me-1" data-photo-action="approve">
          <i class="bi bi-check-lg me-1" aria-hidden="true"></i>Habilitar cambio
        </button>
        <button type="button" class="btn btn-sm btn-outline-danger" data-photo-action="reject">
          <i class="bi bi-x-lg me-1" aria-hidden="true"></i>Rechazar
        </button>`;
    }
    photoHtml += `
      <button type="button" class="btn btn-sm btn-outline-primary mt-1" data-photo-action="upload">
        <i class="bi bi-upload me-1" aria-hidden="true"></i>Subir foto por el estudiante
      </button>`;
    photoActionsEl.innerHTML = photoHtml;
    photoActionsEl.querySelectorAll('[data-photo-action]').forEach(btn => {
      btn.addEventListener('click', () => handlePhotoAction(btn.dataset.photoAction));
    });

    // Edit button
    const editBtn = document.getElementById('btnEditPersonalInfo');
    if (editBtn) {
      editBtn.classList.remove('d-none');
      editBtn.addEventListener('click', openEditModal, { once: true });
    }
  }

  async function handlePhotoAction(action) {
    if (action === 'upload') {
      new bootstrap.Modal(document.getElementById('coordUploadPhotoModal')).show();
      return;
    }
    const approve = action === 'approve';
    const reason = approve ? null : (prompt('Motivo del rechazo (opcional):') || null);

    try {
      const res = await fetch(`/api/v1/users/${USER_ID}/photo/enable-change`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ approve, reason }),
      });
      const json = await res.json();
      if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));
      if (res.ok && !json.error) await loadRecord();
    } catch (e) {
      flashMsg('danger', 'Error al procesar la solicitud.');
    }
  }

  // ── Edit modal ──────────────────────────────────────────────────────────
  function openEditModal() {
    const form = document.getElementById('editPersonalInfoForm');
    const u = RECORD.user;
    Array.from(form.elements).forEach(el => {
      if (el.name && Object.prototype.hasOwnProperty.call(u, el.name)) {
        el.value = u[el.name] == null ? '' : u[el.name];
      }
    });
    new bootstrap.Modal(document.getElementById('editPersonalInfoModal')).show();
  }

  document.getElementById('editPersonalInfoForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const payload = {};
    Array.from(form.elements).forEach(el => {
      if (el.name) payload[el.name] = el.value;
    });
    try {
      const res = await fetch(`/api/v1/students/${USER_ID}/personal-info`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));
      if (res.ok && !json.error) {
        bootstrap.Modal.getInstance(document.getElementById('editPersonalInfoModal'))?.hide();
        await loadRecord();
      }
    } catch (err) {
      flashMsg('danger', 'Error al guardar cambios.');
    }
  });

  document.getElementById('coordUploadPhotoForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await fetch(`/api/v1/users/${USER_ID}/photo`, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: fd,
      });
      const json = await res.json();
      if (json.flash) json.flash.forEach(f => flashMsg(f.level, f.message));
      if (res.ok && !json.error) {
        bootstrap.Modal.getInstance(document.getElementById('coordUploadPhotoModal'))?.hide();
        e.target.reset();
        await loadRecord();
      }
    } catch (err) {
      flashMsg('danger', 'Error de red al subir foto.');
    }
  });

  // ── Other tabs ──────────────────────────────────────────────────────────
  function renderAcademic() {
    const programs = RECORD.programs || [];
    const el = document.getElementById('recordAcademic');
    if (!programs.length) {
      el.innerHTML = `<div class="card-body"><div class="empty-state empty-state--compact">
        <div class="empty-state__icon"><i class="bi bi-mortarboard" aria-hidden="true"></i></div>
        <h3 class="empty-state__title">Sin programa académico</h3>
        <p class="empty-state__description">Este usuario todavía no está vinculado a ningún programa.</p>
      </div></div>`;
      return;
    }
    el.innerHTML = `<div class="card-body p-0"><div class="siiap-table-wrapper">
      <table class="table siiap-table align-middle mb-0">
        <caption class="visually-hidden">Programas académicos del estudiante y su estado de admisión</caption>
        <thead class="table-light">
          <tr>
            <th scope="col">Programa</th>
            <th scope="col">Estado</th>
            <th scope="col">Periodo de admisión</th>
            <th scope="col">Semestre</th>
            <th scope="col">SECIHTI</th>
            <th scope="col">Inscripción</th>
          </tr>
        </thead>
        <tbody>
          ${programs.map(p => `
            <tr>
              <td>${escHtml(p.program_name || '—')}</td>
              <td>${statusBadge(p.admission_status)}</td>
              <td>${escHtml(p.admission_period_name || '—')}</td>
              <td>${escHtml(p.current_semester || '—')}</td>
              <td>${p.has_conacyt_scholarship
                    ? SIIAP.statusBadge('approved', 'Sí', 'sm')
                    : SIIAP.statusBadge('pending', 'No', 'sm')}</td>
              <td>${escHtml(fmtDateOnly(p.enrollment_date))}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div></div>`;
  }

  /**
   * Chip de estado. Antes emitía `status-badge--in_progress` (guion BAJO), un
   * modificador que no existe en el componente: el chip salía sin color.
   * SIIAP.statusBadge normaliza la clave y añade el icono.
   */
  const STATUS_KEY_ALIAS = {
    active: 'in_progress',
    completed: 'approved',
    on_leave: 'deferred',
    dropped: 'rejected',
    used: 'approved',
    expired: 'deferred',
    registered: 'enrolled',
    attended: 'approved',
    absent: 'rejected',
    cancelled: 'rejected',
    confirmed: 'approved',
    scheduled: 'in_progress',
  };

  function statusBadge(status) {
    if (!status) return SIIAP.statusBadge('pending', '—', 'sm');
    const key = STATUS_KEY_ALIAS[status] || status;
    return SIIAP.statusBadge(key, tStatus(status), 'sm');
  }

  function docList(docs) {
    if (!docs || !docs.length) return `<p class="text-secondary small px-3 py-2 mb-0">Sin documentos.</p>`;
    return `<ul class="list-group list-group-flush">${docs.map(d => `
      <li class="list-group-item d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div>
          <div class="fw-medium">${escHtml(d.archive_name || 'Documento')}</div>
          <small class="text-secondary">
            ${d.upload_date ? escHtml(fmtDate(d.upload_date)) : ''}
            ${d.semester ? ` · Semestre ${escHtml(d.semester)}` : ''}
          </small>
        </div>
        <div class="d-flex align-items-center gap-2">
          ${statusBadge(d.status)}
          ${d.file_url ? `<a href="${escHtml(d.file_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary tap-target" title="Ver documento" aria-label="Ver ${escHtml(d.archive_name || 'documento')} (se abre en una pestaña nueva)"><i class="bi bi-eye" aria-hidden="true"></i></a>` : ''}
        </div>
      </li>`).join('')}</ul>`;
  }

  function renderDocuments() {
    const grouped = RECORD.documents_by_phase || {};
    const adm = grouped.admission || [];
    const con = grouped.conclusion || [];
    const perm = grouped.permanence || {};
    const semKeys = Object.keys(perm).sort((a, b) => parseInt(a) - parseInt(b));
    const permCount = semKeys.reduce((acc, k) => acc + (perm[k]?.length || 0), 0);

    const acc = (id, title, count, body) => `
      <div class="accordion-item">
        <h2 class="accordion-header">
          <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse"
                  data-bs-target="#dh-${id}" aria-expanded="false" aria-controls="dh-${id}">
            <i class="bi bi-folder2-open me-2" aria-hidden="true"></i>${escHtml(title)}
            <span class="badge bg-secondary ms-2">${count}</span>
            <span class="visually-hidden">documentos</span>
          </button>
        </h2>
        <div id="dh-${id}" class="accordion-collapse collapse" data-bs-parent="#docsHistAcc">
          <div class="accordion-body p-0">${body}</div>
        </div>
      </div>`;

    const permBody = semKeys.map(s => `
      <div class="border-bottom">
        <h4 class="doc-group-title">Semestre ${escHtml(s)} · ${perm[s].length} documento(s)</h4>
        ${docList(perm[s])}
      </div>`).join('') || `<p class="text-secondary small px-3 py-2 mb-0">Sin documentos.</p>`;

    document.getElementById('recordDocuments').innerHTML = `
      <div class="accordion" id="docsHistAcc">
        ${acc('adm', 'Admisión', adm.length, docList(adm))}
        ${acc('perm', 'Permanencia', permCount, permBody)}
        ${acc('con', 'Conclusión', con.length, docList(con))}
      </div>`;
  }

  function renderAcceptance() {
    const docs = RECORD.acceptance_documents || [];
    if (!docs.length) {
      document.getElementById('recordAcceptance').innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-file-earmark-check" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin documentos de aceptación</h3>
          <p class="empty-state__description">Aún no se ha registrado ningún documento de la fase de aceptación.</p>
        </div>`;
      return;
    }
    const labels = {
      acceptance_letter: 'Carta de Aceptación',
      course_schedule: 'Tira de Materias',
      enrollment_receipt: 'Boleta de Servicios Escolares',
      acceptance_opinion: 'Dictamen de Aceptación',
    };
    document.getElementById('recordAcceptance').innerHTML = `
      <div class="siiap-table-wrapper">
        <table class="table siiap-table align-middle mb-0">
          <caption class="visually-hidden">Documentos de la fase de aceptación</caption>
          <thead class="table-light">
            <tr>
              <th scope="col">Tipo</th>
              <th scope="col">Estado</th>
              <th scope="col">Subido</th>
              <th scope="col">Acciones</th>
            </tr>
          </thead>
          <tbody>
            ${docs.map(d => `
              <tr>
                <td>${escHtml(labels[d.document_type] || d.document_type)}</td>
                <td>${statusBadge(d.status)}</td>
                <td>${escHtml(fmtDate(d.uploaded_at))}</td>
                <td>${d.file_url ? `<a href="${escHtml(d.file_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary"><i class="bi bi-eye me-1" aria-hidden="true"></i>Ver<span class="visually-hidden"> (se abre en una pestaña nueva)</span></a>` : '—'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function renderSemesters() {
    const items = RECORD.semester_enrollments || [];
    if (!items.length) {
      document.getElementById('recordSemesters').innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-list-ol" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin inscripciones registradas</h3>
          <p class="empty-state__description">El estudiante todavía no tiene semestres inscritos.</p>
        </div>`;
      return;
    }
    document.getElementById('recordSemesters').innerHTML = `
      <div class="siiap-table-wrapper">
        <table class="table siiap-table align-middle mb-0">
          <caption class="visually-hidden">Semestres inscritos por el estudiante</caption>
          <thead class="table-light">
            <tr>
              <th scope="col">Semestre</th>
              <th scope="col">Periodo</th>
              <th scope="col">Estado</th>
              <th scope="col">Confirmado</th>
              <th scope="col">Confirmado el</th>
              <th scope="col">Comprobante</th>
              <th scope="col">Horario</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(s => `
              <tr>
                <td><span class="badge bg-info">${escHtml(s.semester_number)}</span></td>
                <td>${escHtml(s.academic_period_name || '—')}</td>
                <td>${statusBadge(s.status)}</td>
                <td>${s.enrollment_confirmed
                      ? '<i class="bi bi-check-circle-fill text-success-strong" aria-hidden="true"></i><span class="visually-hidden">Confirmado</span>'
                      : '<i class="bi bi-x-circle text-secondary" aria-hidden="true"></i><span class="visually-hidden">Sin confirmar</span>'}</td>
                <td>${escHtml(fmtDate(s.confirmed_at))}</td>
                <td>${s.payment_proof_url ? `<a href="${escHtml(s.payment_proof_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary tap-target" title="Ver comprobante de pago" aria-label="Ver el comprobante de pago del semestre ${escHtml(s.semester_number)} (se abre en una pestaña nueva)"><i class="bi bi-file-earmark-pdf" aria-hidden="true"></i></a>` : '—'}</td>
                <td>${s.schedule_url ? `<a href="${escHtml(s.schedule_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-success tap-target" title="Ver horario" aria-label="Ver el horario del semestre ${escHtml(s.semester_number)} (se abre en una pestaña nueva)"><i class="bi bi-calendar2-week" aria-hidden="true"></i></a>` : '—'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function renderInterview() {
    const i = RECORD.interview;
    const el = document.getElementById('recordInterview');
    if (!i) {
      el.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-chat-dots" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin entrevista registrada</h3>
          <p class="empty-state__description">No se ha agendado ninguna entrevista para este aspirante.</p>
        </div>`;
      return;
    }
    el.innerHTML = `
      <h3 class="h5 mb-2">${escHtml(i.event_title || 'Entrevista')}</h3>
      <p class="mb-1"><strong>Estado:</strong> ${statusBadge(i.status)}</p>
      <p class="mb-1"><strong>Fecha:</strong> ${escHtml(fmtDate(i.event_date, 'long'))}</p>
      ${i.interviewer ? `<p class="mb-1"><strong>Entrevistador:</strong> ${escHtml(i.interviewer.name)} <small class="text-secondary">(${escHtml(i.interviewer.email)})</small></p>` : ''}
      ${i.notes ? `<div class="alert alert-light small mt-3 mb-0"><strong>Notas:</strong><br>${escHtml(i.notes)}</div>` : ''}`;
  }

  function renderEvents() {
    const past = RECORD.events_attended || [];
    const upcoming = RECORD.upcoming_events || [];

    const renderList = (items, empty) => {
      if (!items.length) return `<p class="text-secondary small mb-0">${empty}</p>`;
      return `<ul class="list-group list-group-flush">${items.map(ev => `
        <li class="list-group-item">
          <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
            <div>
              <div class="fw-medium">${escHtml(ev.title)}</div>
              <small class="text-secondary">${escHtml(fmtDate(ev.event_date, 'long'))}</small>
            </div>
            ${statusBadge(ev.attendance_status || ev.status)}
          </div>
        </li>`).join('')}</ul>`;
    };

    document.getElementById('recordEvents').innerHTML = `
      <h3 class="h6 mb-2">Próximos e inscritos</h3>
      ${renderList(upcoming, 'Sin eventos próximos.')}
      <hr>
      <h3 class="h6 mb-2">Histórico</h3>
      ${renderList(past, 'Sin participación previa.')}`;
  }

  function renderDeferrals() {
    const items = RECORD.deferrals || [];
    if (!items.length) {
      document.getElementById('recordDeferrals').innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-pause-circle" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin diferimientos</h3>
          <p class="empty-state__description">Este expediente no registra solicitudes de diferimiento.</p>
        </div>`;
      return;
    }
    document.getElementById('recordDeferrals').innerHTML = `
      <div class="siiap-table-wrapper">
        <table class="table siiap-table align-middle mb-0">
          <caption class="visually-hidden">Solicitudes de diferimiento del estudiante</caption>
          <thead class="table-light">
            <tr>
              <th scope="col">N.º</th>
              <th scope="col">Estado</th>
              <th scope="col">Solicitado por</th>
              <th scope="col">Periodo origen</th>
              <th scope="col">Periodo destino</th>
              <th scope="col">Creado</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(d => `
              <tr>
                <td>${escHtml(d.deferral_number)}</td>
                <td>${statusBadge(d.status)}</td>
                <td>${escHtml(d.requested_by)}</td>
                <td>${escHtml(d.original_period_name || '—')}</td>
                <td>${escHtml(d.deferred_to_period_name || '—')}</td>
                <td>${escHtml(fmtDate(d.created_at))}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function renderHistory() {
    const items = RECORD.history || [];
    if (!items.length) {
      document.getElementById('recordHistory').innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin historial</h3>
          <p class="empty-state__description">Todavía no se registran acciones sobre este expediente.</p>
        </div>`;
      return;
    }
    document.getElementById('recordHistory').innerHTML = `
      <ul class="list-group list-group-flush">
        ${items.map(h => `
          <li class="list-group-item">
            <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
              <div>
                <div class="fw-medium">${escHtml(h.action_label || h.action)}</div>
                <small class="text-secondary">${escHtml(h.details || '')}</small>
              </div>
              <small class="text-secondary">${escHtml(fmtDate(h.timestamp, 'short'))}<br>${escHtml(h.admin_name || 'Sistema')}</small>
            </div>
          </li>`).join('')}
      </ul>`;
  }

  function setupBackLink() {
    const link = document.getElementById('recordBackLink');
    if (!link) return;
    // If there is real navigation history within same origin, prefer history.back().
    // Otherwise leave the href as the dashboard fallback (works on new tabs / direct links).
    const ref = document.referrer || '';
    const sameOrigin = ref && ref.startsWith(window.location.origin);
    const recordUrl = window.location.pathname;
    const refPath = sameOrigin ? new URL(ref).pathname : '';
    if (sameOrigin && refPath && refPath !== recordUrl && window.history.length > 1) {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        window.history.back();
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupBackLink();
    loadRecord();
  });
})();
