// app/static/js/admin/settings/document_templates.js
(() => {
  const API   = '/api/admin/document-templates';
  const USERS = '/api/v1/admin/users';
  const AP    = '/api/v1/academic-periods';
  const PROGS = '/api/v1/programs';
  const IS_ADMIN = window.IS_POSTGRAD_ADMIN;

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

  const alertsEl  = document.getElementById('alerts');
  const tbodyTpl  = document.getElementById('tbodyTemplates');
  const tbodyVars = document.getElementById('tbodyVariables');

  // ── Filtros ──────────────────────────────────────────────────────────────
  const filterDocType    = document.getElementById('filterDocType');
  const filterActiveOnly = document.getElementById('filterActiveOnly');
  document.getElementById('btnRefresh').addEventListener('click', loadTemplates);
  filterDocType.addEventListener('change', loadTemplates);
  filterActiveOnly.addEventListener('change', loadTemplates);

  // ── Utilidades ───────────────────────────────────────────────────────────
  const DOC_TYPE_LABELS = {
    acceptance_letter:      'Carta de Aceptación',
    enrollment_confirmation:'Confirmación de Inscripción',
    course_schedule:        'Tira de Materias',
  };

  function flash(msg, type = 'success') {
    const div = document.createElement('div');
    div.className = `alert alert-${type} alert-dismissible fade show`;
    div.innerHTML = `${SIIAP.escapeHtml(msg)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    alertsEl.prepend(div);
    setTimeout(() => bootstrap.Alert.getOrCreateInstance(div)?.close(), 6000);
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    if (window.SIIAP && window.SIIAP.formatDate) return window.SIIAP.formatDate(iso, 'short');
    return new Date(iso).toLocaleDateString('es-MX', { day:'2-digit', month:'short', year:'numeric' });
  }

  // ── Cargar tabla de plantillas ────────────────────────────────────────────
  async function loadTemplates() {
    tbodyTpl.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">
      <div class="spinner-border spinner-border-sm me-2" role="status"></div>Cargando…</td></tr>`;

    const params = new URLSearchParams();
    if (filterDocType.value)          params.set('document_type', filterDocType.value);
    if (filterActiveOnly.checked)     params.set('active_only', 'true');

    try {
      const res  = await fetch(`${API}?${params}`, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Error al cargar plantillas');
      renderTemplates(data.data || []);
    } catch (e) {
      tbodyTpl.innerHTML = `<tr><td colspan="6">
        <div class="empty-state empty-state--inline empty-state--error">
          <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
          <p class="empty-state__title">No se pudieron cargar las plantillas</p>
          <p class="empty-state__description">Vuelve a intentarlo en unos segundos.</p>
          <p class="empty-state__error-detail">${SIIAP.escapeHtml(e.message)}</p>
        </div>
      </td></tr>`;
    }
  }

  function renderTemplates(list) {
    if (!list.length) {
      tbodyTpl.innerHTML = `<tr><td colspan="6">
        <div class="empty-state empty-state--inline">
          <div class="empty-state__icon"><i class="bi bi-file-earmark-text" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin plantillas registradas</p>
          ${IS_ADMIN
            ? '<p class="empty-state__description">Usa el botón «Subir plantilla» para agregar la primera.</p>'
            : '<p class="empty-state__description">Solicita a Administración de posgrado que cargue una plantilla.</p>'}
        </div>
      </td></tr>`;
      return;
    }

    tbodyTpl.innerHTML = list.map(t => {
      const label   = SIIAP.escapeHtml(DOC_TYPE_LABELS[t.document_type] || t.document_type);
      const program = t.program_id
        ? SIIAP.escapeHtml(t.program_name || `Programa #${t.program_id}`)
        : '<span class="text-muted">Global</span>';
      const badge   = t.file_type === 'html'
        ? '<span class="badge bg-info-soft">HTML a PDF</span>'
        : '<span class="badge bg-primary-soft">DOCX</span>';
      const active  = t.is_active
        ? '<span class="status-badge status-badge--accepted status-badge--sm">' +
          '<i class="bi bi-check-circle-fill" aria-hidden="true"></i><span>Activa</span></span>'
        : '<span class="status-badge status-badge--deferred status-badge--sm">' +
          '<i class="bi bi-pause-circle-fill" aria-hidden="true"></i><span>Inactiva</span></span>';

      const nameAttr = SIIAP.escapeAttr(t.name);

      const adminBtns = IS_ADMIN ? `
        <button type="button" class="btn btn-sm btn-outline-secondary btn-edit ms-1 tap-target"
          data-id="${SIIAP.escapeAttr(t.id)}" data-name="${nameAttr}" data-desc="${SIIAP.escapeAttr(t.description || '')}"
          data-active="${SIIAP.escapeAttr(t.is_active)}"
          aria-label="Editar la plantilla ${nameAttr}" title="Editar la plantilla ${nameAttr}">
          <i class="bi bi-pencil" aria-hidden="true"></i>
        </button>` : '';

      return `<tr>
        <th scope="row" class="fw-normal">
          <span class="fw-semibold d-block">${SIIAP.escapeHtml(t.name)}</span>
          ${t.description ? `<span class="text-muted small d-block">${SIIAP.escapeHtml(t.description)}</span>` : ''}
          <span class="text-muted small d-block">Subida: ${SIIAP.escapeHtml(fmtDate(t.created_at))}</span>
        </th>
        <td>${label}</td>
        <td>${program}</td>
        <td class="text-center">${badge}</td>
        <td class="text-center">${active}</td>
        <td class="text-end text-nowrap">
          <button type="button" class="btn btn-sm btn-outline-primary btn-generate"
            data-id="${SIIAP.escapeAttr(t.id)}" data-type="${SIIAP.escapeAttr(t.document_type)}" data-name="${nameAttr}"
            title="Generar un documento con la plantilla ${nameAttr}">
            <i class="bi bi-download me-1" aria-hidden="true"></i>Generar
          </button>
          ${adminBtns}
        </td>
      </tr>`;
    }).join('');
  }

  // ── Clic en tabla ─────────────────────────────────────────────────────────
  tbodyTpl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button');
    if (!btn) return;

    if (btn.classList.contains('btn-generate')) {
      openGenerateModal(btn.dataset.id, btn.dataset.type, btn.dataset.name);
    } else if (btn.classList.contains('btn-edit')) {
      openEditModal(btn.dataset);
    }
  });

  // ── Cargar variables ──────────────────────────────────────────────────────
  async function loadVariables() {
    try {
      const res  = await fetch(`${API}/variables`, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.ok) return;
      tbodyVars.innerHTML = (data.data || []).map(v =>
        `<tr><td><code>${SIIAP.escapeHtml(v.key)}</code></td><td class="text-muted">${SIIAP.escapeHtml(v.description)}</td></tr>`
      ).join('');
    } catch (_) { /* silencioso */ }
  }

  // ── Cargar programas (para el select de upload) ───────────────────────────
  async function loadPrograms() {
    if (!IS_ADMIN) return;
    const sel = document.getElementById('uploadProgramId');
    if (!sel) return;
    try {
      const res  = await fetch(`${PROGS}/`, { credentials: 'same-origin' });
      const data = await res.json();
      const list = data.programs || data.data || [];
      list.forEach(p => {
        const opt = new Option(p.name, p.id);
        sel.add(opt);
      });
    } catch (_) { /* silencioso */ }
  }

  // ── Cargar periodos (para el select de generar) ───────────────────────────
  async function loadPeriods() {
    const sel = document.getElementById('genPeriodId');
    try {
      const res  = await fetch(`${AP}`, { credentials: 'same-origin' });
      const data = await res.json();
      const list = data.data || data.periods || [];
      list.forEach(p => {
        const opt = new Option(p.name, p.id);
        sel.add(opt);
      });
    } catch (_) { /* silencioso */ }
  }

  // ── Modal: Subir plantilla ────────────────────────────────────────────────
  if (IS_ADMIN) {
    document.getElementById('formUpload')?.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const file = document.getElementById('uploadFile').files[0];
      const name = document.getElementById('uploadName').value.trim();
      const type = document.getElementById('uploadDocType').value;

      if (!file || !name || !type) {
        flash('Completa los campos obligatorios.', 'warning');
        return;
      }

      const spinner = document.getElementById('spinnerUpload');
      const btn     = document.getElementById('btnUploadSubmit');
      spinner.classList.remove('d-none');
      btn.disabled = true;

      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', name);
      fd.append('document_type', type);
      const programId = document.getElementById('uploadProgramId').value;
      if (programId) fd.append('program_id', programId);
      const desc = document.getElementById('uploadDescription').value.trim();
      if (desc) fd.append('description', desc);

      try {
        const res  = await fetch(API, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrfToken() },
          body: fd,
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Error al subir');
        flash('Plantilla subida exitosamente.');
        bootstrap.Modal.getInstance(document.getElementById('modalUpload'))?.hide();
        document.getElementById('formUpload').reset();
        await loadTemplates();
      } catch (e) {
        flash(e.message, 'danger');
      } finally {
        spinner.classList.add('d-none');
        btn.disabled = false;
      }
    });

    // ── Modal: Editar plantilla ─────────────────────────────────────────────
    function openEditModal({ id, name, desc, active }) {
      document.getElementById('editId').value       = id;
      document.getElementById('editName').value     = name;
      document.getElementById('editDescription').value = desc || '';
      document.getElementById('editIsActive').checked  = active === 'true';
      bootstrap.Modal.getOrCreateInstance(document.getElementById('modalEdit')).show();
    }

    document.getElementById('formEdit')?.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const id = document.getElementById('editId').value;
      const body = {
        name:        document.getElementById('editName').value.trim(),
        description: document.getElementById('editDescription').value.trim() || null,
        is_active:   document.getElementById('editIsActive').checked,
      };
      try {
        const res  = await fetch(`${API}/${id}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Error al actualizar');
        flash('Plantilla actualizada.');
        bootstrap.Modal.getInstance(document.getElementById('modalEdit'))?.hide();
        await loadTemplates();
      } catch (e) {
        flash(e.message, 'danger');
      }
    });

    document.getElementById('btnDeleteTemplate')?.addEventListener('click', async () => {
      const id   = document.getElementById('editId').value;
      const name = document.getElementById('editName').value;
      const ok = await siiapConfirm({
        type: 'danger',
        title: 'Eliminar plantilla',
        message: `¿Eliminar la plantilla "${name}"? Esta acción no se puede deshacer.`,
        confirmLabel: 'Sí, eliminar',
      });
      if (!ok) return;
      try {
        const res  = await fetch(`${API}/${id}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrfToken() },
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'Error al eliminar');
        flash('Plantilla eliminada.', 'warning');
        bootstrap.Modal.getInstance(document.getElementById('modalEdit'))?.hide();
        await loadTemplates();
      } catch (e) {
        flash(e.message, 'danger');
      }
    });
  }

  // ── Modal: Generar documento ──────────────────────────────────────────────
  function openGenerateModal(templateId, docType, templateName) {
    document.getElementById('genDocType').value     = docType;
    document.getElementById('genTemplateName').textContent = templateName;
    document.getElementById('btnGenerateConfirm').dataset.templateId = templateId;

    // Limpiar estado previo
    clearStudentSelection();
    document.getElementById('genStudentSearch').value = '';
    document.getElementById('genError').classList.add('d-none');

    bootstrap.Modal.getOrCreateInstance(document.getElementById('modalGenerate')).show();
  }

  // Búsqueda de estudiantes con debounce
  let searchTimer = null;
  const searchInput   = document.getElementById('genStudentSearch');
  const resultsBox    = document.getElementById('genStudentResults');

  searchInput?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (q.length < 2) { resultsBox.innerHTML = ''; return; }
    searchTimer = setTimeout(() => searchStudents(q), 320);
  });

  async function searchStudents(q) {
    resultsBox.innerHTML = `<a class="list-group-item list-group-item-action disabled text-muted small">Buscando…</a>`;
    try {
      const res  = await fetch(
        `${USERS}/?role=student&search=${encodeURIComponent(q)}&per_page=10`,
        { credentials: 'same-origin' }
      );
      const data = await res.json();
      const users = data.data?.users || data.users || [];
      if (!users.length) {
        resultsBox.innerHTML = `<a class="list-group-item list-group-item-action disabled text-muted small">Sin resultados</a>`;
        return;
      }
      resultsBox.innerHTML = users.map(u => {
        const prog = u.program;
        const progText = prog ? ` — ${prog.name}` : '';
        const fullName = `${u.first_name} ${u.last_name}`;
        return `<a class="list-group-item list-group-item-action small" href="#"
          data-user-id="${SIIAP.escapeAttr(u.id)}"
          data-program-id="${SIIAP.escapeAttr(prog?.id || '')}"
          data-label="${SIIAP.escapeAttr(`${fullName} (${u.email})`)}">
          <strong>${SIIAP.escapeHtml(fullName)}</strong>
          <span class="text-muted">${SIIAP.escapeHtml(`${u.email}${progText}`)}</span>
        </a>`;
      }).join('');
    } catch (_) {
      resultsBox.innerHTML = `<a class="list-group-item disabled text-danger small">Error al buscar</a>`;
    }
  }

  resultsBox?.addEventListener('click', (ev) => {
    ev.preventDefault();
    const item = ev.target.closest('[data-user-id]');
    if (!item) return;
    selectStudent(item.dataset.userId, item.dataset.programId, item.dataset.label);
  });

  function selectStudent(userId, programId, label) {
    document.getElementById('genUserId').value    = userId;
    document.getElementById('genProgramId').value = programId;
    document.getElementById('genStudentBadge').textContent = label;
    document.getElementById('genStudentSelected').classList.remove('d-none');
    searchInput.classList.add('d-none');
    resultsBox.innerHTML = '';
  }

  function clearStudentSelection() {
    document.getElementById('genUserId').value    = '';
    document.getElementById('genProgramId').value = '';
    document.getElementById('genStudentSelected').classList.add('d-none');
    searchInput.classList.remove('d-none');
  }

  document.getElementById('btnClearStudent')?.addEventListener('click', () => {
    clearStudentSelection();
    searchInput.value = '';
    searchInput.focus();
  });

  // Cerrar resultados al hacer clic fuera
  document.addEventListener('click', (ev) => {
    if (!resultsBox.contains(ev.target) && ev.target !== searchInput) {
      resultsBox.innerHTML = '';
    }
  });

  document.getElementById('btnGenerateConfirm')?.addEventListener('click', async () => {
    const userId    = document.getElementById('genUserId').value;
    const programId = document.getElementById('genProgramId').value;
    const docType   = document.getElementById('genDocType').value;
    const periodId  = document.getElementById('genPeriodId').value;
    const errEl     = document.getElementById('genError');

    if (!userId || !programId) {
      errEl.textContent = 'Selecciona un estudiante de la lista.';
      errEl.classList.remove('d-none');
      return;
    }
    errEl.classList.add('d-none');

    const spinner = document.getElementById('spinnerGenerate');
    const btn     = document.getElementById('btnGenerateConfirm');
    spinner.classList.remove('d-none');
    btn.disabled = true;

    try {
      // `user_id` es el identificador público del estudiante (UUID) y viaja
      // como cadena; `program_id` y `period_id` siguen siendo enteros porque
      // esos modelos no tienen handle público.
      const body = { user_id: userId, program_id: Number(programId), document_type: docType };
      if (periodId) body.period_id = Number(periodId);

      const res = await fetch(`${API}/generate`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Error del servidor (${res.status})`);
      }

      // Descargar el archivo
      const blob        = await res.blob();
      const disposition = res.headers.get('Content-Disposition') || '';
      const match       = disposition.match(/filename="?([^"]+)"?/);
      const filename    = match ? match[1] : 'documento.pdf';

      const url  = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href  = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      flash('Documento generado y descargado exitosamente.');
      bootstrap.Modal.getInstance(document.getElementById('modalGenerate'))?.hide();
    } catch (e) {
      errEl.textContent = e.message;
      errEl.classList.remove('d-none');
    } finally {
      spinner.classList.add('d-none');
      btn.disabled = false;
    }
  });

  // Limpiar búsqueda al cerrar el modal
  document.getElementById('modalGenerate')?.addEventListener('hidden.bs.modal', () => {
    resultsBox.innerHTML = '';
    clearStudentSelection();
    document.getElementById('genStudentSearch').value = '';
    document.getElementById('genError').classList.add('d-none');
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  loadTemplates();
  loadVariables();
  loadPrograms();
  loadPeriods();
})();
