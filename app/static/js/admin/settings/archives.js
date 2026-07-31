(() => {
  const API = "/api/v1";
  const tblBody = document.getElementById("tbodyArchives");
  const alerts = document.getElementById("alerts");
  const search = document.getElementById("search");
  const btnReload = document.getElementById("btnReload");
  const btnNew = document.getElementById("btnNew");

  // Modal plantilla
  const modalTpl = new bootstrap.Modal(document.getElementById("modalTemplate"));
  const formTpl = document.getElementById("formTemplate");
  const tplArchiveId = document.getElementById("tplArchiveId");
  const tplFile = document.getElementById("tplFile");

  // Modal editar/crear
  const modalEdit = new bootstrap.Modal(document.getElementById("modalEdit"));
  const formEdit = document.getElementById("formEdit");
  // El título lo emite el macro modal_shell con el id "<modalId>Title".
  const editTitle = document.getElementById("modalEditTitle");
  const editId = document.getElementById("editId");
  const editName = document.getElementById("editName");
  const editDesc = document.getElementById("editDesc");
  const editStep = document.getElementById("editStep");
  const editIsUploadable = document.getElementById("editIsUploadable");
  const editIsDownloadable = document.getElementById("editIsDownloadable");
  const editAllowCoord = document.getElementById("editAllowCoord");
  const editAllowExt = document.getElementById("editAllowExt");

  // Modal eliminar
  const modalDel = new bootstrap.Modal(document.getElementById("modalDelete"));
  const formDel = document.getElementById("formDelete");
  const delId = document.getElementById("delId");
  const delForce = document.getElementById("delForce");

  let cached = [];
  let steps = [];

  // Función para disparar flash usando el sistema existente
  function flash(msg, type = "success") {
    window.dispatchEvent(new CustomEvent('flash', { 
      detail: { level: type, message: msg } 
    }));
  }

  // Función helper para obtener CSRF token
  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  // Validación de archivos
  function validateFile(file) {
    const maxSize = 3 * 1024 * 1024; // 3MB
    const allowedTypes = ['application/pdf'];
    
    if (!file) {
      return { valid: false, error: 'No se ha seleccionado ningún archivo' };
    }
    
    if (!allowedTypes.includes(file.type) && !file.name.toLowerCase().endsWith('.pdf')) {
      return { valid: false, error: 'Solo se permiten archivos PDF' };
    }
    
    if (file.size > maxSize) {
      return { valid: false, error: `El archivo excede el límite de 3MB (actual: ${(file.size / (1024 * 1024)).toFixed(2)}MB)` };
    }
    
    return { valid: true };
  }

  // Función helper para hacer peticiones con manejo de errores mejorado
  async function apiRequest(url, options = {}) {
    const defaultHeaders = {
      'X-CSRFToken': getCsrfToken()
    };

    // Solo agregar Content-Type para JSON, no para FormData
    if (options.body && typeof options.body === 'string') {
      defaultHeaders['Content-Type'] = 'application/json';
    }

    const defaultOptions = {
      credentials: "same-origin",
      headers: {
        ...defaultHeaders,
        ...options.headers
      }
    };

    const finalOptions = { ...defaultOptions, ...options };

    try {
      const response = await fetch(url, finalOptions);
      
      // Verificar si la respuesta es HTML (redirección o error de permisos)
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('text/html')) {
        throw new Error('No tienes permisos para realizar esta acción o la sesión ha expirado');
      }

      let data;
      try {
        data = await response.json();
      } catch (jsonError) {
        throw new Error('Error al procesar la respuesta del servidor');
      }

      if (!response.ok) {
        throw new Error(data.error || data.message || `Error HTTP ${response.status}`);
      }

      if (data.ok === false) {
        throw new Error(data.error || 'Operación fallida');
      }

      return { response, data };
    } catch (error) {
      console.error('API Request Error:', error);
      throw error;
    }
  }

  function stepName(step_id) {
    const s = steps.find(x => x.id === step_id);
    return s ? `${s.name} · ${s.phase_name}` : "";
  }

  function rowTemplate(a) {
    const tplUrl = a.template_url ? 
      `<a class="template-link" href="${a.template_url}" target="_blank" rel="noopener" title="${a.template_name || 'Descargar'}">${a.template_name || 'Ver plantilla'}</a>` : 
      `<span class="text-muted">—</span>`;
    const stepLabel = a.step_name || stepName(a.step_id) || "";
    
    const label = a.name || 'este archivo';

    return `
      <tr data-id="${a.id}" data-name="${(a.name||'').toLowerCase()}" data-step="${(stepLabel||'').toLowerCase()}">
        <th scope="row" class="fw-normal">
          <span class="fw-semibold d-block">${a.name}</span>
          <span class="text-muted small">${a.description||''}</span>
        </th>
        <td>${stepLabel}</td>
        <td class="toggle-cell">
          <input class="form-check-input chk-uploadable" type="checkbox"
                 aria-label="El alumno sube ${label}" ${a.is_uploadable ? 'checked':''}>
        </td>
        <td class="toggle-cell">
          <input class="form-check-input chk-downloadable" type="checkbox"
                 aria-label="${label} es descargable" ${a.is_downloadable ? 'checked':''}>
        </td>
        <td class="toggle-cell">
          <input class="form-check-input chk-allow-coord" type="checkbox"
                 aria-label="El coordinador puede subir ${label}" ${a.allow_coordinator_upload ? 'checked':''}>
        </td>
        <td class="toggle-cell">
          <input class="form-check-input chk-allow-ext" type="checkbox"
                 aria-label="${label} permite solicitar prórroga" ${a.allow_extension_request ? 'checked':''}>
        </td>
        <td>${tplUrl}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm" role="group" aria-label="Acciones de ${label}">
            <button type="button" class="btn btn-outline-secondary btn-edit tap-target"
                    aria-label="Editar ${label}" title="Editar ${label}">
              <i class="bi bi-pencil-square" aria-hidden="true"></i>
            </button>
            <button type="button" class="btn btn-outline-primary btn-upload-template tap-target"
                    aria-label="Subir plantilla de ${label}" title="Subir plantilla de ${label}">
              <i class="bi bi-upload" aria-hidden="true"></i>
            </button>
            <button type="button" class="btn btn-success btn-save tap-target"
                    aria-label="Guardar cambios de ${label}" title="Guardar cambios de ${label}">
              <i class="bi bi-save" aria-hidden="true"></i>
            </button>
            <button type="button" class="btn btn-outline-danger btn-delete tap-target"
                    aria-label="Eliminar ${label}" title="Eliminar ${label}">
              <i class="bi bi-trash" aria-hidden="true"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }

  async function loadSteps() {
    try {
      const { data } = await apiRequest(`${API}/archives/steps?scope=permitted`);
      steps = data.items || [];
      
      // Llenar select de steps para crear/editar
      editStep.innerHTML = steps.map(s => 
        `<option value="${s.id}">${s.name} (${s.phase_name})</option>`
      ).join('');
    } catch (err) {
      console.error('Error loading steps:', err);
      flash(`Error cargando steps: ${err.message}`, 'danger');
    }
  }

  async function loadArchives() {
    tblBody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-4">Cargando archivos…</td></tr>`;
    try {
      const { data } = await apiRequest(`${API}/archives?include=step`);
      cached = data.items || [];
      render();
    } catch (err) {
      console.error('Error loading archives:', err);
      tblBody.innerHTML = `
        <tr>
          <td colspan="8">
            <div class="empty-state empty-state--inline empty-state--error">
              <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
              <p class="empty-state__title">No se pudieron cargar los archivos</p>
              <p class="empty-state__description">Vuelve a intentarlo; si el problema persiste, avisa a soporte.</p>
              <p class="empty-state__error-detail">${err.message}</p>
            </div>
          </td>
        </tr>`;
      flash(`Error cargando archivos: ${err.message}`, 'danger');
    }
  }

  function render() {
    const q = (search.value || "").trim().toLowerCase();
    const items = !q ? cached : cached.filter(a =>
      (a.name || '').toLowerCase().includes(q) ||
      (a.step_name || '').toLowerCase().includes(q)
    );
    
    if (!items.length) {
      tblBody.innerHTML = `
        <tr>
          <td colspan="8">
            <div class="empty-state empty-state--inline">
              <div class="empty-state__icon"><i class="bi bi-search" aria-hidden="true"></i></div>
              <p class="empty-state__title">Sin archivos que coincidan</p>
              <p class="empty-state__description">Ningún archivo cumple la búsqueda actual.</p>
            </div>
          </td>
        </tr>`;
      return;
    }

    tblBody.innerHTML = items.map(rowTemplate).join("");
    if (window.SIIAP && typeof window.SIIAP.announce === 'function') {
      window.SIIAP.announce(
        items.length === 1 ? '1 archivo listado.' : `${items.length} archivos listados.`
      );
    }
  }

  // ========= Eventos globales =========
  btnReload?.addEventListener("click", async () => { 
    await loadSteps(); 
    await loadArchives(); 
  });
  
  search?.addEventListener("input", render);

  btnNew?.addEventListener("click", () => {
    editTitle.textContent = "Nuevo archivo";
    editId.value = "";
    editName.value = "";
    editDesc.value = "";
    editIsUploadable.checked = false;
    editIsDownloadable.checked = false;
    editAllowCoord.checked = false;
    editAllowExt.checked = false;
    if (steps.length) editStep.value = steps[0].id;
    modalEdit.show();
  });

  tblBody?.addEventListener("click", async (ev) => {
    const tr = ev.target.closest("tr");
    if (!tr) return;
    const id = tr.getAttribute("data-id");
    if (!id) return;

    if (ev.target.closest(".btn-upload-template")) {
      tplArchiveId.value = id;
      tplFile.value = "";
      modalTpl.show();
      return;
    }

    if (ev.target.closest(".btn-save")) {
      const body = {
        is_uploadable: tr.querySelector(".chk-uploadable").checked,
        is_downloadable: tr.querySelector(".chk-downloadable").checked,
        allow_coordinator_upload: tr.querySelector(".chk-allow-coord").checked,
        allow_extension_request: tr.querySelector(".chk-allow-ext").checked
      };
      
      try {
        await apiRequest(`${API}/archives/${id}`, {
          method: "PUT",
          body: JSON.stringify(body)
        });
        
        flash("Configuración guardada exitosamente", "success");
        await loadArchives();
      } catch (err) {
        flash(`Error al guardar: ${err.message}`, "danger");
      }
      return;
    }

    if (ev.target.closest(".btn-edit")) {
      const row = cached.find(x => String(x.id) === String(id));
      if (!row) return;
      
      editTitle.textContent = "Editar archivo";
      editId.value = row.id;
      editName.value = row.name || "";
      editDesc.value = row.description || "";
      editIsUploadable.checked = !!row.is_uploadable;
      editIsDownloadable.checked = !!row.is_downloadable;
      editAllowCoord.checked = !!row.allow_coordinator_upload;
      editAllowExt.checked = !!row.allow_extension_request;
      editStep.value = row.step_id;
      modalEdit.show();
      return;
    }

    if (ev.target.closest(".btn-delete")) {
      delId.value = id;
      delForce.checked = false;
      modalDel.show();
      return;
    }
  });

  // ========= Form plantilla =========
  formTpl?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = tplArchiveId.value;
    
    if (!tplFile.files.length) { 
      flash("Selecciona un archivo", "warning"); 
      return; 
    }
    
    // Validar archivo
    const validation = validateFile(tplFile.files[0]);
    if (!validation.valid) {
      flash(validation.error, "danger");
      return;
    }
    
    const fd = new FormData();
    fd.append("file", tplFile.files[0]);
    
    try {
      const { data } = await apiRequest(`${API}/archives/${id}/template`, {
        method: "POST",
        body: fd
      });
      
      flash("Plantilla actualizada exitosamente", "success");
      modalTpl.hide();
      await loadArchives();
    } catch (err) {
      flash(`Error subiendo plantilla: ${err.message}`, "danger");
    }
  });

  // Validación en tiempo real para el input de archivo de plantilla
  tplFile?.addEventListener("change", (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    
    const validation = validateFile(file);
    const feedback = ev.target.parentElement.querySelector('.file-feedback') || 
                     document.createElement('div');
    
    if (!ev.target.parentElement.querySelector('.file-feedback')) {
      feedback.className = 'file-feedback small mt-1';
      ev.target.parentElement.appendChild(feedback);
    }
    
    if (!validation.valid) {
      feedback.className = 'file-feedback small mt-1 text-danger';
      feedback.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> ${validation.error}`;
    } else {
      feedback.className = 'file-feedback small mt-1 text-success';
      feedback.innerHTML = `<i class="bi bi-check-lg"></i> Archivo válido (${(file.size / (1024 * 1024)).toFixed(2)}MB)`;
    }
  });

  // ========= Form editar/crear =========
  formEdit?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    
    const body = {
      name: editName.value.trim(),
      description: editDesc.value.trim(),
      step_id: Number(editStep.value),
      is_uploadable: editIsUploadable.checked,
      is_downloadable: editIsDownloadable.checked,
      allow_coordinator_upload: editAllowCoord.checked,
      allow_extension_request: editAllowExt.checked
    };
    
    if (!body.name) {
      flash("El nombre es requerido", "warning");
      return;
    }
    
    try {
      const isEdit = !!editId.value;
      const url = isEdit ? `${API}/archives/${editId.value}` : `${API}/archives`;
      const method = isEdit ? "PUT" : "POST";
      
      await apiRequest(url, {
        method,
        body: JSON.stringify(body)
      });
      
      flash(`Archivo ${isEdit ? 'actualizado' : 'creado'} exitosamente`, "success");
      modalEdit.hide();
      await loadArchives();
    } catch (err) {
      flash(`Error al guardar: ${err.message}`, "danger");
    }
  });

  // ========= Form eliminar =========
  formDel?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = delId.value;
    const force = delForce.checked ? "?force=true" : "";
    
    try {
      const response = await fetch(`${API}/archives/${id}${force}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          'X-CSRFToken': getCsrfToken()
        }
      });

      // Verificar si es HTML en lugar de JSON
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('text/html')) {
        throw new Error('No tienes permisos para realizar esta acción');
      }

      const data = await response.json();
      
      if (response.status === 409 && data.requires_force) {
        flash(data.message || "Tiene submissions; marca 'Eliminar forzado' para continuar", "warning");
        return;
      }
      
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "No se pudo eliminar");
      }
      
      flash("Archivo eliminado exitosamente", "success");
      modalDel.hide();
      await loadArchives();
    } catch (err) {
      flash(`Error al eliminar: ${err.message}`, "danger");
    }
  });

  // Inicialización
  (async () => {
    try {
      await loadSteps();
      await loadArchives();
    } catch (err) {
      flash(`Error inicializando la página: ${err.message}`, 'danger');
    }
  })();
})();