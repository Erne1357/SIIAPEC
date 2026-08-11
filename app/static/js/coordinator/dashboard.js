// static/js/coordinator/dashboard.js - FASE 2
document.addEventListener('DOMContentLoaded', () => {
  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };
  const csrf = getCsrf();

  function emitFlash(level, message) {
    window.dispatchEvent(new CustomEvent('flash', { detail: { level, message } }));
  }

  // ==================== VARIABLES GLOBALES ====================
  let studentsData = [];
  let currentFilters = {};

  // ==================== INICIALIZACIÓN ====================
  loadPrograms();  // Cargar programas primero
  loadStudents();
  setupEventListeners();

  // ==================== CARGA DE PROGRAMAS ====================
  async function loadPrograms() {
    try {
      const res = await fetch('/api/v1/coordinator/programs', {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('No se pudieron cargar los programas');

      const data = await res.json();
      const programFilter = document.getElementById('programFilter');
      
      if (programFilter && data.programs) {
        programFilter.innerHTML = '<option value="">Todos los programas</option>' +
          data.programs.map(p =>
            `<option value="${SIIAP.escapeAttr(p.id)}">${SIIAP.escapeHtml(p.name)}</option>`
          ).join('');
      }
    } catch (err) {
      console.error('Error loading programs:', err);
    }
  }

  // ==================== CARGA DE DATOS ====================
  async function loadStudents() {
    try {
      const params = new URLSearchParams(currentFilters);
      const res = await fetch(`/api/v1/coordinator/students?${params}`, {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('No se pudieron cargar los estudiantes');

      const data = await res.json();
      

      studentsData = data.students || [];
      
      

      updateTables();
      updateCounts();

    } catch (err) {
      console.error('Error loading students:', err);
      emitFlash('danger', 'Error al cargar estudiantes');
    }
  }

  /** Estado vacío en tabla con el componente compartido completo. */
  function emptyRow(colspan, icon, title, description) {
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

  // ==================== ACTUALIZACIÓN DE TABLAS ====================
  function updateTables() {
    updateAdmissionTable();
    updatePermanenceTable();
    updateConclusionTable();
  }

  function updateAdmissionTable() {
    const tbody = document.querySelector('#admissionTable tbody');
    // Ordenar por progreso ascendente (menor progreso primero = necesita más atención)
    const admissionStudents = studentsData
      .filter(s => s.current_phase === 'admission')
      .sort((a, b) => a.progress_percentage - b.progress_percentage);

    tbody.innerHTML = admissionStudents.map(student => {
      // Numeric coercion for the CSS custom property: a string value here would
      // be a style-attribute injection, and escaping alone does not fix that.
      const progress = Number(student.progress_percentage) || 0;
      return `
      <tr data-student-id="${SIIAP.escapeAttr(student.id)}" class="${student.can_manage ? '' : 'table-secondary'}"
          title="${student.can_manage ? '' : 'Solo consulta - Programa de otro coordinador'}">
        <td>
          <img src="${SIIAP.escapeAttr(student.avatar_url || '/static/assets/images/default.jpg')}"
               alt="" class="rounded-circle avatar-xs">
        </td>
        <td>
          <div>
            <div class="fw-semibold">${SIIAP.escapeHtml(student.full_name)}</div>
            <small class="text-muted">${SIIAP.escapeHtml(student.email)}</small>
          </div>
        </td>
        <td>
          <span class="text-secondary small">${SIIAP.escapeHtml(student.program_name)}</span>
          ${!student.can_manage ? '<i class="bi bi-eye text-secondary ms-1" aria-hidden="true"></i><span class="visually-hidden">Solo consulta</span>' : ''}
        </td>
        <td class="text-center">
          <div class="progress" role="progressbar" aria-valuenow="${progress}"
               aria-valuemin="0" aria-valuemax="100" aria-label="Progreso documental">
            <div class="progress-bar progress-bar--dynamic" style="--progress: ${progress}%"></div>
          </div>
          <small class="text-secondary">${progress}% completado</small>
        </td>
        <td class="text-center">
          <span class="badge bg-success me-1" title="Aprobados">${SIIAP.escapeHtml(student.approved_docs)}<span class="visually-hidden"> aprobados</span></span>
          <span class="badge bg-warning me-1" title="Pendientes">${SIIAP.escapeHtml(student.pending_docs)}<span class="visually-hidden"> pendientes</span></span>
          <span class="badge bg-info me-1" title="En prórroga">${SIIAP.escapeHtml(student.extended_docs)}<span class="visually-hidden"> en prórroga</span></span>
          <span class="badge bg-danger" title="Rechazados">${SIIAP.escapeHtml(student.rejected_docs)}<span class="visually-hidden"> rechazados</span></span>
        </td>
        <td class="text-center">
          ${getStatusBadge(student.overall_status)}
        </td>
        <td>
          <div class="btn-group btn-group-sm" role="group">
            <button class="btn btn-outline-primary btn-view-student"
                    data-student-id="${SIIAP.escapeAttr(student.id)}" title="Ver detalles">
              <i class="bi bi-eye"></i>
            </button>
            ${student.can_manage ? `
            <button type="button" class="btn btn-outline-success btn-upload-for tap-target"
                    data-student-id="${SIIAP.escapeAttr(student.id)}" title="Subir documento"
                    aria-label="Subir documento de ${SIIAP.escapeAttr(student.full_name)}">
              <i class="bi bi-upload" aria-hidden="true"></i>
            </button>
            ` : ''}
            ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(student.id) : ''}
          </div>
        </td>
      </tr>
    `;
    }).join('') || emptyRow(7, 'people', 'Sin estudiantes en admisión',
        'Ajusta los filtros o espera a que se registren nuevos aspirantes.');
    SIIAP.announce(`${admissionStudents.length} estudiante(s) en admisión.`);
  }

  function updatePermanenceTable() {
    const tbody = document.querySelector('#permanenceTable tbody');
    // Ordenar por progreso ascendente (menor progreso primero = necesita más atención)
    const permanenceStudents = studentsData
      .filter(s => s.current_phase === 'permanence')
      .sort((a, b) => a.academic_progress - b.academic_progress);

    tbody.innerHTML = permanenceStudents.map(student => `
      <tr data-student-id="${SIIAP.escapeAttr(student.id)}" class="${student.can_manage ? '' : 'table-secondary'}"
          title="${student.can_manage ? '' : 'Solo consulta - Programa de otro coordinador'}">
        <td>
          <img src="${SIIAP.escapeAttr(student.avatar_url || '/static/assets/images/default.jpg')}"
               alt="" class="rounded-circle avatar-xs">
        </td>
        <td>
          <div>
            <div class="fw-semibold">${SIIAP.escapeHtml(student.full_name)}</div>
            <small class="text-muted">${SIIAP.escapeHtml(student.email)}</small>
          </div>
        </td>
        <td>
          <span class="text-secondary small">${SIIAP.escapeHtml(student.program_name)}</span>
          ${!student.can_manage ? '<i class="bi bi-eye text-secondary ms-1" aria-hidden="true"></i><span class="visually-hidden">Solo consulta</span>' : ''}
        </td>
        <td class="text-center">
          <span class="badge bg-secondary">${SIIAP.escapeHtml(student.current_semester || '—')}</span>
          <span class="visually-hidden">semestre</span>
        </td>
        <td class="text-center">
          ${renderPermanenceProgress(student)}
        </td>
        <td class="text-center">
          ${getPermanenceStatusBadge(student.academic_status)}
        </td>
        <td>
          <div class="btn-group btn-group-sm" role="group">
            <button type="button" class="btn btn-outline-info btn-view-permanence tap-target"
                    data-student-id="${SIIAP.escapeAttr(student.id)}"
                    title="Ver detalle de permanencia"
                    aria-label="Ver detalle de permanencia de ${SIIAP.escapeAttr(student.full_name)}">
              <i class="bi bi-person-badge" aria-hidden="true"></i>
            </button>
            ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(student.id) : ''}
          </div>
        </td>
      </tr>
    `).join('') || emptyRow(7, 'mortarboard', 'Sin estudiantes en permanencia',
        'Ajusta los filtros o confirma inscripciones desde el panel de Permanencia.');
    SIIAP.announce(`${permanenceStudents.length} estudiante(s) en permanencia.`);
  }

  function updateConclusionTable() {
    const tbody = document.querySelector('#conclusionTable tbody');
    // Ordenar por progreso ascendente (menor progreso primero = necesita más atención)
    const conclusionStudents = studentsData
      .filter(s => s.current_phase === 'conclusion')
      .sort((a, b) => a.conclusion_progress - b.conclusion_progress);

    tbody.innerHTML = conclusionStudents.map(student => {
      // Numeric coercion for the CSS custom property (style-attribute injection).
      const progress = Number(student.conclusion_progress) || 0;
      return `
      <tr data-student-id="${SIIAP.escapeAttr(student.id)}" class="${student.can_manage ? '' : 'table-secondary'}"
          title="${student.can_manage ? '' : 'Solo consulta - Programa de otro coordinador'}">
        <td>
          <img src="${SIIAP.escapeAttr(student.avatar_url || '/static/assets/images/default.jpg')}"
               alt="" class="rounded-circle avatar-xs">
        </td>
        <td>
          <div>
            <div class="fw-semibold">${SIIAP.escapeHtml(student.full_name)}</div>
            <small class="text-muted">${SIIAP.escapeHtml(student.email)}</small>
          </div>
        </td>
        <td>
          <span class="text-secondary small">${SIIAP.escapeHtml(student.program_name)}</span>
          ${!student.can_manage ? '<i class="bi bi-eye text-secondary ms-1" aria-hidden="true"></i><span class="visually-hidden">Solo consulta</span>' : ''}
        </td>
        <td class="text-center">
          <span class="badge bg-secondary">${SIIAP.escapeHtml(student.conclusion_stage || 'Inicial')}</span>
        </td>
        <td class="text-center">
          <div class="progress" role="progressbar" aria-valuenow="${progress}"
               aria-valuemin="0" aria-valuemax="100" aria-label="Progreso de conclusión">
            <div class="progress-bar progress-bar--dynamic bg-success" style="--progress: ${progress}%"></div>
          </div>
          <small class="text-secondary">${progress}% completado</small>
        </td>
        <td class="text-center">
          ${getStatusBadge(student.conclusion_status)}
        </td>
        <td>
          <div class="btn-group btn-group-sm" role="group">
            <button type="button" class="btn btn-outline-primary btn-view-student tap-target"
                    data-student-id="${SIIAP.escapeAttr(student.id)}" title="Ver detalles"
                    aria-label="Ver detalles de ${SIIAP.escapeAttr(student.full_name)}">
              <i class="bi bi-eye" aria-hidden="true"></i>
            </button>
            ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(student.id) : ''}
          </div>
        </td>
      </tr>
    `;
    }).join('') || emptyRow(7, 'award', 'Sin estudiantes en conclusión',
        'Ajusta los filtros o espera a que avancen a la fase de conclusión.');
    SIIAP.announce(`${conclusionStudents.length} estudiante(s) en conclusión.`);
  }

  // Los chips de ESTADO usan el componente compartido .status-badge (icono +
  // texto + color): el color nunca es el único portador de significado.
  /** Chip del estado de una inscripción semestral. */
  function semesterStatusBadge(status) {
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

  function getStatusBadge(status) {
    const map = {
      'pending':     ['pending',     'Pendiente'],
      'in_progress': ['in_progress', 'En progreso'],
      'review':      ['review',      'En revisión'],
      'approved':    ['approved',    'Aprobado'],
      'rejected':    ['rejected',    'Rechazado'],
      'completed':   ['approved',    'Completado'],
    };
    const [key, label] = map[status] || ['pending', 'Sin dato'];
    return SIIAP.statusBadge(key, label, 'sm');
  }

  function getPermanenceStatusBadge(status) {
    const map = {
      'active':    ['in_progress', 'Cursando'],
      'completed': ['approved',    'Completado'],
      'on_leave':  ['deferred',    'Baja temporal'],
      'dropped':   ['rejected',    'Baja definitiva'],
      'pending':   ['pending',     'Sin inscripción'],
    };
    const [key, label] = map[status] || ['pending', 'Sin dato'];
    return SIIAP.statusBadge(key, label, 'sm');
  }

  function renderPermanenceProgress(student) {
    const completed = Number(student.academic_progress) || 0;
    const inProgress = Number(student.in_progress_segment) || 0;
    const completedSemesters = student.completed_semesters ?? 0;
    const total = student.total_semesters ?? 4;
    const inProgressBar = inProgress > 0
      ? `<div class="progress-bar progress-bar--dynamic perm-progress-current"
              style="--progress: ${inProgress}%"></div>`
      : '';
    return `
      <div class="progress" role="progressbar" aria-valuenow="${completed}"
           aria-valuemin="0" aria-valuemax="100" aria-label="Avance académico">
        <div class="progress-bar progress-bar--dynamic bg-info" style="--progress: ${completed}%"></div>
        ${inProgressBar}
      </div>
      <small class="text-secondary">${SIIAP.escapeHtml(completedSemesters)} de ${SIIAP.escapeHtml(total)} semestres · ${completed}%</small>
    `;
  }

  function updateCounts() {
    const phases = ['admission', 'permanence', 'conclusion'];
    phases.forEach(phase => {
      const count = studentsData.filter(s => s.current_phase === phase).length;
      document.getElementById(`${phase}Count`).textContent = count;
    });
  }

  // ==================== EVENT LISTENERS ====================
  function setupEventListeners() {
    // Filtros
    document.getElementById('programFilter').addEventListener('change', updateFilters);
    document.getElementById('phaseFilter').addEventListener('change', updateFilters);
    document.getElementById('statusFilter').addEventListener('change', updateFilters);
    document.getElementById('showOtherPrograms').addEventListener('change', updateFilters);

    // Búsqueda
    document.getElementById('studentSearch').addEventListener('input', debounce(updateFilters, 300));
    document.getElementById('searchBtn').addEventListener('click', updateFilters);

    // Botones de acción
    document.getElementById('refreshBtn').addEventListener('click', loadStudents);

    // Delegación de eventos para botones dinámicos
    document.addEventListener('click', handleButtonClicks);

    // Subida de archivos por coordinador
    setupCoordinatorUpload();

    // ==================== TIEMPO REAL ====================
    // Estrategia: partial refresh (loadStudents) en lugar de reload completo,
    // para preservar filtros activos y no interrumpir workflow del coordinador.
    // Se debounces para evitar ráfagas si llegan muchos eventos juntos.
    //
    // Eventos que SÍ llegan a la sala role:coordinator:
    //   - submission:new         → aspirante/estudiante subió documento
    //   - acceptance:updated     → receipt_submitted (aspirante subió boleta)
    //   - deliberation:updated   → sólo si el coordinador entró a la sala deliberation:{pid}
    // Otros eventos (submission:reviewed, admission:status_changed, permanence:status_changed,
    // extension:decided) se emiten sólo al usuario afectado; ver PLAN_SOCKETS.md Fase 5
    // para agregar canal coordinator:feed si se requiere propagar todos los cambios.
    const refreshDebounced = debounce(loadStudents, 800);

    window.addEventListener('siiap:submission:new', (e) => {
      const d = e.detail || {};
      emitFlash('info', `Nuevo documento: ${d.archive_name || 'documento'}`);
      refreshDebounced();
    });

    window.addEventListener('siiap:acceptance:updated',   refreshDebounced);
    window.addEventListener('siiap:deliberation:updated', refreshDebounced);
  }

  function updateFilters() {
    currentFilters = {
      program_id: document.getElementById('programFilter').value,
      phase: document.getElementById('phaseFilter').value,
      status: document.getElementById('statusFilter').value,
      show_other: document.getElementById('showOtherPrograms').checked ? 'true' : '',
      search: document.getElementById('studentSearch').value.trim()
    };

    // Remover valores vacíos
    Object.keys(currentFilters).forEach(key => {
      if (!currentFilters[key]) delete currentFilters[key];
    });

    loadStudents();
  }

  function handleButtonClicks(e) {
    const button = e.target.closest('button');
    if (!button) return;

    const studentId = button.dataset.studentId;

    if (button.classList.contains('btn-view-student')) {
      viewStudentDetails(studentId);
    } else if (button.classList.contains('btn-view-permanence')) {
      viewPermanenceDetails(studentId);
    } else if (button.classList.contains('btn-upload-for')) {
      openUploadModal(studentId);
    }
  }

  // ==================== ACCIONES DE ESTUDIANTES ====================
  async function viewStudentDetails(studentId) {
    const student = studentsData.find(s => s.id == studentId);
    if (!student) return;

    // Mostrar modal con loading
    const modal = new bootstrap.Modal(document.getElementById('studentDetailsModal'));
    document.getElementById('modalStudentName').textContent = 'Cargando...';
    document.getElementById('modalStudentEmail').textContent = '';
    document.getElementById('modalStudentProgram').textContent = '';
    
    // Mostrar loading solo en el tab activo, manteniendo la estructura del modal
    document.getElementById('generalTabContent').innerHTML = `
      <div class="text-center py-5">
        <div class="spinner-border text-primary" role="status">
          <span class="visually-hidden">Cargando información del estudiante…</span>
        </div>
        <p class="mt-3 text-secondary">Obteniendo información del estudiante…</p>
      </div>
    `;
    
    // Limpiar otros tabs
    document.getElementById('documentsTabContent').innerHTML = '';
    document.getElementById('interviewTabContent').innerHTML = '';
    
    // Asegurar que el tab General esté activo
    document.getElementById('general-tab').click();
    
    modal.show();

    try {
      const res = await fetch(`/api/v1/coordinator/student/${encodeURIComponent(studentId)}/details`, {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('Error al cargar detalles');

      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Error desconocido');

      renderStudentDetails(data);

    } catch (err) {
      console.error('Error loading student details:', err);
      // El error se pinta DENTRO del panel General, nunca sobre #modalContent:
      // reemplazar el cuerpo entero borra las pestañas y sus contenedores, y a
      // partir de ahí cualquier apertura posterior del modal falla en cadena.
      document.getElementById('generalTabContent').innerHTML = `
      <div class="alert alert-danger">
        <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
        Error al cargar información: ${SIIAP.escapeHtml(err.message)}
      </div>
    `;
      document.getElementById('documentsTabContent').innerHTML = '';
      document.getElementById('interviewTabContent').innerHTML = '';
    }
  }

  /**
   * Aviso del nivel reducido entre programas.
   *
   * El backend responde 200 con `restricted: true` y todo lo prohibido en
   * `null`. Sin este cartel el modal parecería un expediente vacío —«perfil
   * incompleto», «sin documentos»— y eso sería afirmar como hecho algo que no
   * sabemos. Aquí se dice lo único cierto: el dato existe, pero pertenece a
   * otro programa.
   */
  function restrictedNotice(extra) {
    return `
      <div class="alert alert-info d-flex align-items-start gap-2">
        <i class="bi bi-shield-lock-fill flex-shrink-0 mt-1" aria-hidden="true"></i>
        <div>
          <strong>Información limitada.</strong>
          Esta persona pertenece a un programa que no gestionas. Solo puedes
          consultar su nombre, su correo y su avance general.
          ${extra ? `<span class="d-block mt-1">${extra}</span>` : ''}
        </div>
      </div>`;
  }

  function renderStudentDetails(data) {
    const student = data.student;
    const restricted = data.restricted === true || data.can_manage === false;

    // Header
    document.getElementById('modalStudentName').textContent = student.full_name || '';
    document.getElementById('modalStudentEmail').textContent = student.email || '';
    document.getElementById('modalStudentAvatar').src = student.avatar_url || '/static/assets/images/default.jpg';
    document.getElementById('modalStudentProgram').textContent =
      (student.program && student.program.name) || 'Programa no disponible';

    // Tab General
    renderGeneralTab(student, data.metrics || {}, data.missing_documents || [], restricted);

    // Tab Documentos
    renderDocumentsTab(data.documents || [], student.id, data.can_manage, restricted);

    // Tab Entrevista
    renderInterviewTab(data.interview || {}, student, restricted);
  }

  function renderGeneralTab(student, metrics, missing, restricted) {
    const generalContent = document.getElementById('generalTabContent');
    // Numeric coercion for the CSS custom property (style-attribute injection).
    const progress = Number(metrics.progress_percentage) || 0;
    const profile = student.profile_data || {};
    const emergency = profile.emergency_contact || {};

    // Fuera de alcance no hay «perfil incompleto» ni «documentos en orden»: no
    // los sabemos. Cada bloque que dependa de un dato prohibido se sustituye
    // por su versión neutra en lugar de imprimir el valor por defecto.
    const profileBlock = restricted
      ? ''
      : `
    <div class="alert ${student.profile_completed ? 'alert-success' : 'alert-warning'} mb-4">
      <h4 class="h6 mb-2">
        <i class="bi bi-person-check-fill me-2" aria-hidden="true"></i>Estado del perfil
      </h4>
      ${student.profile_completed
        ? '<p class="mb-0"><i class="bi bi-check-circle-fill me-1" aria-hidden="true"></i>Perfil completo: elegible para entrevista</p>'
        : '<p class="mb-0"><i class="bi bi-exclamation-triangle-fill me-1" aria-hidden="true"></i>Perfil incompleto: debe completar sus datos personales</p>'}
    </div>`;

    const missingBlock = restricted
      ? ''
      : (missing.length > 0 ? `
      <div class="mb-4">
        <h4 class="h6 mb-2">
          <i class="bi bi-exclamation-circle-fill text-warning-strong me-2" aria-hidden="true"></i>
          Documentos pendientes (${missing.length})
        </h4>
        <ul class="list-group">
          ${missing.map(item => `
            <li class="list-group-item d-flex justify-content-between align-items-center">
              <div>
                <strong>${SIIAP.escapeHtml(item.archive)}</strong>
                <small class="d-block text-muted">${SIIAP.escapeHtml(item.step)}</small>
              </div>
              ${item.status === 'rejected'
                ? SIIAP.statusBadge('rejected', 'Rechazado', 'sm')
                : SIIAP.statusBadge('pending', 'Pendiente', 'sm')}
            </li>
          `).join('')}
        </ul>
      </div>
    ` : '<div class="alert alert-success"><i class="bi bi-check-circle-fill me-1" aria-hidden="true"></i>Todos los documentos están en orden</div>');

    const personalBlock = restricted
      ? `
    <div>
      <h4 class="h6 mb-3">Datos personales</h4>
      <div class="empty-state empty-state--compact">
        <div class="empty-state__icon"><i class="bi bi-shield-lock" aria-hidden="true"></i></div>
        <h5 class="empty-state__title">Reservados a su programa</h5>
        <p class="empty-state__description">
          El teléfono, la CURP, el NSS, la fecha de nacimiento y el contacto de
          emergencia solo puede consultarlos quien coordina el programa de esta
          persona.
        </p>
      </div>
    </div>`
      : `
    <div>
      <h4 class="h6 mb-3">Datos personales</h4>
      <dl class="row g-3 mb-0">
        <div class="col-md-6">
          <dt class="small text-secondary fw-normal">Teléfono</dt>
          <dd class="mb-0">${SIIAP.escapeHtml(profile.phone || profile.mobile_phone || 'No registrado')}</dd>
        </div>
        <div class="col-md-6">
          <dt class="small text-secondary fw-normal">CURP</dt>
          <dd class="mb-0">${SIIAP.escapeHtml(profile.curp || 'No registrado')}</dd>
        </div>
        <div class="col-md-6">
          <dt class="small text-secondary fw-normal">Fecha de nacimiento</dt>
          <dd class="mb-0">${SIIAP.escapeHtml(SIIAP.formatDate(profile.birth_date, 'long', 'No registrado'))}</dd>
        </div>
        <div class="col-md-6">
          <dt class="small text-secondary fw-normal">NSS</dt>
          <dd class="mb-0">${SIIAP.escapeHtml(profile.nss || 'No registrado')}</dd>
        </div>
        <div class="col-12">
          <dt class="small text-secondary fw-normal">Contacto de emergencia</dt>
          <dd class="mb-0">
            ${SIIAP.escapeHtml(emergency.name || 'No registrado')}
            <small class="d-block text-secondary">
              ${SIIAP.escapeHtml(emergency.phone || '')}
              ${emergency.relationship ? `(${SIIAP.escapeHtml(emergency.relationship)})` : ''}
            </small>
          </dd>
        </div>
      </dl>
    </div>`;

    generalContent.innerHTML = `
    ${restricted ? restrictedNotice('Los contadores y el porcentaje de avance sí son datos reales.') : ''}
    <!-- Métricas -->
    <div class="row g-3 mb-4">
      <div class="col-6 col-md-3">
        <div class="stat-card stat-card--success h-100">
          <i class="bi bi-check-circle-fill stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${SIIAP.escapeHtml(metrics.approved)}</p>
          <p class="stat-card__label">Aprobados</p>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card stat-card--warning h-100">
          <i class="bi bi-hourglass-split stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${SIIAP.escapeHtml(metrics.pending)}</p>
          <p class="stat-card__label">Pendientes</p>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card stat-card--danger h-100">
          <i class="bi bi-x-circle-fill stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${SIIAP.escapeHtml(metrics.rejected)}</p>
          <p class="stat-card__label">Rechazados</p>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card stat-card--info h-100">
          <i class="bi bi-clock-history stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${SIIAP.escapeHtml(metrics.extended)}</p>
          <p class="stat-card__label">En prórroga</p>
        </div>
      </div>
    </div>
    
    <!-- Progreso -->
    <div class="mb-4">
      <h4 class="h6 mb-2">Progreso general</h4>
      <div class="progress progress--lg" role="progressbar"
           aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"
           aria-label="Progreso general del expediente">
        <div class="progress-bar progress-bar--dynamic bg-success" style="--progress: ${progress}%">
          ${progress}%
        </div>
      </div>
    </div>
    
    <!-- Estado del Perfil -->
    ${profileBlock}

    <!-- Documentos Faltantes/Rechazados -->
    ${missingBlock}

    <!-- Datos Personales -->
    ${personalBlock}
  `;
  }

  function renderDocumentsTab(documents, studentId, canManage, restricted) {
    const docsContent = document.getElementById('documentsTabContent');

    // Fuera de alcance el backend NO envía documentos: la lista vacía no
    // significa «no subió nada», significa «no puedes verlos». Decirlo.
    if (restricted) {
      docsContent.innerHTML = `
        ${restrictedNotice()}
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-file-earmark-lock" aria-hidden="true"></i></div>
          <h4 class="empty-state__title">Documentos no disponibles</h4>
          <p class="empty-state__description">
            Los expedientes y sus archivos solo son accesibles para quien
            coordina el programa de esta persona.
          </p>
        </div>`;
      return;
    }

    // Si es solo lectura, mostrar advertencia
    const readOnlyWarning = !canManage ? `
      <div class="alert alert-info mb-3">
        <i class="bi bi-eye me-2" aria-hidden="true"></i>
        <strong>Modo solo lectura:</strong> Este estudiante pertenece a un programa de otro coordinador.
      </div>
    ` : '';

    docsContent.innerHTML = readOnlyWarning + documents.map(step => `
    <section class="mb-4">
      <h4 class="h6 d-flex align-items-center gap-2 mb-2">
        <span>${SIIAP.escapeHtml(step.sequence)}. ${SIIAP.escapeHtml(step.step_name)}</span>
        ${getArchiveStatusBadge(step.state)}
      </h4>
      <div class="siiap-table-wrapper">
          <table class="table siiap-table table-sm table-hover mb-0">
            <caption class="visually-hidden">Documentos de la etapa ${SIIAP.escapeHtml(step.step_name)}</caption>
            <thead class="table-light">
              <tr>
                <th scope="col">Documento</th>
                <th scope="col" class="text-center">Estado</th>
                <th scope="col">Fecha</th>
                <th scope="col">Observaciones</th>
                <th scope="col" class="text-center">Acciones</th>
              </tr>
            </thead>
            <tbody>
              ${step.archives.map(arch => `
                <tr>
                  <td>
                    <strong>${SIIAP.escapeHtml(arch.name)}</strong>
                    ${arch.uploaded_by_role === 'program_admin' ? '<i class="bi bi-person-vcard-fill text-brand-primary ms-1" title="Subido por coordinador" aria-hidden="true"></i><span class="visually-hidden">Subido por el coordinador</span>' : ''}
                  </td>
                  <td class="text-center">
                    ${getArchiveStatusBadge(arch.status)}
                  </td>
                  <td>${SIIAP.escapeHtml(SIIAP.formatDate(arch.uploaded_at, 'numeric', '-'))}</td>
                  <td>
                    <small class="text-secondary">${SIIAP.escapeHtml(arch.reviewer_comment || '-')}</small>
                  </td>
                  <td class="text-center">
                    <div class="btn-group btn-group-sm" role="group">
                      ${arch.has_submission ? `
                        <a href="${SIIAP.escapeAttr(arch.file_url)}" target="_blank" rel="noopener"
                           class="btn btn-outline-primary tap-target"
                           title="Ver documento" aria-label="Ver documento ${SIIAP.escapeAttr(arch.name)} (se abre en una pestaña nueva)">
                          <i class="bi bi-eye" aria-hidden="true"></i>
                        </a>
                      ` : ''}
                      ${canManage && arch.allow_coordinator_upload ? `
                        <button type="button" class="btn btn-outline-success btn-upload-for-modal tap-target"
                                data-student-id="${SIIAP.escapeAttr(studentId)}" data-archive-id="${SIIAP.escapeAttr(arch.id)}"
                                title="Subir documento" aria-label="Subir documento ${SIIAP.escapeAttr(arch.name)}">
                          <i class="bi bi-upload" aria-hidden="true"></i>
                        </button>
                      ` : ''}
                    </div>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
      </div>
    </section>
  `).join('');
  }

  function renderInterviewTab(interview, student, restricted) {
    const interviewContent = document.getElementById('interviewTabContent');

    // `eligible: null` no es «no cumple». Fuera de alcance la elegibilidad no
    // se calcula, y pintar la alerta amarilla afirmaría un rechazo inexistente.
    if (restricted) {
      interviewContent.innerHTML = `
        ${restrictedNotice()}
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
          <h4 class="empty-state__title">Entrevista no disponible</h4>
          <p class="empty-state__description">
            La cita de entrevista y la elegibilidad las gestiona el programa al
            que pertenece esta persona.
          </p>
        </div>`;
      return;
    }

    const eligibility = interview.eligibility || {};
    const missingItems = eligibility.missing_items || [];
    const appt = interview.appointment;
    const apptStatus = appt?.status;
    const interviewDone = apptStatus === 'done' || apptStatus === 'no_show';

    // Mapa de estado de cita → badge + color header
    const apptStatusMap = {
      scheduled: { badge: SIIAP.statusBadge('in_progress', 'Programada', 'sm'), headerBg: 'bg-primary-soft' },
      done:      { badge: SIIAP.statusBadge('approved',    'Realizada', 'sm'),  headerBg: 'bg-success-soft' },
      no_show:   { badge: SIIAP.statusBadge('rejected',    'No se presentó', 'sm'), headerBg: 'bg-danger-soft' },
    };
    const apptStyle = apptStatusMap[apptStatus]
      || { badge: SIIAP.statusBadge('pending', SIIAP.statusLabel(apptStatus), 'sm'), headerBg: 'bg-info-soft' };

    interviewContent.innerHTML = `
    <!-- Estado de Elegibilidad (solo si la entrevista no se realizó todavía) -->
    ${!interviewDone ? `
    <div class="alert ${eligibility.eligible ? 'alert-success' : 'alert-warning'} mb-4">
      <h4 class="h6 mb-2">
        <i class="bi ${eligibility.eligible ? 'bi-check-circle-fill' : 'bi-exclamation-triangle-fill'} me-2" aria-hidden="true"></i>
        Estado de elegibilidad
      </h4>
      <p class="mb-0">
        ${eligibility.eligible
          ? 'El estudiante cumple con todos los requisitos para la entrevista.'
          : 'El estudiante NO cumple con los requisitos para la entrevista.'}
      </p>
      ${!eligibility.eligible && missingItems.length > 0 ? `
        <hr>
        <p class="mb-2 small"><strong>Elementos faltantes:</strong></p>
        <ul class="small mb-0">
          ${missingItems.map(item => `
            <li>${item.type === 'profile'
              ? SIIAP.escapeHtml(item.description)
              : `${SIIAP.escapeHtml(item.step)}: ${SIIAP.escapeHtml(item.archive)} (${SIIAP.escapeHtml(item.current_status)})`}</li>
          `).join('')}
        </ul>
      ` : ''}
    </div>
    ` : ''}

    <!-- Detalle de la cita de entrevista -->
    ${interview.has_interview ? `
      <div class="card">
        <div class="card-header ${apptStyle.headerBg}">
          <h4 class="h6 mb-0">
            <i class="bi bi-calendar-check me-2" aria-hidden="true"></i>
            ${interviewDone ? 'Entrevista completada' : 'Entrevista asignada'}
          </h4>
        </div>
        <div class="card-body">
          <dl class="row g-3 mb-0">
            <div class="col-md-6">
              <dt class="small text-secondary fw-normal">Evento</dt>
              <dd class="mb-0"><strong>${SIIAP.escapeHtml(appt.event.title)}</strong></dd>
            </div>
            <div class="col-md-6">
              <dt class="small text-secondary fw-normal">Fecha y hora</dt>
              <dd class="mb-0">
                ${SIIAP.escapeHtml(SIIAP.formatDate(appt.slot.starts_at, 'long', '—'))}
                <small class="d-block text-secondary">
                  ${SIIAP.escapeHtml(SIIAP.formatTime(appt.slot.starts_at, '—'))} – ${SIIAP.escapeHtml(SIIAP.formatTime(appt.slot.ends_at, '—'))}
                </small>
              </dd>
            </div>
            <div class="col-md-6">
              <dt class="small text-secondary fw-normal">Lugar</dt>
              <dd class="mb-0">${SIIAP.escapeHtml(appt.event.location || 'Por confirmar')}</dd>
            </div>
            <div class="col-md-6">
              <dt class="small text-secondary fw-normal">Estado</dt>
              <dd class="mb-0">${apptStyle.badge}</dd>
            </div>
            ${appt.notes ? `
              <div class="col-12">
                <dt class="small text-secondary fw-normal">Notas</dt>
                <dd class="mb-0 small">${SIIAP.escapeHtml(appt.notes)}</dd>
              </div>
            ` : ''}
          </dl>
        </div>
      </div>
    ` : `
      <div class="alert alert-info">
        <i class="bi bi-info-circle-fill me-2"></i>
        El estudiante no tiene entrevista asignada aún.
        ${eligibility.eligible ? ' Sin embargo, cumple con los requisitos y puede ser asignado.' : ''}
      </div>
    `}
  `;
  }

  // Funciones auxiliares
  function getStepStateName(state) {
    const names = {
      'approved': 'Completo',
      'rejected': 'Rechazado',
      'pending': 'Pendiente',
      'extended': 'En Prórroga',
      'review': 'En Revisión'
    };
    return names[state] || state;
  }

  function getArchiveStatusBadge(status) {
    const map = {
      'approved': ['approved',     'Aprobado'],
      'rejected': ['rejected',     'Rechazado'],
      'pending':  ['pending',      'Pendiente'],
      'review':   ['review',       'En revisión'],
      'extended': ['deliberation', 'En prórroga'],
    };
    const [key, label] = map[status] || ['pending', SIIAP.statusLabel(status)];
    return SIIAP.statusBadge(key, label, 'sm');
  }
  function openUploadModal(studentId) {
    const student = studentsData.find(s => s.id == studentId);
    if (!student) return;

    // Pre-seleccionar estudiante en el modal
    document.getElementById('targetStudent').value = studentId;
    document.getElementById('targetProgram').value = student.program_name;
    loadStudentArchives(studentId);

    const modal = new bootstrap.Modal(document.getElementById('uploadForStudentModal'));
    modal.show();
  }

  // ==================== SUBIDA POR COORDINADOR ====================
  function setupCoordinatorUpload() {
    const form = document.querySelector('form[data-coordinator-upload="true"]');
    if (!form) return;

    const studentSelect = document.getElementById('targetStudent');
    const archiveSelect = document.getElementById('targetArchive');
    const programInput = document.getElementById('targetProgram');

    // Cargar lista de estudiantes
    loadStudentsList();

    // Auto-clear estado inválido del textarea cuando el coordinador empieza a escribir
    // o adjunta archivo
    const notesEl = document.getElementById('coordinatorNotes');
    const fileEl = document.getElementById('coordinatorFile');
    notesEl?.addEventListener('input', () => notesEl.classList.remove('is-invalid'));
    fileEl?.addEventListener('change', () => notesEl?.classList.remove('is-invalid'));

    // Cambio de estudiante -> cargar archivos
    studentSelect.addEventListener('change', (e) => {
      const studentId = e.target.value;
      if (studentId) {
        const student = studentsData.find(s => s.id == studentId);
        programInput.value = student ? student.program_name : '';
        loadStudentArchives(studentId);
      } else {
        programInput.value = '';
        archiveSelect.innerHTML = '<option value="">Primero selecciona un estudiante</option>';
        archiveSelect.disabled = true;
      }
    });

    // Submit del formulario
    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const formData = new FormData(form);
      const studentId = formData.get('student_id');
      const archiveId = formData.get('archive_id');
      const fileRaw = formData.get('file');
      const file = (fileRaw && fileRaw.name) ? fileRaw : null;
      const notes = (formData.get('notes') || '').trim();
      const notesEl = document.getElementById('coordinatorNotes');

      // Reset visual error
      notesEl?.classList.remove('is-invalid');

      if (!studentId || !archiveId) {
        emitFlash('warning', 'Selecciona estudiante y archivo destino');
        return;
      }

      // Comentario obligatorio si no hay archivo
      if (!file && !notes) {
        notesEl?.classList.add('is-invalid');
        notesEl?.focus();
        emitFlash('warning', 'Si no adjuntas archivo, el comentario es obligatorio');
        return;
      }

      // Validar archivo solo si fue provisto
      if (file) {
        if (file.size > 3 * 1024 * 1024) {
          emitFlash('danger', 'El archivo no puede superar los 3MB');
          return;
        }
        if (!file.name.toLowerCase().endsWith('.pdf')) {
          emitFlash('danger', 'Solo se permiten archivos PDF');
          return;
        }
      } else {
        // No mandar campo file vacío al backend
        formData.delete('file');
      }

      const submitBtn = form.querySelector('button[type="submit"]');
      const originalText = submitBtn.innerHTML;
      submitBtn.innerHTML = '<i class="bi bi-arrow-repeat bi-spin"></i> Subiendo...';
      submitBtn.disabled = true;

      try {
        const res = await fetch('/api/v1/coordinator/upload-for-student', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrf },
          body: formData
        });

        const json = await res.json().catch(() => ({}));

        if (!res.ok) {
          emitFlash('danger', json.error || 'No se pudo subir el documento');
          return;
        }

        emitFlash('success', json.message || 'Documento registrado exitosamente por coordinador');

        // Cerrar modal y recargar datos
        const modal = bootstrap.Modal.getInstance(document.getElementById('uploadForStudentModal'));
        modal.hide();
        form.reset();
        loadStudents();

      } catch (err) {
        console.error('Upload error:', err);
        emitFlash('danger', 'Error de red al subir documento');
      } finally {
        submitBtn.innerHTML = originalText;
        submitBtn.disabled = false;
      }
    });
  }

  async function loadStudentsList() {
    try {
      const res = await fetch('/api/v1/coordinator/manageable-students', {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('No se pudieron cargar estudiantes');

      const data = await res.json();
      const select = document.getElementById('targetStudent');

      select.innerHTML = '<option value="">Seleccionar estudiante...</option>' +
        data.students.map(s =>
          `<option value="${SIIAP.escapeAttr(s.id)}">${SIIAP.escapeHtml(s.full_name)} - ${SIIAP.escapeHtml(s.program_name)}</option>`
        ).join('');

    } catch (err) {
      console.error('Error loading students list:', err);
    }
  }

  async function loadStudentArchives(studentId) {
    try {
      const res = await fetch(`/api/v1/coordinator/student/${encodeURIComponent(studentId)}/uploadable-archives`, {
        credentials: 'same-origin'
      });

      if (!res.ok) throw new Error('No se pudieron cargar archivos');

      const data = await res.json();
      const select = document.getElementById('targetArchive');

      if (data.archives.length === 0) {
        select.innerHTML = '<option value="">No hay archivos disponibles para subir</option>';
        select.disabled = true;
      } else {
        select.innerHTML = '<option value="">Seleccionar archivo...</option>' +
          data.archives.map(a =>
            `<option value="${SIIAP.escapeAttr(a.id)}">${SIIAP.escapeHtml(a.name)} (${SIIAP.escapeHtml(a.step_name)})</option>`
          ).join('');
        select.disabled = false;
      }

    } catch (err) {
      console.error('Error loading archives:', err);
      const select = document.getElementById('targetArchive');
      select.innerHTML = '<option value="">Error al cargar archivos</option>';
      select.disabled = true;
    }
  }

  // ==================== UTILIDADES ====================
  function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  }

  // ==================== MODAL DE PERMANENCIA ====================

  // Estado del modal para acciones internas
  let _permCurrentStudentId = null;
  let _permCurrentUserProgramId = null;
  let _permCurrentConacyt = false;

  async function viewPermanenceDetails(studentId) {
    _permCurrentStudentId = parseInt(studentId);

    // Resetear modal
    document.getElementById('permModalAvatar').src = '/static/assets/images/default.jpg';
    document.getElementById('permModalName').textContent = 'Cargando...';
    document.getElementById('permModalEmail').textContent = '';
    document.getElementById('permModalProgram').textContent = '';
    document.getElementById('permModalControlNumber').textContent = '';
    document.getElementById('permModalSpinner').classList.remove('d-none');
    document.getElementById('permModalContent').classList.add('d-none');

    const modal = new bootstrap.Modal(document.getElementById('permanenceDetailsModal'));
    modal.show();

    try {
      const res = await fetch(`/api/v1/coordinator/student/${encodeURIComponent(studentId)}/permanence-details`, {
        credentials: 'same-origin'
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Error al cargar');
      renderPermanenceModal(data);
    } catch (err) {
      document.getElementById('permModalSpinner').innerHTML = `
        <div class="alert alert-danger">
          <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>No se pudo cargar el detalle: ${SIIAP.escapeHtml(err.message)}
        </div>`;
    }
  }

  function renderPermanenceModal(data) {
    const { student, user_program, program, active_period, current_enrollment,
            pending_admission_count, semester_history, can_manage } = data;
    const restricted = data.restricted === true || can_manage === false;

    _permCurrentUserProgramId = user_program.id;
    _permCurrentConacyt = user_program.has_conacyt_scholarship === true;

    // Header
    document.getElementById('permModalAvatar').src = student.avatar_url || '/static/assets/images/default.jpg';
    document.getElementById('permModalName').textContent = student.full_name;
    document.getElementById('permModalEmail').textContent = student.email;
    document.getElementById('permModalProgram').textContent = (program && program.name) || '—';
    // Fuera de alcance el número de control no se envía: «(sin N° control)»
    // afirmaría que no tiene, que es distinto de no poder verlo.
    document.getElementById('permModalControlNumber').textContent = restricted
      ? '—'
      : (student.control_number || '(sin N° control)');

    // Alerta docs admisión pendientes
    const admAlert = document.getElementById('permAdmissionAlert');
    if (pending_admission_count > 0) {
      document.getElementById('permAdmissionCount').textContent = pending_admission_count;
      admAlert.classList.remove('d-none');
      document.getElementById('permOpenAdmissionBtn').onclick = () => {
        bootstrap.Modal.getInstance(document.getElementById('permanenceDetailsModal'))?.hide();
        viewStudentDetails(_permCurrentStudentId);
      };
    } else {
      admAlert.classList.add('d-none');
    }

    // Botón "Ver expediente completo"
    document.getElementById('permOpenFullBtn').onclick = () => {
      bootstrap.Modal.getInstance(document.getElementById('permanenceDetailsModal'))?.hide();
      viewStudentDetails(_permCurrentStudentId);
    };

    // ── Tarjetas resumen ──────────────────────────────────────────
    const statusEnrollment = current_enrollment
      ? (current_enrollment.enrollment_confirmed
          ? [SIIAP.statusBadge('approved', 'Confirmada', 'sm'), 'bi-check-circle-fill']
          : [SIIAP.statusBadge('pending', 'Pendiente', 'sm'), 'bi-hourglass-split'])
      : [SIIAP.statusBadge('pending', 'Sin registro', 'sm'), 'bi-dash-circle'];

    document.getElementById('permSummaryCards').innerHTML = `
      ${restricted ? `<div class="col-12">${restrictedNotice()}</div>` : ''}
      <div class="col-6 col-md-4">
        <div class="stat-card stat-card--brand h-100">
          <i class="bi bi-mortarboard-fill stat-card__icon" aria-hidden="true"></i>
          <p class="stat-card__value">${SIIAP.escapeHtml(user_program.current_semester)}</p>
          <p class="stat-card__label">Semestre actual</p>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="stat-card stat-card--info h-100">
          <i class="bi bi-calendar-event-fill stat-card__icon" aria-hidden="true"></i>
          <p class="kpi-value kpi-value--sm">${active_period ? SIIAP.escapeHtml(active_period.name) : '—'}</p>
          <p class="stat-card__label">${active_period ? SIIAP.escapeHtml(active_period.code) : 'Sin periodo activo'}</p>
        </div>
      </div>
      <div class="col-12 col-md-4">
        <div class="stat-card h-100">
          <i class="bi ${statusEnrollment[1]} stat-card__icon" aria-hidden="true"></i>
          <p class="mb-0">${statusEnrollment[0]}</p>
          <p class="stat-card__label">Inscripción semestral</p>
        </div>
      </div>
    `;

    // ── SECIHTI ────────────────────────────────────────────────────
    const badge = document.getElementById('permConacytBadge');
    const switchEl = document.getElementById('permConacytSwitch');
    const toggleWrap = document.getElementById('permConacytToggleWrap');

    // `has_conacyt_scholarship: null` (fuera de alcance) no es «sin beca».
    badge.innerHTML = restricted
      ? SIIAP.statusBadge('pending', 'Beca SECIHTI no disponible')
      : (_permCurrentConacyt
          ? SIIAP.statusBadge('approved', 'Becario SECIHTI')
          : SIIAP.statusBadge('pending', 'Sin beca SECIHTI'));
    switchEl.checked = _permCurrentConacyt;
    toggleWrap.classList.toggle('d-none', !can_manage);

    switchEl.onchange = () => {
      // Doble confirmación: capturamos el deseo y pedimos confirmación
      const desired = switchEl.checked;
      // Revertir visualmente hasta que confirme
      switchEl.checked = _permCurrentConacyt;
      switchEl.disabled = true;

      const studentName = document.getElementById('permModalName')?.textContent || 'estudiante';
      document.getElementById('confirmConacytStudent').textContent = studentName;
      document.getElementById('confirmConacytAction').textContent =
        desired ? 'Activar beca SECIHTI' : 'Quitar beca SECIHTI';

      const modalEl = document.getElementById('confirmConacytModal');
      const modal = new bootstrap.Modal(modalEl);

      const onConfirm = async () => {
        bootstrap.Modal.getInstance(modalEl)?.hide();
        await toggleConacytScholarship(_permCurrentUserProgramId, desired);
        switchEl.disabled = false;
      };
      const onCancel = () => { switchEl.disabled = false; };

      // Listeners one-shot
      const confirmBtn = document.getElementById('btnConfirmConacyt');
      const cancelBtn  = document.getElementById('btnCancelConacyt');
      const confirmHandler = () => { confirmBtn.removeEventListener('click', confirmHandler); onConfirm(); };
      const cancelHandler  = () => { cancelBtn.removeEventListener('click', cancelHandler); onCancel(); };
      confirmBtn.addEventListener('click', confirmHandler);
      cancelBtn.addEventListener('click', cancelHandler);
      modalEl.addEventListener('hidden.bs.modal', () => { switchEl.disabled = false; }, { once: true });

      modal.show();
    };

    // ── Inscripción semestral detalle ──────────────────────────────
    const enrollBody = document.getElementById('permEnrollmentBody');
    if (!active_period) {
      enrollBody.innerHTML = '<p class="text-muted mb-0">No hay periodo académico activo.</p>';
    } else if (!current_enrollment) {
      enrollBody.innerHTML = `
        <div class="d-flex align-items-center gap-2 flex-wrap">
          ${SIIAP.statusBadge('pending', 'Pendiente de confirmación', 'sm')}
          <span class="small text-secondary">El estudiante no tiene inscripción registrada para el periodo activo.</span>
        </div>`;
    } else {
      const confirmedAt = SIIAP.formatDate(current_enrollment.confirmed_at, 'short', '');
      enrollBody.innerHTML = `
        <div class="row g-2 align-items-center">
          <div class="col-auto">${semesterStatusBadge(current_enrollment.status)}</div>
          ${current_enrollment.enrollment_confirmed
            ? `<div class="col-auto small text-secondary">Confirmada${confirmedAt ? ' el ' + SIIAP.escapeHtml(confirmedAt) : ''}</div>`
            : `<div class="col-auto small text-secondary">Pendiente de confirmación por el coordinador</div>`}
          ${current_enrollment.notes
            ? `<div class="col-12"><small class="text-secondary fst-italic">"${SIIAP.escapeHtml(current_enrollment.notes)}"</small></div>`
            : ''}
        </div>`;
    }

    // ── Historial ─────────────────────────────────────────────────
    const histContainer = document.getElementById('permHistoryContent');
    if (!semester_history.length) {
      histContainer.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <h4 class="empty-state__title">Sin historial semestral</h4>
          <p class="empty-state__description">Aún no se registran semestres para este estudiante.</p>
        </div>`;
    } else {
      histContainer.innerHTML = `
        <div class="siiap-table-wrapper">
        <table class="table siiap-table table-sm align-middle mb-0">
          <caption class="visually-hidden">Historial de semestres cursados por el estudiante</caption>
          <thead class="table-light">
            <tr>
              <th scope="col" class="text-center">Semestre</th>
              <th scope="col">Periodo</th>
              <th scope="col" class="text-center">Estado</th>
              <th scope="col" class="text-center">Confirmado</th>
            </tr>
          </thead>
          <tbody>
            ${semester_history.map(h => {
              const confirmedIcon = h.enrollment_confirmed
                ? '<i class="bi bi-check-circle-fill text-success-strong" aria-hidden="true"></i><span class="visually-hidden">Confirmado</span>'
                : '<i class="bi bi-dash-circle text-secondary" aria-hidden="true"></i><span class="visually-hidden">Sin confirmar</span>';
              return `
                <tr>
                  <td class="text-center fw-bold">Sem. ${SIIAP.escapeHtml(h.semester_number)}</td>
                  <td>
                    ${SIIAP.escapeHtml(h.period_name)}
                    <span class="badge bg-secondary ms-1">${SIIAP.escapeHtml(h.period_code)}</span>
                  </td>
                  <td class="text-center">${semesterStatusBadge(h.status)}</td>
                  <td class="text-center">${confirmedIcon}</td>
                </tr>`;
            }).join('')}
          </tbody>
        </table>
        </div>`;
    }

    // Mostrar contenido
    document.getElementById('permModalSpinner').classList.add('d-none');
    document.getElementById('permModalContent').classList.remove('d-none');
  }

  async function toggleConacytScholarship(userProgramId, newValue) {
    try {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const res = await fetch(`/api/v1/permanence/user-program/${userProgramId}/conacyt-scholarship`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ value: newValue }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => emitFlash(f.level, f.message));
      if (res.ok && !json.error) {
        _permCurrentConacyt = json.data.has_conacyt_scholarship;
        const badge = document.getElementById('permConacytBadge');
        badge.innerHTML = _permCurrentConacyt
          ? SIIAP.statusBadge('approved', 'Becario SECIHTI')
          : SIIAP.statusBadge('pending', 'Sin beca SECIHTI');
        // Actualizar también en la tabla principal
        loadStudents();
      }
    } catch (err) {
      emitFlash('danger', 'Error al actualizar beca SECIHTI');
    }
  }

});