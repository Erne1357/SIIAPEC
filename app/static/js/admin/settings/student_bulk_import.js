/**
 * app/static/js/admin/settings/student_bulk_import.js
 *
 * Handles Alta Masiva de Estudiantes page logic:
 *   - Individual form: validate + create
 *   - CSV: template download, preview, execute
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Constants / helpers
  // ---------------------------------------------------------------------------

  const API_BASE = '/api/v1/student-bulk';

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  /**
   * POST JSON to an endpoint, handle flash array from response.
   * Returns parsed `data` on success, null on error.
   */
  async function postJson(url, payload) {
    let res;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify(payload),
      });
    } catch (networkErr) {
      showFlash('danger', 'Error de red. Verifica tu conexión e intenta de nuevo.');
      return null;
    }

    let body;
    try {
      body = await res.json();
    } catch {
      showFlash('danger', 'Respuesta inesperada del servidor.');
      return null;
    }

    if (body.flash && Array.isArray(body.flash)) {
      body.flash.forEach(f => showFlash(f.level, f.message));
    }

    if (!res.ok) {
      if (!body.flash) {
        const msg = body.error && body.error.message
          ? body.error.message
          : 'Error inesperado del servidor.';
        showFlash('danger', msg);
      }
      return null;
    }

    return body.data;
  }

  /**
   * POST multipart/form-data to an endpoint.
   * Returns parsed `data` on success, null on error.
   */
  async function postFormData(url, formData) {
    let res;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() },
        body: formData,
      });
    } catch (networkErr) {
      showFlash('danger', 'Error de red. Verifica tu conexión e intenta de nuevo.');
      return null;
    }

    let body;
    try {
      body = await res.json();
    } catch {
      showFlash('danger', 'Respuesta inesperada del servidor.');
      return null;
    }

    if (body.flash && Array.isArray(body.flash)) {
      body.flash.forEach(f => showFlash(f.level, f.message));
    }

    if (!res.ok) {
      if (!body.flash) {
        const msg = body.error && body.error.message
          ? body.error.message
          : 'Error inesperado del servidor.';
        showFlash('danger', msg);
      }
      return null;
    }

    return body.data;
  }

  // ---------------------------------------------------------------------------
  // Individual form helpers
  // ---------------------------------------------------------------------------

  /** Collect current form values into a payload object. */
  function collectIndividualPayload() {
    return {
      first_name: document.getElementById('firstName').value.trim(),
      last_name: document.getElementById('lastName').value.trim(),
      mother_last_name: document.getElementById('motherLastName').value.trim(),
      email: document.getElementById('email').value.trim(),
      control_number: document.getElementById('controlNumber').value.trim(),
      program_slug: document.getElementById('programSlug').value,
      admission_period_code: document.getElementById('admissionPeriodCode').value,
      current_semester: parseInt(document.getElementById('currentSemester').value, 10) || null,
      has_conacyt: document.getElementById('hasConacyt').checked,
    };
  }

  /**
   * Render validation feedback in the individual form's alert area.
   * @param {boolean} valid
   * @param {string[]} errors
   */
  function renderIndividualValidation(valid, errors) {
    const area = document.getElementById('individualValidationArea');
    const msgs = document.getElementById('individualValidationMessages');

    area.classList.remove('d-none');

    if (valid) {
      msgs.innerHTML = `
        <div class="alert alert-success mb-0">
          <i class="bi bi-check-circle-fill me-2"></i>
          Todos los datos son válidos. Puedes proceder a crear el estudiante.
        </div>`;
    } else {
      const items = errors.map(e => `<li>${SIIAP.escapeHtml(e)}</li>`).join('');
      msgs.innerHTML = `
        <div class="alert alert-danger mb-0">
          <strong><i class="bi bi-exclamation-triangle-fill me-2"></i>
          Errores de validación:</strong>
          <ul class="mb-0 mt-2">${items}</ul>
        </div>`;
    }
  }

  /** Clear the individual validation area and reset create button. */
  function resetIndividualValidation() {
    const area = document.getElementById('individualValidationArea');
    area.classList.add('d-none');
    document.getElementById('btnCreateIndividual').disabled = true;
  }

  // ---------------------------------------------------------------------------
  // Individual: validate handler
  // ---------------------------------------------------------------------------

  async function handleValidateIndividual() {
    resetIndividualValidation();

    const payload = collectIndividualPayload();
    if (!payload.first_name || !payload.last_name || !payload.mother_last_name ||
        !payload.email || !payload.control_number || !payload.program_slug ||
        !payload.admission_period_code || !payload.current_semester) {
      renderIndividualValidation(false, ['Completa todos los campos obligatorios.']);
      return;
    }

    const btn = document.getElementById('btnValidateIndividual');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Validando...';

    const data = await postJson(`${API_BASE}/validate`, payload);

    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-check2-circle me-1"></i>Validar';

    if (!data) return; // errors already shown via showFlash

    renderIndividualValidation(data.valid, data.errors || []);

    if (data.valid) {
      document.getElementById('btnCreateIndividual').disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Individual: create handler
  // ---------------------------------------------------------------------------

  async function handleCreateIndividual() {
    const payload = collectIndividualPayload();

    const btn = document.getElementById('btnCreateIndividual');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creando...';

    const data = await postJson(`${API_BASE}/create`, payload);

    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-person-check-fill me-1"></i>Crear Estudiante';

    if (!data) return;

    showFlash(
      'success',
      `Estudiante creado exitosamente. ID de usuario: ${data.user_id}. ` +
      `Se generaron ${data.sems_created} semestre(s). ` +
      `Se envió correo de bienvenida con enlace para configurar contraseña.`
    );

    // Reset form
    document.getElementById('individualForm').reset();
    resetIndividualValidation();
  }

  // ---------------------------------------------------------------------------
  // CSV: template download handler
  // ---------------------------------------------------------------------------

  function handleDownloadTemplate() {
    window.location.href = `${API_BASE}/csv/template`;
  }

  // ---------------------------------------------------------------------------
  // CSV: preview (validate) — render helper
  // ---------------------------------------------------------------------------

  /** Render the preview table from the rows array returned by the API. */
  function renderCsvPreview(previewData) {
    const { rows, summary } = previewData;

    // Update summary bar
    const validCount = summary.valid;
    const totalCount = summary.total;
    const invalidCount = summary.invalid;

    document.getElementById('summaryValid').textContent = `${validCount} válidas`;
    document.getElementById('summaryTotal').textContent = `${totalCount} total`;

    const invalidBadge = document.getElementById('summaryInvalid');
    if (invalidCount > 0) {
      invalidBadge.textContent = `${invalidCount} con errores`;
      invalidBadge.classList.remove('d-none');
    } else {
      invalidBadge.classList.add('d-none');
    }

    // Render table body
    const tbody = document.getElementById('csvPreviewBody');
    if (!rows || rows.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="12" class="text-center text-muted py-3">
            El archivo CSV está vacío o no contiene filas de datos.
          </td>
        </tr>`;
      return;
    }

    tbody.innerHTML = rows.map(row => {
      const d = row.data || {};
      const rowClass = row.valid ? '' : 'csv-row-invalid';
      const statusBadge = row.valid
        ? '<span class="status-badge status-badge--accepted status-badge--sm">' +
          '<i class="bi bi-check-circle-fill" aria-hidden="true"></i><span>Válida</span></span>'
        : '<span class="status-badge status-badge--rejected status-badge--sm">' +
          '<i class="bi bi-x-circle-fill" aria-hidden="true"></i><span>Con errores</span></span>';

      const errorsHtml = row.valid
        ? '<span class="text-muted">—</span>'
        : `<ul class="mb-0 ps-3 text-danger small">${
            (row.errors || []).map(e => `<li>${SIIAP.escapeHtml(e)}</li>`).join('')
          }</ul>`;

      const conacytText = d.has_conacyt ? 'Sí' : 'No';

      return `<tr class="${rowClass}">
        <th scope="row" class="text-center fw-normal">${SIIAP.escapeHtml(row.index)}</th>
        <td>${statusBadge}</td>
        <td>${SIIAP.escapeHtml(d.first_name || '')}</td>
        <td>${SIIAP.escapeHtml(d.last_name || '')}</td>
        <td>${SIIAP.escapeHtml(d.mother_last_name || '')}</td>
        <td>${SIIAP.escapeHtml(d.email || '')}</td>
        <td>${SIIAP.escapeHtml(d.control_number || '')}</td>
        <td>${SIIAP.escapeHtml(d.program_slug || '')}</td>
        <td class="text-center">${d.current_semester != null ? SIIAP.escapeHtml(d.current_semester) : '—'}</td>
        <td>${SIIAP.escapeHtml(d.admission_period_code || '')}</td>
        <td class="text-center">${conacytText}</td>
        <td>${errorsHtml}</td>
      </tr>`;
    }).join('');

    // Show section
    document.getElementById('csvPreviewSection').classList.remove('d-none');

    // Enable execute button only if there are valid rows
    document.getElementById('btnExecuteCsv').disabled = (validCount === 0);
  }

  // ---------------------------------------------------------------------------
  // CSV: execute
  // ---------------------------------------------------------------------------

  async function handleExecuteCsv() {
    const previewData = _cachedPreviewRows;
    if (!previewData || previewData.length === 0) {
      showFlash('warning', 'No hay filas válidas para aplicar.');
      return;
    }

    const validRows = previewData.filter(row => row.valid);

    if (validRows.length === 0) {
      showFlash('warning', 'No hay filas válidas para aplicar.');
      return;
    }

    const btn = document.getElementById('btnExecuteCsv');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Aplicando...';

    const data = await postJson(`${API_BASE}/csv/execute`, { rows: validRows });

    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-cloud-upload me-1"></i>Aplicar válidas';

    if (!data) return;

    renderCsvResult(data);
  }

  // ---------------------------------------------------------------------------
  // CSV: render result modal
  // ---------------------------------------------------------------------------

  function renderCsvResult(data) {
    const { created, failed, created_users } = data;

    document.getElementById('resultCreatedCount').textContent = created || 0;
    document.getElementById('resultFailedCount').textContent = (failed && failed.length) || 0;

    // Created list
    const createdSection = document.getElementById('resultCreatedSection');
    const createdList = document.getElementById('resultCreatedList');
    if (created_users && created_users.length > 0) {
      createdList.innerHTML = created_users.map(u => `
        <tr>
          <td>${SIIAP.escapeHtml(u.user_id)}</td>
          <td>${SIIAP.escapeHtml(u.email)}</td>
          <td>${SIIAP.escapeHtml(u.control_number)}</td>
        </tr>`).join('');
      createdSection.classList.remove('d-none');
    } else {
      createdSection.classList.add('d-none');
    }

    // Failed list
    const failedSection = document.getElementById('resultFailedSection');
    const failedList = document.getElementById('resultFailedList');
    if (failed && failed.length > 0) {
      failedList.innerHTML = failed.map(f => `
        <tr>
          <td>${SIIAP.escapeHtml(f.index)}</td>
          <td>${SIIAP.escapeHtml(f.email || '—')}</td>
          <td class="text-danger">${SIIAP.escapeHtml(f.error || '—')}</td>
        </tr>`).join('');
      failedSection.classList.remove('d-none');
    } else {
      failedSection.classList.add('d-none');
    }

    // Show modal
    const modal = new bootstrap.Modal(document.getElementById('csvResultModal'));
    modal.show();

    // Flash summary
    if (created > 0) {
      showFlash('success', `Se crearon ${created} estudiante(s) exitosamente.`);
    }
    if (failed && failed.length > 0) {
      showFlash('warning', `${failed.length} registro(s) fallaron. Revisa el reporte.`);
    }
  }

  // ---------------------------------------------------------------------------
  // Module-level cache for preview rows (used by execute)
  // ---------------------------------------------------------------------------
  let _cachedPreviewRows = null;

  // Wrap postFormData to also cache preview rows
  async function handleValidateCsvWithCache() {
    _cachedPreviewRows = null;

    const fileInput = document.getElementById('csvFileInput');
    if (!fileInput.files || fileInput.files.length === 0) {
      showFlash('warning', 'Selecciona un archivo CSV antes de validar.');
      return;
    }

    const btn = document.getElementById('btnValidateCsv');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Validando...';

    document.getElementById('csvPreviewSection').classList.add('d-none');
    document.getElementById('btnExecuteCsv').disabled = true;

    const formData = new FormData();
    formData.append('csv_file', fileInput.files[0]);

    const data = await postFormData(`${API_BASE}/csv/preview`, formData);

    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-search me-1"></i>Validar CSV';

    if (!data) return;

    _cachedPreviewRows = data.rows || [];
    renderCsvPreview(data);
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  function init() {
    const btnValidateInd = document.getElementById('btnValidateIndividual');
    const btnCreateInd = document.getElementById('btnCreateIndividual');
    const btnDownloadTpl = document.getElementById('btnDownloadTemplate');
    const btnValidateCsv = document.getElementById('btnValidateCsv');
    const btnExecuteCsv = document.getElementById('btnExecuteCsv');

    if (btnValidateInd) {
      btnValidateInd.addEventListener('click', handleValidateIndividual);
    }
    if (btnCreateInd) {
      btnCreateInd.addEventListener('click', handleCreateIndividual);
    }
    if (btnDownloadTpl) {
      btnDownloadTpl.addEventListener('click', handleDownloadTemplate);
    }
    if (btnValidateCsv) {
      btnValidateCsv.addEventListener('click', handleValidateCsvWithCache);
    }
    if (btnExecuteCsv) {
      btnExecuteCsv.addEventListener('click', handleExecuteCsv);
    }

    // Reset validation state whenever any individual field changes
    const individualFields = [
      'firstName', 'lastName', 'motherLastName', 'email',
      'controlNumber', 'programSlug', 'admissionPeriodCode',
      'currentSemester', 'hasConacyt',
    ];
    individualFields.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('input', resetIndividualValidation);
        el.addEventListener('change', resetIndividualValidation);
      }
    });

    // Reset cached CSV when a new file is selected
    const csvInput = document.getElementById('csvFileInput');
    if (csvInput) {
      csvInput.addEventListener('change', () => {
        _cachedPreviewRows = null;
        document.getElementById('csvPreviewSection').classList.add('d-none');
        document.getElementById('btnExecuteCsv').disabled = true;
      });
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
