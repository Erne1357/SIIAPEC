// app/static/js/coordinator/permanence.js
// Fase 6 + Iteración 3: Permanencia Semestral + Seguimiento Documental

'use strict';

class PermanenceManager {
  constructor(programId, activePeriodId) {
    this.programId = programId || null; // null = "Todos los programas"
    this.activePeriodId = activePeriodId;
    this.students = [];
    this.csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    this.init();
  }

  init() {
    this.bindEvents();
    this.applyAllModeUi();
    this.loadStudents();
    this.loadStats();
    this.bindTabEvents();
    // Tab "Inscripción" es la activa por default → carga inicial
    this.loadEnrollmentTab();
  }

  // ── Helpers para modo "Todos los programas" ─────────────────────────────
  _isAllMode() { return !this.programId; }

  _targetProgramIds() {
    if (this.programId) return [parseInt(this.programId)];
    return (window.COORDINATOR_PROGRAMS || []).map(p => p.id);
  }

  _programName(pid) {
    const p = (window.COORDINATOR_PROGRAMS || []).find(x => x.id === pid);
    return p ? p.name : '—';
  }

  /**
   * Celda «Programa» del modo «Todos los programas», que es el valor por
   * defecto del selector. La columna se acota por CSS (.program-col): con
   * ocho columnas los anchos preferidos suman ~1369px contra los 1138px
   * reales del portátil, así que nombres y correos envolvían a dos líneas y
   * la fila crecía de 61 a 85px. El nombre completo queda en el title.
   */
  _programCell(name) {
    const value = this.escapeHtml(name || '');
    return this._isAllMode()
      ? `<td class="program-col text-muted small" title="${value}">${value}</td>`
      : '';
  }

  async _fanFetch(urlBuilder) {
    const ids = this._targetProgramIds();
    return Promise.all(ids.map(async (pid) => {
      try {
        const res = await fetch(urlBuilder(pid));
        const json = await res.json();
        if (!res.ok || json.error) return { pid, data: null, meta: null };
        return { pid, data: json.data, meta: json.meta };
      } catch (e) {
        return { pid, data: null, meta: null };
      }
    }));
  }

  _toggleProgramHeader(tableId) {
    const thead = document.querySelector(`#${tableId} thead tr`);
    if (!thead) return;
    const existing = thead.querySelector('th.program-col');
    const want = this._isAllMode();
    if (want && !existing) {
      const th = document.createElement('th');
      th.className = 'program-col';
      th.scope = 'col';
      th.textContent = 'Programa';
      thead.insertBefore(th, thead.firstChild);
    } else if (!want && existing) {
      existing.remove();
    }
  }

  /**
   * Habilita/deshabilita botones que requieren un programa específico
   * y agrega tooltip explicativo en modo "Todos".
   */
  applyAllModeUi() {
    const tooltip = 'Selecciona un programa específico para usar esta acción';
    const ids = ['btnNewDeadline', 'btnNewDeadlineEmpty', 'btnConacytMonthly'];
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      if (this._isAllMode()) {
        el.disabled = true;
        el.title = tooltip;
        el.classList.add('disabled');
      } else {
        el.disabled = false;
        el.title = '';
        el.classList.remove('disabled');
      }
    });
  }

  /** Chip de estado con el componente compartido .status-badge. */
  static statusBadge(key, label) {
    return SIIAP.statusBadge(key, label, 'sm');
  }

  /** Chip del estado de una inscripción semestral. */
  static semesterStatusBadge(status) {
    const map = {
      active:    ['in_progress', 'Activo'],
      pending:   ['pending',     'Pendiente'],
      completed: ['approved',    'Completado'],
      on_leave:  ['deferred',    'Baja temporal'],
      dropped:   ['rejected',    'Baja definitiva'],
    };
    const [key, label] = map[status] || ['pending', SIIAP.statusLabel(status)];
    return SIIAP.statusBadge(key, label, 'sm');
  }

  /** Estado vacío dentro de una tabla, con el componente completo. */
  static emptyRow(colspan, icon, title, description) {
    return `
      <tr>
        <td colspan="${colspan}">
          <div class="empty-state empty-state--inline">
            <div class="empty-state__icon"><i class="bi bi-${icon}" aria-hidden="true"></i></div>
            <h3 class="empty-state__title">${title}</h3>
            <p class="empty-state__description">${description}</p>
          </div>
        </td>
      </tr>`;
  }

  bindEvents() {
    // Selector de programa
    const progSel = document.getElementById('programSelector');
    if (progSel) {
      progSel.addEventListener('change', () => {
        const v = progSel.value;
        window.location.href = v
          ? `/coordinator/permanence/${v}`
          : `/coordinator/permanence`;
      });
    }

    // Búsqueda en tabla de estudiantes
    document.getElementById('searchInput')?.addEventListener('input', e => {
      this.filterTable(e.target.value);
    });

    // Confirmar inscripción
    document.getElementById('confirmEnrollBtn')?.addEventListener('click', () => {
      this.submitConfirmEnrollment();
    });

    // Actualizar estado semestral
    document.getElementById('confirmUpdateStatusBtn')?.addEventListener('click', () => {
      this.submitUpdateStatus();
    });

    // Botones nueva ventana
    document.getElementById('btnNewDeadline')?.addEventListener('click', () => {
      this.showCreateDeadlineModal();
    });
    document.getElementById('btnNewDeadlineEmpty')?.addEventListener('click', () => {
      this.showCreateDeadlineModal();
    });

    // Crear ventanas mensuales SECIHTI
    document.getElementById('btnConacytMonthly')?.addEventListener('click', () => {
      this.createConacytMonthlyDeadlines();
    });

    // Auto-label al seleccionar archive en modal nueva ventana
    document.getElementById('deadlineArchiveId')?.addEventListener('change', e => {
      const opt = e.target.selectedOptions[0];
      const labelInput = document.getElementById('deadlineLabel');
      if (opt && opt.dataset.name && !labelInput.value.trim()) {
        labelInput.value = opt.dataset.name;
      }
    });

    // Guardar nueva ventana
    document.getElementById('btnSaveDeadline')?.addEventListener('click', () => {
      this.submitCreateDeadline();
    });

    // Revisar documento: aprobar / rechazar
    document.getElementById('btnApproveDoc')?.addEventListener('click', () => {
      this.submitReview('approved');
    });
    document.getElementById('btnRejectDoc')?.addEventListener('click', () => {
      this.submitReview('rejected');
    });

    // Aprobar / Rechazar baja temporal
    document.getElementById('btnApproveLeave')?.addEventListener('click', () => {
      this.submitLeaveDecision(true);
    });
    document.getElementById('btnRejectLeave')?.addEventListener('click', () => {
      this.submitLeaveDecision(false);
    });

    // Botón refrescar solicitudes
    document.getElementById('btnRefreshLeave')?.addEventListener('click', () => {
      this.loadLeaveRequestsTab();
    });
  }

  bindTabEvents() {
    document.getElementById('tab-enrollment')?.addEventListener('shown.bs.tab', () => {
      this.loadEnrollmentTab();
    });
    document.getElementById('tab-leave')?.addEventListener('shown.bs.tab', () => {
      this.loadLeaveRequestsTab();
    });
    document.getElementById('tab-documents')?.addEventListener('shown.bs.tab', () => {
      this.loadDocumentsTabData();
    });
  }

  // ── Carga de datos ─────────────────────────────────────────────────────────

  async loadStudents() {
    this.showLoading(true);
    try {
      const results = await this._fanFetch(pid => `/api/v1/permanence/program/${pid}/students`);
      const students = [];
      results.forEach(r => {
        if (!Array.isArray(r.data)) return;
        const programName = this._programName(r.pid);
        r.data.forEach(s => { s.__program_name = programName; students.push(s); });
      });
      this.students = students;
      this.renderTable(this.students);
    } catch (e) {
      showFlash('danger', `Error al cargar estudiantes: ${e.message}`);
    } finally {
      this.showLoading(false);
    }
  }

  async loadStats() {
    try {
      const results = await this._fanFetch(pid => `/api/v1/permanence/program/${pid}/stats`);
      // Sumar campos numéricos de cada respuesta
      const merged = {};
      results.forEach(r => {
        if (!r.data || typeof r.data !== 'object') return;
        Object.keys(r.data).forEach(k => {
          const v = r.data[k];
          if (typeof v === 'number') merged[k] = (merged[k] || 0) + v;
        });
      });
      this.renderStats(merged);
    } catch (_) { /* silencioso */ }
  }

  async loadDocumentsTabData() {
    const loading = document.getElementById('deadlinesLoading');
    const empty = document.getElementById('deadlinesEmpty');
    const list = document.getElementById('deadlinesList');
    loading?.classList.remove('d-none');
    empty?.classList.add('d-none');
    if (list) list.innerHTML = '';

    const showArchived = document.getElementById('toggleShowArchivedDeadlines')?.checked ? 'true' : 'false';

    try {
      const [dlResults, pdResults] = await Promise.all([
        this._fanFetch(pid => `/api/v1/permanence/program/${pid}/deadlines?include_archived=${showArchived}`),
        this._fanFetch(pid => `/api/v1/permanence/program/${pid}/pending-documents`),
      ]);
      loading?.classList.add('d-none');

      const deadlines = [];
      dlResults.forEach(r => {
        if (!Array.isArray(r.data)) return;
        const programName = this._programName(r.pid);
        r.data.forEach(d => { d.__program_name = programName; deadlines.push(d); });
      });
      const pendingDocs = [];
      pdResults.forEach(r => {
        if (!Array.isArray(r.data)) return;
        pendingDocs.push(...r.data);
      });

      // Agrupar pending por deadline_id
      const pendingByDeadline = {};
      for (const doc of pendingDocs) {
        const dlId = doc.submission.document_deadline_id;
        if (!pendingByDeadline[dlId]) pendingByDeadline[dlId] = [];
        pendingByDeadline[dlId].push(doc);
      }

      const badge = document.getElementById('deadlinesBadge');
      if (badge) {
        badge.textContent = deadlines.length;
        badge.classList.toggle('d-none', !deadlines.length);
      }

      if (!deadlines.length) {
        empty?.classList.remove('d-none');
        this._deadlinesCache = [];
        return;
      }
      this._deadlinesCache = deadlines;
      list.innerHTML = deadlines
        .map(dl => this.renderDeadlineCard(dl, pendingByDeadline[dl.id] || []))
        .join('');
    } catch (e) {
      loading?.classList.add('d-none');
      showFlash('danger', `Error al cargar ventanas: ${e.message}`);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  renderStats(stats) {
    const container = document.getElementById('statsContainer');
    if (!container) return;
    // Tarjetas de indicadores (componente compartido .stat-card). La tarjeta
    // sin modificador ES el tono neutro; el color nunca va solo: siempre
    // acompañado de icono y etiqueta.
    const items = [
      { label: 'Total de estudiantes', value: stats.total_students, icon: 'people-fill',       tone: '' },
      { label: 'Confirmados',          value: stats.confirmed,      icon: 'check-circle-fill', tone: 'success' },
      { label: 'Pendientes',           value: stats.pending,        icon: 'hourglass-split',   tone: 'warning' },
      { label: 'Baja temporal',        value: stats.on_leave,       icon: 'pause-circle-fill', tone: 'info' },
    ];
    container.innerHTML = items.map(i => `
      <div class="stat-card${i.tone ? ' stat-card--' + i.tone : ''}">
        <i class="bi bi-${i.icon} stat-card__icon" aria-hidden="true"></i>
        <p class="stat-card__value">${i.value || 0}</p>
        <p class="stat-card__label">${i.label}</p>
      </div>
    `).join('');
  }

  renderTable(students) {
    const tbody = document.getElementById('studentsTableBody');
    const empty = document.getElementById('emptyState');
    const table = document.getElementById('tableContainer');

    // Encabezado condicional "Programa"
    const headerRow = document.getElementById('studentsTableHeaderRow');
    if (headerRow) {
      const existing = headerRow.querySelector('th.program-col');
      if (this._isAllMode() && !existing) {
        const th = document.createElement('th');
        th.className = 'program-col';
        th.scope = 'col';
        th.textContent = 'Programa';
        headerRow.insertBefore(th, headerRow.firstChild);
      } else if (!this._isAllMode() && existing) {
        existing.remove();
      }
    }

    if (!students.length) {
      empty.classList.remove('d-none');
      table.classList.add('d-none');
      return;
    }
    empty.classList.add('d-none');
    table.classList.remove('d-none');

    tbody.innerHTML = students.map((s, i) => this.renderStudentRow(s, i)).join('');
  }

  renderStudentRow(s, index) {
    const up = s.user_program;
    const user = s.user;
    const ce = s.current_enrollment;

    const semesterBadge = `<span class="badge bg-info">${up.current_semester || 1}</span><span class="visually-hidden"> semestre</span>`;

    let periodCell = '<span class="text-secondary small">—</span>';
    if (s.current_period) {
      periodCell = `<span class="small">${s.current_period.name}</span>`;
    }

    let statusCell = '';
    let actionCell = '';

    const safeFullName = this.escapeHtml(user.full_name);

    if (!this.activePeriodId) {
      statusCell = PermanenceManager.statusBadge('pending', 'Sin periodo activo');
      actionCell = `
        <button type="button" class="btn btn-sm btn-outline-info tap-target"
          onclick="permanenceManager.showHistoryByIndex(${index})"
          title="Ver historial" aria-label="Ver historial de ${safeFullName}">
          <i class="bi bi-clock-history" aria-hidden="true"></i>
        </button>`;
    } else if (!ce) {
      statusCell = PermanenceManager.statusBadge('pending', 'Pendiente de confirmación');
      actionCell = `
        <a href="#pane-enrollment" class="btn btn-sm btn-outline-warning" data-bs-toggle="tab" data-bs-target="#pane-enrollment"
          onclick="document.getElementById('tab-enrollment').click()" title="Confirmar en la pestaña Inscripción">
          <i class="bi bi-arrow-right-short" aria-hidden="true"></i>Inscripción
        </a>`;
    } else {
      statusCell = PermanenceManager.semesterStatusBadge(ce.status);
      actionCell = `
        <div class="d-flex gap-1 justify-content-center">
          <button type="button" class="btn btn-sm btn-outline-secondary tap-target"
            onclick="permanenceManager.showUpdateStatusModal(${ce.id}, '${safeFullName}', '${ce.status}')"
            title="Cambiar estado" aria-label="Cambiar el estado de ${safeFullName}">
            <i class="bi bi-pencil" aria-hidden="true"></i>
          </button>
          <button type="button" class="btn btn-sm btn-outline-info tap-target"
            onclick="permanenceManager.showHistoryByIndex(${index})"
            title="Historial" aria-label="Ver historial de ${safeFullName}">
            <i class="bi bi-clock-history" aria-hidden="true"></i>
          </button>
        </div>`;
    }

    // Columna SECIHTI
    const conacytBadge = up.has_conacyt_scholarship
      ? PermanenceManager.statusBadge('approved', 'Becario')
      : PermanenceManager.statusBadge('pending', 'Sin beca');
    const conacytToggle = `
      <button type="button" class="btn btn-sm ${up.has_conacyt_scholarship ? 'btn-outline-success' : 'btn-outline-secondary'} ms-1 tap-target"
        title="${up.has_conacyt_scholarship ? 'Quitar beca SECIHTI' : 'Marcar como becario SECIHTI'}"
        aria-label="${up.has_conacyt_scholarship ? 'Quitar la beca SECIHTI a' : 'Marcar como becario SECIHTI a'} ${safeFullName}"
        onclick="permanenceManager.toggleConacyt(${up.id}, ${!up.has_conacyt_scholarship})">
        <i class="bi bi-toggles" aria-hidden="true"></i>
      </button>`;

    const programCell = this._programCell(s.__program_name);

    return `
      <tr>
        ${programCell}
        <td>
          <div class="fw-semibold">${this.escapeHtml(user.full_name)}</div>
          <div class="small text-muted">${this.escapeHtml(user.email)}</div>
        </td>
        <td class="text-center">
          <span class="badge bg-secondary font-monospace">${user.control_number || '—'}</span>
        </td>
        <td class="text-center">${semesterBadge}</td>
        <td class="text-center">${periodCell}</td>
        <td class="text-center">${statusCell}</td>
        <td class="text-center">${conacytBadge}${conacytToggle}</td>
        <td class="text-center">${actionCell}</td>
      </tr>`;
  }

  renderDeadlineCard(dl, pendingDocs = []) {
    const statusBadge = dl.is_currently_open
      ? PermanenceManager.statusBadge('approved', 'Abierta')
      : PermanenceManager.statusBadge('pending', 'Cerrada');

    const closesAt = dl.closes_at
      ? `Cierra: ${SIIAP.formatDate(dl.closes_at, 'short', '—')}`
      : 'Sin fecha límite';

    const opensAt = dl.opens_at
      ? `Abre: ${SIIAP.formatDate(dl.opens_at, 'short', '—')}`
      : '';

    const toggleIcon = dl.is_open ? 'bi-lock-fill' : 'bi-unlock-fill';
    const toggleTitle = dl.is_open ? 'Cerrar ventana' : 'Abrir ventana';
    const toggleCls = dl.is_open ? 'btn-outline-secondary' : 'btn-outline-success';

    // Sólo se muestra un badge con el conteo y un link al panel central de
    // revisión de documentos. La aprobación/rechazo ya no ocurre aquí; se
    // hace en /admin/review/submissions?phase=permanence con todos los
    // metadatos (programa, ventana, semestre, periodo, historial).
    const pendingSection = pendingDocs.length ? `
      <div class="border-top mt-2 pt-2 d-flex align-items-center gap-2 flex-wrap">
        ${PermanenceManager.statusBadge('review', `${pendingDocs.length} pendiente(s) de revisión`)}
        <a href="/admin/review/submissions?phase=permanence&status=review"
           class="btn btn-sm btn-outline-warning" target="_blank" rel="noopener">
          <i class="bi bi-clipboard2-check me-1" aria-hidden="true"></i>Ir a revisión
          <span class="visually-hidden">(se abre en una pestaña nueva)</span>
        </a>
        <span class="small text-secondary">La revisión se centraliza en el panel de Documentos.</span>
      </div>` : '';

    return `
      <div class="card mb-2">
        <div class="card-body py-2 px-3">
          <div class="d-flex align-items-center gap-2 flex-wrap">
            <div class="flex-grow-1">
              <div class="fw-semibold">${this.escapeHtml(dl.label)}</div>
              <div class="small text-muted">
                ${this.escapeHtml(dl.archive_name || '')}
                &nbsp;·&nbsp; Secuencia: ${dl.sequence}
                ${opensAt ? '&nbsp;·&nbsp; ' + opensAt : ''}
                &nbsp;·&nbsp; ${closesAt}
              </div>
            </div>
            <div class="d-flex align-items-center gap-2">
              ${statusBadge}
              <span class="badge bg-secondary">
                ${dl.stats.total} entregas${dl.stats.approved ? ` · ${dl.stats.approved} aprobadas` : ''}
              </span>
              ${dl.is_archived ? `
                ${PermanenceManager.statusBadge('deferred', 'Archivada')}
                <button type="button" class="btn btn-sm btn-outline-success tap-target" title="Restaurar ventana"
                  aria-label="Restaurar la ventana ${this.escapeHtml(dl.label)}"
                  onclick="permanenceManager.restoreDeadline(${dl.id})">
                  <i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i>
                </button>
              ` : `
                <button type="button" class="btn btn-sm btn-outline-primary tap-target" title="Editar ventana"
                  aria-label="Editar la ventana ${this.escapeHtml(dl.label)}"
                  onclick="permanenceManager.openEditDeadlineModal(${dl.id})">
                  <i class="bi bi-pencil" aria-hidden="true"></i>
                </button>
                <button type="button" class="btn btn-sm ${toggleCls} tap-target" title="${toggleTitle}"
                  aria-label="${toggleTitle}: ${this.escapeHtml(dl.label)}"
                  onclick="permanenceManager.toggleDeadline(${dl.id}, ${!dl.is_open})">
                  <i class="bi ${toggleIcon}" aria-hidden="true"></i>
                </button>
                <button type="button" class="btn btn-sm btn-outline-warning tap-target" title="Archivar ventana"
                  aria-label="Archivar la ventana ${this.escapeHtml(dl.label)}"
                  onclick="permanenceManager.archiveDeadline(${dl.id}, '${this.escapeHtml(dl.label)}')">
                  <i class="bi bi-archive" aria-hidden="true"></i>
                </button>
              `}
            </div>
          </div>
          ${pendingSection}
        </div>
      </div>`;
  }

  // ── Acciones: Estudiantes ───────────────────────────────────────────────────

  /**
   * Abre el modal en uno de tres modos: 'confirm', 'advance', 'reinstate'.
   * El submit selecciona el endpoint correcto según el mode.
   */
  showConfirmModal(userProgramId, studentName, mode = 'confirm', proofUrlArg = '') {
    const TITLES = {
      confirm: ['Confirmar inscripción semestral', 'Esto confirmará la inscripción del estudiante en el periodo activo.', 'Confirmar inscripción', 'btn-success'],
      advance: ['Avanzar semestre manualmente', 'Avanzará al estudiante al siguiente semestre en el periodo activo aunque tenga rezagos. El semestre anterior se cerrará como completado en la misma operación.', 'Avanzar semestre', 'btn-primary'],
      reinstate: ['Reincorporar estudiante', 'Crea un nuevo semestre activo en el periodo actual saliendo de la baja temporal.', 'Reincorporar', 'btn-warning'],
    };
    const [title, desc, btnLabel, btnCls] = TITLES[mode] || TITLES.confirm;
    document.getElementById('confirmEnrollTitle').innerHTML =
      `<i class="bi bi-check-circle-fill text-success-strong me-2" aria-hidden="true"></i>${title}`;
    document.getElementById('confirmEnrollDescription').textContent = desc;
    document.getElementById('confirmEnrollBtnLabel').textContent = btnLabel;

    const btn = document.getElementById('confirmEnrollBtn');
    btn.classList.remove('btn-success', 'btn-primary', 'btn-warning');
    btn.classList.add(btnCls);

    document.getElementById('confirmEnrollStudentName').textContent = studentName;
    document.getElementById('confirmEnrollUserProgramId').value = userProgramId;
    document.getElementById('confirmEnrollMode').value = mode;
    document.getElementById('confirmEnrollNotes').value = '';
    document.getElementById('confirmEnrollFile').value = '';
    const scheduleInput = document.getElementById('confirmEnrollSchedule');
    if (scheduleInput) scheduleInput.value = '';

    // El render de la fila pasa el URL del comprobante (string vacío si no hay).
    // Sólo aplica al modo 'confirm'. Decodificar el escape '%27' → "'".
    const wrap = document.getElementById('confirmEnrollExistingProofWrap');
    const link = document.getElementById('confirmEnrollExistingProofLink');
    let proofUrl = '';
    if (mode === 'confirm' && proofUrlArg) {
      proofUrl = String(proofUrlArg).replace(/%27/g, "'");
    }
    if (proofUrl) {
      link.href = proofUrl;
      link.classList.remove('disabled');
      link.setAttribute('aria-disabled', 'false');
      wrap.classList.remove('d-none');
    } else {
      wrap.classList.add('d-none');
      link.href = '#';
      link.classList.add('disabled');
      link.setAttribute('aria-disabled', 'true');
    }

    new bootstrap.Modal(document.getElementById('confirmEnrollmentModal')).show();
  }

  async submitConfirmEnrollment() {
    const upId = parseInt(document.getElementById('confirmEnrollUserProgramId').value);
    const mode = document.getElementById('confirmEnrollMode').value || 'confirm';
    const notes = document.getElementById('confirmEnrollNotes').value.trim();
    const file = document.getElementById('confirmEnrollFile').files[0] || null;
    const schedule = document.getElementById('confirmEnrollSchedule')?.files[0] || null;

    if (!this.activePeriodId) {
      showFlash('warning', 'No hay periodo académico activo.');
      return;
    }

    bootstrap.Modal.getInstance(document.getElementById('confirmEnrollmentModal'))?.hide();

    const endpoint = mode === 'reinstate'
      ? `/api/v1/permanence/user-program/${upId}/reinstate`
      : `/api/v1/permanence/user-program/${upId}/confirm`;

    const fd = new FormData();
    fd.append('academic_period_id', this.activePeriodId);
    if (notes) fd.append('notes', notes);
    if (file) fd.append('payment_proof', file);
    if (schedule) fd.append('schedule', schedule);
    // 'advance' = avance manual desde Rezagados → coordinador asume el salto explícitamente
    if (mode === 'advance') fd.append('force', 'true');

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'X-CSRFToken': this.csrfToken },
        body: fd,
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) {
        await this.loadStudents();
        await this.loadStats();
        await this.loadEnrollmentTab();
      }
    } catch (e) {
      showFlash('danger', `Error: ${e.message}`);
    }
  }

  // ── Tab Inscripción ───────────────────────────────────────────────────────

  async loadEnrollmentTab() {
    const loading = document.getElementById('enrollmentLoading');
    loading?.classList.remove('d-none');
    try {
      const results = await this._fanFetch(pid => `/api/v1/permanence/program/${pid}/enrollment-overview`);
      const merged = { to_confirm: [], on_leave: [], behind: [], recently_confirmed: [] };
      results.forEach(r => {
        if (!r.data) return;
        const programName = this._programName(r.pid);
        ['to_confirm', 'on_leave', 'behind', 'recently_confirmed'].forEach(k => {
          (r.data[k] || []).forEach(row => { row.__program_name = programName; merged[k].push(row); });
        });
      });
      // Cache para que showConfirmModal pueda leer payment_proof_url por user_program.id
      this._lastOverview = merged;
      this._renderEnrollmentSection('toConfirmTable', 'toConfirmCount', merged.to_confirm, 'confirm');
      this._renderEnrollmentSection('onLeaveTable', 'onLeaveCount', merged.on_leave, 'reinstate');
      this._renderEnrollmentSection('behindTable', 'behindCount', merged.behind, 'advance');
      this._renderRecentSection(merged.recently_confirmed);

      // Badge en la pestaña con total accionable
      const totalActionable = merged.to_confirm.length + merged.on_leave.length + merged.behind.length;
      const badge = document.getElementById('enrollmentBadge');
      if (badge) {
        badge.textContent = totalActionable;
        badge.classList.toggle('d-none', !totalActionable);
      }
    } catch (e) {
      showFlash('danger', `Error al cargar inscripciones: ${e.message}`);
    } finally {
      loading?.classList.add('d-none');
    }
  }

  _renderEnrollmentSection(tableId, countId, rows, mode) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    const countEl = document.getElementById(countId);
    if (countEl) countEl.textContent = rows.length;

    // Agregar columna Programa condicional al thead
    this._toggleProgramHeader(tableId);

    if (!rows.length) {
      // Cols: confirm=5 (estudiante, n°ctrl, próx sem, pago, acciones)
      //       onLeave/behind=5 (estudiante, n°ctrl, sem, periodo, acciones)
      const cols = 5;
      const colspan = this._isAllMode() ? cols + 1 : cols;
      tbody.innerHTML = PermanenceManager.emptyRow(
        colspan, 'check2-circle', 'Sin pendientes',
        'No hay estudiantes en esta situación para el periodo activo.'
      );
      return;
    }

    const btnLabel = mode === 'reinstate' ? 'Reincorporar'
      : mode === 'advance' ? 'Avanzar'
      : 'Confirmar';
    const btnCls = mode === 'reinstate' ? 'btn-warning'
      : mode === 'advance' ? 'btn-primary'
      : 'btn-success';

    tbody.innerHTML = rows.map(r => {
      const u = r.user;
      const last = r.last_enrollment;
      const programCell = this._programCell(r.__program_name);
      const safeName = this.escapeHtml(u.full_name).replace(/'/g, "\\'");

      // Comprobante PDF subido por el estudiante (si existe)
      const ceProofUrl = r.current_enrollment?.payment_proof_url || '';

      // Columnas variables según modo
      let middleCols = '';
      if (mode === 'confirm') {
        const nextSem = (last?.semester_number || 0) + 1;
        const proofCell = ceProofUrl
          ? `<a href="${ceProofUrl}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-primary py-0 px-2"
                title="Ver comprobante. Recuerda revisar el SII para confirmar la inscripción.">
              <i class="bi bi-file-earmark-pdf" aria-hidden="true"></i> Pago
              <span class="visually-hidden">(se abre en una pestaña nueva)</span>
            </a>`
          : '<span class="text-secondary small">Sin pago</span>';
        middleCols = `
          <td class="text-center"><span class="badge bg-info">${nextSem}</span></td>
          <td class="text-center">${proofCell}</td>
        `;
      } else if (mode === 'reinstate' || mode === 'advance') {
        middleCols = `
          <td class="text-center"><span class="badge bg-info">${last?.semester_number || '—'}</span></td>
          <td class="small text-secondary">${last?.period_name || '—'} <span class="badge bg-secondary">${last?.period_code || ''}</span></td>
        `;
      }

      // Pasamos proofUrl como cuarto argumento al modal para evitar lookups por cache.
      const safeProofUrl = ceProofUrl.replace(/'/g, "%27");

      return `
        <tr>
          ${programCell}
          <td>
            <a href="javascript:void(0)" class="text-decoration-none fw-semibold student-name-link"
               onclick="permanenceManager.showStudentExpediente(${u.id})"
               title="Ver expediente">
              ${this.escapeHtml(u.full_name)}
              <i class="bi bi-box-arrow-up-right small ms-1 text-secondary" aria-hidden="true"></i>
            </a>
            <div class="small text-secondary">${this.escapeHtml(u.email)}</div>
          </td>
          <td class="text-center">
            <span class="badge bg-secondary font-monospace">${u.control_number || '—'}</span>
          </td>
          ${middleCols}
          <td class="text-center">
            <button type="button" class="btn btn-sm ${btnCls}"
              onclick="permanenceManager.showConfirmModal(${r.user_program.id}, '${safeName}', '${mode}', '${safeProofUrl}')">
              <i class="bi bi-check-lg me-1" aria-hidden="true"></i>${btnLabel}
            </button>
            ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(u.id) : ''}
          </td>
        </tr>`;
    }).join('');
  }

  _renderRecentSection(rows) {
    const tbody = document.querySelector('#recentTable tbody');
    const countEl = document.getElementById('recentCount');
    if (countEl) countEl.textContent = rows.length;
    this._toggleProgramHeader('recentTable');

    if (!rows.length) {
      const colspan = this._isAllMode() ? 6 : 5;
      tbody.innerHTML = PermanenceManager.emptyRow(
        colspan, 'inbox', 'Sin confirmados todavía',
        'Aquí aparecerán los estudiantes conforme confirmes su inscripción.'
      );
      return;
    }

    tbody.innerHTML = rows.map(r => {
      const u = r.user;
      const ce = r.current_enrollment;
      const programCell = this._programCell(r.__program_name);
      const proofCell = ce?.payment_proof_url
        ? `<a href="${ce.payment_proof_url}" target="_blank" rel="noopener"
              class="btn btn-sm btn-outline-secondary py-0 px-2 tap-target"
              title="Ver comprobante de pago" aria-label="Ver el comprobante de pago de ${this.escapeHtml(u.full_name)} (se abre en una pestaña nueva)">
              <i class="bi bi-file-earmark-pdf" aria-hidden="true"></i></a>`
        : '<span class="text-secondary small">—</span>';
      const completeBtn = ce && ce.status === 'active'
        ? `<button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 tap-target"
             onclick="permanenceManager.markCompletedFromOverview(${ce.id}, '${this.escapeHtml(u.full_name).replace(/'/g, "\\'")}')"
             title="Marcar semestre como completado"
             aria-label="Marcar como completado el semestre de ${this.escapeHtml(u.full_name)}">
             <i class="bi bi-check2-all" aria-hidden="true"></i>
           </button>`
        : '<span class="text-secondary small">—</span>';

      return `
        <tr>
          ${programCell}
          <td>
            <a href="javascript:void(0)" class="text-decoration-none fw-semibold student-name-link"
               onclick="permanenceManager.showStudentExpediente(${u.id})"
               title="Ver expediente">
              ${this.escapeHtml(u.full_name)}
              <i class="bi bi-box-arrow-up-right small ms-1 text-secondary" aria-hidden="true"></i>
            </a>
            <div class="small text-secondary">${this.escapeHtml(u.email)}</div>
          </td>
          <td class="text-center">
            <span class="badge bg-secondary font-monospace">${u.control_number || '—'}</span>
          </td>
          <td class="text-center"><span class="badge bg-info">${ce?.semester_number || '—'}</span></td>
          <td class="text-center">${proofCell}</td>
          <td class="text-center">${completeBtn} ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(u.id) : ''}</td>
        </tr>`;
    }).join('');
  }

  /**
   * Abre modal de doble confirmación para marcar semestre como completado.
   * La ejecución real ocurre en `_doMarkCompleted` cuando confirma.
   */
  markCompletedFromOverview(seId, studentName) {
    document.getElementById('completedStudentName').textContent = studentName;
    document.getElementById('completedSemesterEnrollmentId').value = seId;
    new bootstrap.Modal(document.getElementById('confirmCompletedModal')).show();
  }

  async _doMarkCompleted(seId) {
    bootstrap.Modal.getInstance(document.getElementById('confirmCompletedModal'))?.hide();
    try {
      const res = await fetch(`/api/v1/permanence/semester-enrollment/${seId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ status: 'completed' }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) {
        await this.loadEnrollmentTab();
        await this.loadStudents();
      }
    } catch (e) {
      showFlash('danger', `Error: ${e.message}`);
    }
  }

  // ── Modal Expediente ──────────────────────────────────────────────────────

  async showStudentExpediente(studentId) {
    const modalEl = document.getElementById('studentExpedienteModal');
    const modal = new bootstrap.Modal(modalEl);
    document.getElementById('expModalSpinner').classList.remove('d-none');
    document.getElementById('expModalContent').classList.add('d-none');
    document.getElementById('expModalName').textContent = 'Cargando...';
    document.getElementById('expModalEmail').textContent = '';
    document.getElementById('expModalProgram').textContent = '';
    document.getElementById('expModalControlNumber').textContent = '';
    document.getElementById('expModalAvatar').src = '/static/assets/images/default.jpg';
    modal.show();

    try {
      const res = await fetch(`/api/v1/coordinator/student/${studentId}/permanence-details`);
      const json = await res.json();
      if (!res.ok || json.ok === false) {
        showFlash('danger', json.error || 'Error al cargar expediente');
        modal.hide();
        return;
      }
      this._renderExpediente(json);
    } catch (e) {
      showFlash('danger', `Error al cargar expediente: ${e.message}`);
      modal.hide();
    }
  }

  _renderExpediente(data) {
    const { student, user_program, program, active_period, current_enrollment,
            pending_admission_count, semester_history } = data;

    document.getElementById('expModalName').textContent = student.full_name;
    document.getElementById('expModalEmail').textContent = student.email || '';
    document.getElementById('expModalProgram').textContent = program?.name || '';
    document.getElementById('expModalControlNumber').textContent = student.control_number || '—';
    document.getElementById('expModalAvatar').src = student.avatar_url || '/static/assets/images/default.jpg';

    // Alerta admisión pendiente
    const alertEl = document.getElementById('expAdmissionAlert');
    if (pending_admission_count > 0) {
      document.getElementById('expAdmissionCount').textContent = pending_admission_count;
      alertEl.classList.remove('d-none');
    } else {
      alertEl.classList.add('d-none');
    }

    // Tarjetas resumen
    const ENROLLMENT_ICONS = {
      active: 'bi-play-circle-fill',
      pending: 'bi-hourglass-split',
      completed: 'bi-check-circle-fill',
      on_leave: 'bi-pause-circle-fill',
      dropped: 'bi-x-circle-fill',
    };
    const enrBadge = current_enrollment
      ? PermanenceManager.semesterStatusBadge(current_enrollment.status)
      : PermanenceManager.statusBadge('pending', 'Sin inscripción');
    const enrIcon = current_enrollment
      ? (ENROLLMENT_ICONS[current_enrollment.status] || 'bi-circle')
      : 'bi-dash-circle';

    document.getElementById('expSummaryCards').innerHTML = `
      <div class="col-6 col-md-4">
        <div class="stat-card stat-card--info h-100">
          <i class="bi bi-bookmark-star-fill stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${user_program.current_semester}</p>
          <p class="stat-card__label">Semestre actual</p>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="stat-card stat-card--brand h-100">
          <i class="bi bi-calendar-event-fill stat-card__icon" aria-hidden="true"></i>
          <p class="kpi-value kpi-value--sm">${active_period ? this.escapeHtml(active_period.name) : '—'}</p>
          <p class="stat-card__label">${active_period ? active_period.code : 'Sin periodo activo'}</p>
        </div>
      </div>
      <div class="col-12 col-md-4">
        <div class="stat-card h-100">
          <i class="bi ${enrIcon} stat-card__icon" aria-hidden="true"></i>
          <p class="mb-0">${enrBadge}</p>
          <p class="stat-card__label">Inscripción semestral</p>
        </div>
      </div>
    `;

    // Inscripción del periodo activo
    const enrBody = document.getElementById('expEnrollmentBody');
    if (!active_period) {
      enrBody.innerHTML = '<p class="text-muted mb-0">No hay periodo académico activo.</p>';
    } else if (!current_enrollment) {
      enrBody.innerHTML = `
        <div class="d-flex align-items-center gap-2 flex-wrap">
          ${PermanenceManager.statusBadge('pending', 'Pendiente de confirmación')}
          <span class="small text-secondary">Sin inscripción registrada para el periodo activo.</span>
        </div>`;
    } else {
      const confirmedIcon = current_enrollment.enrollment_confirmed
        ? '<i class="bi bi-check-circle-fill text-success-strong me-1" aria-hidden="true"></i>Confirmado'
        : '<i class="bi bi-dash-circle text-warning-strong me-1" aria-hidden="true"></i>Sin confirmar';
      const confirmedAt = SIIAP.formatDate(current_enrollment.confirmed_at, 'short', '—');
      enrBody.innerHTML = `
        <div class="d-flex flex-wrap gap-3 align-items-center">
          <div><strong>Sem. ${current_enrollment.semester_number}</strong></div>
          <div>${enrBadge}</div>
          <div class="small">${confirmedIcon}</div>
          <div class="small text-secondary">Fecha: ${confirmedAt}</div>
        </div>
        ${current_enrollment.notes ? `<div class="small text-secondary mt-2"><strong>Notas:</strong> ${this.escapeHtml(current_enrollment.notes)}</div>` : ''}
      `;
    }

    // SECIHTI
    const cStatus = document.getElementById('expConacytStatus');
    cStatus.textContent = user_program.has_conacyt_scholarship
      ? 'Becario activo — Formato de Desempeño mensual obligatorio.'
      : 'Sin beca SECIHTI activa.';

    // Historial
    const hBody = document.getElementById('expHistoryBody');
    if (!semester_history || !semester_history.length) {
      hBody.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <h4 class="empty-state__title">Sin historial semestral</h4>
          <p class="empty-state__description">Aún no se registran semestres para este estudiante.</p>
        </div>`;
    } else {
      hBody.innerHTML = `
        <div class="siiap-table-wrapper">
        <table class="table siiap-table table-sm align-middle mb-0">
          <caption class="visually-hidden">Historial de semestres del estudiante</caption>
          <thead class="table-light">
            <tr>
              <th scope="col" class="text-center">Sem.</th>
              <th scope="col">Periodo</th>
              <th scope="col" class="text-center">Estado</th>
              <th scope="col" class="text-center">Confirmado</th>
            </tr>
          </thead>
          <tbody>
            ${semester_history.map(h => {
              const cIcon = h.enrollment_confirmed
                ? '<i class="bi bi-check-circle-fill text-success-strong" aria-hidden="true"></i><span class="visually-hidden">Confirmado</span>'
                : '<i class="bi bi-dash-circle text-secondary" aria-hidden="true"></i><span class="visually-hidden">Sin confirmar</span>';
              return `
                <tr>
                  <td class="text-center fw-bold">${h.semester_number}</td>
                  <td>${this.escapeHtml(h.period_name)} <span class="badge bg-secondary">${h.period_code}</span></td>
                  <td class="text-center">${PermanenceManager.semesterStatusBadge(h.status)}</td>
                  <td class="text-center">${cIcon}</td>
                </tr>`;
            }).join('')}
          </tbody>
        </table>
        </div>`;
    }

    document.getElementById('expModalSpinner').classList.add('d-none');
    document.getElementById('expModalContent').classList.remove('d-none');
  }

  showUpdateStatusModal(enrollmentId, studentName, currentStatus) {
    document.getElementById('updateStatusStudentName').textContent = studentName;
    document.getElementById('updateStatusEnrollmentId').value = enrollmentId;
    document.getElementById('updateStatusSelect').value = currentStatus;
    document.getElementById('updateStatusNotes').value = '';
    new bootstrap.Modal(document.getElementById('updateStatusModal')).show();
  }

  async submitUpdateStatus() {
    const seId = parseInt(document.getElementById('updateStatusEnrollmentId').value);
    const newStatus = document.getElementById('updateStatusSelect').value;
    const notes = document.getElementById('updateStatusNotes').value.trim();

    if (!Number.isInteger(seId) || seId <= 0) {
      showFlash('danger', 'Inscripción inválida — recarga la página e intenta de nuevo.');
      return;
    }

    bootstrap.Modal.getInstance(document.getElementById('updateStatusModal'))?.hide();

    try {
      const res = await fetch(`/api/v1/permanence/semester-enrollment/${seId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ status: newStatus, notes: notes || null }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) await this.loadStudents();
    } catch (e) {
      showFlash('danger', `Error al actualizar estado: ${e.message}`);
    }
  }

  showHistoryByIndex(index) {
    const s = this.students[index];
    if (!s) return;
    this.showHistory(s.user.full_name, s.history);
  }

  showHistory(studentName, history) {
    document.getElementById('historyStudentName').textContent = studentName;
    const container = document.getElementById('historyContent');
    if (!history.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin historial semestral</h3>
          <p class="empty-state__description">Aún no se registran semestres para este estudiante.</p>
        </div>`;
    } else {
      container.innerHTML = `
        <div class="siiap-table-wrapper">
        <table class="table siiap-table table-sm align-middle mb-0">
          <caption class="visually-hidden">Historial de semestres del estudiante</caption>
          <thead class="table-light">
            <tr>
              <th scope="col" class="text-center">Semestre</th>
              <th scope="col">Periodo</th>
              <th scope="col" class="text-center">Estado</th>
              <th scope="col" class="text-center">Confirmado</th>
            </tr>
          </thead>
          <tbody>
            ${history.map(h => {
              const confirmed = h.enrollment_confirmed
                ? '<i class="bi bi-check-circle-fill text-success-strong" aria-hidden="true"></i><span class="visually-hidden">Confirmado</span>'
                : '<i class="bi bi-dash-circle text-secondary" aria-hidden="true"></i><span class="visually-hidden">Sin confirmar</span>';
              return `
                <tr>
                  <td class="text-center fw-bold">Sem. ${h.semester_number}</td>
                  <td>${this.escapeHtml(h.period_name)} <span class="badge bg-secondary">${h.period_code}</span></td>
                  <td class="text-center">${PermanenceManager.semesterStatusBadge(h.status)}</td>
                  <td class="text-center">${confirmed}</td>
                </tr>`;
            }).join('')}
          </tbody>
        </table>
        </div>`;
    }
    new bootstrap.Modal(document.getElementById('historyModal')).show();
  }

  // ── Movilidad / Baja Temporal ───────────────────────────────────────────────

  async loadLeaveRequestsTab() {
    const loading = document.getElementById('leaveLoading');
    const empty = document.getElementById('leaveEmpty');
    const list = document.getElementById('leaveList');
    loading?.classList.remove('d-none');
    empty?.classList.add('d-none');
    if (list) list.innerHTML = '';

    try {
      const results = await this._fanFetch(pid => `/api/v1/permanence/program/${pid}/leave-requests`);
      const requests = [];
      results.forEach(r => {
        if (!Array.isArray(r.data)) return;
        const programName = this._programName(r.pid);
        r.data.forEach(rq => { rq.__program_name = programName; requests.push(rq); });
      });

      // Actualizar badge
      const badge = document.getElementById('leaveBadge');
      if (badge) {
        badge.textContent = requests.length;
        badge.classList.toggle('d-none', !requests.length);
      }

      if (!requests.length) {
        empty?.classList.remove('d-none');
      } else {
        this.renderLeaveRequests(requests);
      }
    } catch (e) {
      if (list) list.innerHTML = `
        <div class="empty-state empty-state--compact empty-state--error">
          <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">No se pudieron cargar las solicitudes</h3>
          <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
          <p class="empty-state__error-detail">${this.escapeHtml(e.message)}</p>
        </div>`;
    } finally {
      loading?.classList.add('d-none');
    }
  }

  renderLeaveRequests(requests) {
    const list = document.getElementById('leaveList');
    if (!list) return;
    list.innerHTML = requests.map(r => {
      const sub = r.submission;
      const uploadDate = SIIAP.formatDate(sub.upload_date, 'short', '—');
      return `
        <div class="border rounded p-3 mb-2 d-flex flex-wrap align-items-center gap-3">
          <div class="flex-grow-1">
            <div class="fw-semibold">${this.escapeHtml(r.user.full_name)}</div>
            <div class="small text-secondary">
              N.º de control: ${this.escapeHtml(r.user.control_number || '—')}
              &nbsp;·&nbsp; Semestre ${r.current_semester || '—'}
              &nbsp;·&nbsp; Subida: ${uploadDate}
            </div>
          </div>
          <div class="d-flex gap-2 flex-shrink-0">
            ${r.file_url ? `<a href="${r.file_url}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">
              <i class="bi bi-file-earmark-text me-1" aria-hidden="true"></i>Ver
              <span class="visually-hidden">la solicitud de ${this.escapeHtml(r.user.full_name)} (se abre en una pestaña nueva)</span>
            </a>` : ''}
            <button type="button" class="btn btn-sm btn-success"
              onclick="permanenceManager.showLeaveModal(${sub.id}, '${this.escapeHtml(r.user.full_name)}', ${r.current_semester || 0}, '${r.file_url || ''}')">
              <i class="bi bi-check-lg me-1" aria-hidden="true"></i>Revisar
              <span class="visually-hidden">la solicitud de ${this.escapeHtml(r.user.full_name)}</span>
            </button>
          </div>
        </div>`;
    }).join('');
  }

  showLeaveModal(submissionId, studentName, semester, fileUrl) {
    document.getElementById('leaveSubmissionId').value = submissionId;
    document.getElementById('leaveStudentName').textContent = studentName;
    document.getElementById('leaveStudentSemester').textContent = semester || '—';
    document.getElementById('leaveNotes').value = '';
    const fileLink = document.getElementById('leaveFileLink');
    if (fileLink) {
      fileLink.href = fileUrl || '#';
      fileLink.classList.toggle('disabled', !fileUrl);
      fileLink.setAttribute('aria-disabled', fileUrl ? 'false' : 'true');
    }
    new bootstrap.Modal(document.getElementById('processLeaveModal')).show();
  }

  async submitLeaveDecision(approve) {
    const submissionId = document.getElementById('leaveSubmissionId')?.value;
    const notes = document.getElementById('leaveNotes')?.value.trim() || null;
    if (!submissionId) return;

    const btn = approve
      ? document.getElementById('btnApproveLeave')
      : document.getElementById('btnRejectLeave');
    btn.disabled = true;

    try {
      const res = await fetch(`/api/v1/permanence/submissions/${submissionId}/leave-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ approve, notes }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) {
        bootstrap.Modal.getInstance(document.getElementById('processLeaveModal'))?.hide();
        await this.loadLeaveRequestsTab();
        await this.loadStudents(); // refrescar badge SECIHTI y estado
      }
    } catch (e) {
      showFlash('danger', `Error al procesar solicitud: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  async createConacytMonthlyDeadlines() {
    if (this._isAllMode()) {
      showFlash('info', 'Selecciona un programa específico para crear ventanas SECIHTI.');
      return;
    }
    const btn = document.getElementById('btnConacytMonthly');
    btn.disabled = true;
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creando...';
    try {
      const res = await fetch(`/api/v1/permanence/program/${this.programId}/deadlines/conacyt-monthly`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({}),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && json.data?.created > 0) {
        await this.loadDocumentsTabData();
      }
    } catch (e) {
      showFlash('danger', `Error al crear ventanas SECIHTI: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }

  /**
   * Abre el modal de doble confirmación. La ejecución real ocurre en
   * `_doToggleConacyt` cuando el usuario confirma desde el modal.
   */
  toggleConacyt(userProgramId, newValue) {
    const student = this.students.find(s => s.user_program?.id === userProgramId);
    const fullName = student?.user?.full_name || 'estudiante';
    const actionLabel = newValue ? 'Activar beca SECIHTI' : 'Quitar beca SECIHTI';

    document.getElementById('confirmConacytStudent').textContent = fullName;
    document.getElementById('confirmConacytAction').textContent = actionLabel;
    document.getElementById('confirmConacytUserProgramId').value = userProgramId;
    document.getElementById('confirmConacytNewValue').value = newValue ? '1' : '0';

    new bootstrap.Modal(document.getElementById('confirmConacytModal')).show();
  }

  async _doToggleConacyt(userProgramId, newValue) {
    try {
      const res = await fetch(`/api/v1/permanence/user-program/${userProgramId}/conacyt-scholarship`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ value: newValue }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) await this.loadStudents();
    } catch (e) {
      showFlash('danger', `Error al actualizar beca SECIHTI: ${e.message}`);
    }
  }

  // ── Acciones: Ventanas de Entrega ─────────────────────────────────────────

  showCreateDeadlineModal() {
    if (this._isAllMode()) {
      showFlash('info', 'Selecciona un programa específico para crear ventanas.');
      return;
    }
    document.getElementById('deadlineArchiveId').value = '';
    document.getElementById('deadlineLabel').value = '';
    document.getElementById('deadlineSequence').value = '1';
    document.getElementById('deadlineOpensAt').value = '';
    document.getElementById('deadlineClosesAt').value = '';
    new bootstrap.Modal(document.getElementById('createDeadlineModal')).show();
  }

  async submitCreateDeadline() {
    const archiveId = parseInt(document.getElementById('deadlineArchiveId').value);
    const label = document.getElementById('deadlineLabel').value.trim();
    const sequence = parseInt(document.getElementById('deadlineSequence').value) || 1;
    const periodId = parseInt(document.getElementById('deadlinePeriodId').value);
    const opensAt = document.getElementById('deadlineOpensAt').value || null;
    const closesAt = document.getElementById('deadlineClosesAt').value || null;

    if (!archiveId || !label) {
      showFlash('warning', 'Selecciona un documento y escribe una etiqueta.');
      return;
    }

    bootstrap.Modal.getInstance(document.getElementById('createDeadlineModal'))?.hide();

    try {
      const res = await fetch(`/api/v1/permanence/program/${this.programId}/deadlines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({
          archive_id: archiveId,
          label,
          sequence,
          academic_period_id: periodId || null,
          opens_at: opensAt,
          closes_at: closesAt,
        }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) this.loadDocumentsTabData();
    } catch (e) {
      showFlash('danger', `Error al crear ventana: ${e.message}`);
    }
  }

  async toggleDeadline(deadlineId, isOpen) {
    try {
      const res = await fetch(`/api/v1/permanence/deadlines/${deadlineId}/toggle`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ is_open: isOpen }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) this.loadDocumentsTabData();
    } catch (e) {
      showFlash('danger', `Error al actualizar ventana: ${e.message}`);
    }
  }

  // ── Archivar ventana (soft-delete) ─────────────────────────────────────
  archiveDeadline(deadlineId, label) {
    document.getElementById('deleteDeadlineId').value = deadlineId;
    document.getElementById('deleteDeadlineLabel').textContent = label;
    new bootstrap.Modal(document.getElementById('deleteDeadlineModal')).show();
  }

  // Alias retro-compat (cualquier llamada antigua a deleteDeadline ahora archiva)
  deleteDeadline(deadlineId, label) {
    return this.archiveDeadline(deadlineId, label);
  }

  async _confirmDeleteDeadline() {
    const deadlineId = document.getElementById('deleteDeadlineId').value;
    bootstrap.Modal.getInstance(document.getElementById('deleteDeadlineModal'))?.hide();
    try {
      const res = await fetch(`/api/v1/permanence/deadlines/${deadlineId}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': this.csrfToken },
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) this.loadDocumentsTabData();
    } catch (e) {
      showFlash('danger', `Error al archivar ventana: ${e.message}`);
    }
  }

  async restoreDeadline(deadlineId) {
    try {
      const res = await fetch(`/api/v1/permanence/deadlines/${deadlineId}/restore`, {
        method: 'POST',
        headers: { 'X-CSRFToken': this.csrfToken },
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) this.loadDocumentsTabData();
    } catch (e) {
      showFlash('danger', `Error al restaurar ventana: ${e.message}`);
    }
  }

  // ── Editar ventana de entrega ──────────────────────────────────────────────
  openEditDeadlineModal(deadlineId) {
    // Localizar la ventana en el cache `_deadlinesCache` poblado por loadDocumentsTabData
    const dl = (this._deadlinesCache || []).find(d => d.id === deadlineId);
    if (!dl) {
      showFlash('warning', 'Ventana no encontrada en la vista actual.');
      return;
    }
    document.getElementById('editDeadlineId').value = dl.id;
    document.getElementById('editDeadlineLabel').value = dl.label || '';
    // Convertir ISO timestamp a 'YYYY-MM-DDTHH:MM' (datetime-local)
    const toLocalDt = (iso) => {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const tz = d.getTimezoneOffset() * 60000;
      return new Date(d.getTime() - tz).toISOString().slice(0, 16);
    };
    document.getElementById('editDeadlineOpensAt').value = toLocalDt(dl.opens_at);
    document.getElementById('editDeadlineClosesAt').value = toLocalDt(dl.closes_at);
    document.getElementById('editDeadlineIsOpen').checked = !!dl.is_open;
    new bootstrap.Modal(document.getElementById('editDeadlineModal')).show();
  }

  async _submitEditDeadline() {
    const id = parseInt(document.getElementById('editDeadlineId').value, 10);
    if (!id) return;
    const payload = {
      label: document.getElementById('editDeadlineLabel').value.trim(),
      opens_at: document.getElementById('editDeadlineOpensAt').value || null,
      closes_at: document.getElementById('editDeadlineClosesAt').value || null,
      is_open: document.getElementById('editDeadlineIsOpen').checked,
    };
    try {
      const res = await fetch(`/api/v1/permanence/deadlines/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) {
        bootstrap.Modal.getInstance(document.getElementById('editDeadlineModal'))?.hide();
        this.loadDocumentsTabData();
      }
    } catch (e) {
      showFlash('danger', `Error al actualizar ventana: ${e.message}`);
    }
  }

  // ── Acciones: Revisión de Documentos ─────────────────────────────────────

  showReviewModal(submissionId, studentName, deadlineLabel, focusReject = false) {
    document.getElementById('reviewDocSubmissionId').value = submissionId;
    document.getElementById('reviewDocStudentName').textContent = studentName;
    document.getElementById('reviewDocLabel').textContent = deadlineLabel;
    document.getElementById('reviewDocNotes').value = '';
    if (focusReject) {
      document.getElementById('reviewDocNotes').placeholder = 'Motivo del rechazo (requerido)...';
    } else {
      document.getElementById('reviewDocNotes').placeholder = 'Comentarios opcionales...';
    }
    new bootstrap.Modal(document.getElementById('reviewDocModal')).show();
  }

  async submitReview(status) {
    const subId = parseInt(document.getElementById('reviewDocSubmissionId').value);
    const notes = document.getElementById('reviewDocNotes').value.trim();

    if (!Number.isInteger(subId) || subId <= 0) {
      showFlash('danger', 'Submission inválida — recarga la página e intenta de nuevo.');
      return;
    }

    if (status === 'rejected' && !notes) {
      showFlash('warning', 'Escribe el motivo del rechazo antes de continuar.');
      return;
    }

    bootstrap.Modal.getInstance(document.getElementById('reviewDocModal'))?.hide();

    try {
      const res = await fetch(`/api/v1/permanence/submissions/${subId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ status, notes: notes || null }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) this.loadDocumentsTabData();
    } catch (e) {
      showFlash('danger', `Error al revisar documento: ${e.message}`);
    }
  }

  // ── Utilidades ─────────────────────────────────────────────────────────────

  filterTable(query) {
    const q = query.toLowerCase();
    const filtered = this.students.filter(s =>
      s.user.full_name.toLowerCase().includes(q) ||
      (s.user.control_number || '').toLowerCase().includes(q) ||
      (s.user.email || '').toLowerCase().includes(q)
    );
    this.renderTable(filtered);
    SIIAP.announce(`${filtered.length} estudiante(s) coinciden con la búsqueda.`);
  }

  showLoading(show) {
    document.getElementById('loadingSpinner')?.classList.toggle('d-none', !show);
    if (show) document.getElementById('tableContainer')?.classList.add('d-none');
  }

  escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

// Inicializar
let permanenceManager;
document.addEventListener('DOMContentLoaded', () => {
  // Inicializa siempre — null = modo "Todos los programas"
  const initialPid = (typeof PROGRAM_ID !== 'undefined') ? PROGRAM_ID : null;
  const initialPeriod = (typeof ACTIVE_PERIOD_ID !== 'undefined') ? ACTIVE_PERIOD_ID : null;
  permanenceManager = new PermanenceManager(initialPid, initialPeriod);

  // ── Confirmar eliminación de ventana ──
  document.getElementById('btnConfirmDeleteDeadline')?.addEventListener('click', () => {
    permanenceManager?._confirmDeleteDeadline();
  });

  // ── Guardar edición de ventana ──
  document.getElementById('btnSaveEditDeadline')?.addEventListener('click', () => {
    permanenceManager?._submitEditDeadline();
  });

  // ── Toggle "mostrar archivadas" ──
  document.getElementById('toggleShowArchivedDeadlines')?.addEventListener('change', () => {
    permanenceManager?.loadDocumentsTabData();
  });

  // ── Doble confirmación SECIHTI (toggle desde tabla) ──
  document.getElementById('btnConfirmConacyt')?.addEventListener('click', () => {
    if (!permanenceManager) return;
    const upId = parseInt(document.getElementById('confirmConacytUserProgramId').value);
    const newValue = document.getElementById('confirmConacytNewValue').value === '1';
    bootstrap.Modal.getInstance(document.getElementById('confirmConacytModal'))?.hide();
    permanenceManager._doToggleConacyt(upId, newValue);
  });

  // ── Confirmar marcar semestre completado ──
  document.getElementById('btnConfirmCompleted')?.addEventListener('click', () => {
    if (!permanenceManager) return;
    const seId = parseInt(document.getElementById('completedSemesterEnrollmentId').value);
    permanenceManager._doMarkCompleted(seId);
  });

  // ── Tiempo real: nuevo documento de permanencia recibido ──
  window.addEventListener('siiap:submission:new', (e) => {
    const data = e.detail;
    if (!data || !permanenceManager) return;
    // En modo específico, sólo reaccionar al programa actual.
    // En modo "Todos" reaccionamos a cualquier programa accesible.
    if (permanenceManager.programId &&
        data.program_id &&
        String(data.program_id) !== String(permanenceManager.programId)) return;

    if (data.context === 'permanence') {
      permanenceManager.loadStats();
      permanenceManager.loadDocumentsTabData();
    } else if (data.context === 'leave_request') {
      permanenceManager.loadStats();
      permanenceManager.loadLeaveRequestsTab();
    }
  });
});
