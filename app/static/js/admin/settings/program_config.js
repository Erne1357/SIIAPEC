// app/static/js/admin/settings/program_config.js
(function() {
  'use strict';

  // ============================================
  // FUNCIONES PARA LISTAS DINÁMICAS (Objetivos y Competencias)
  // ============================================

  function addObjective() {
    const container = document.getElementById('objectivesList');
    const count = container.children.length + 1;
    const div = document.createElement('div');
    div.className = 'list-item';
    div.innerHTML = `
      <input type="text" class="form-control" placeholder="Objetivo ${count}"
             aria-label="Objetivo ${count}">
      <button type="button" class="btn btn-outline-danger btn-sm tap-target"
              data-action="remove-list-item"
              aria-label="Eliminar el objetivo ${count}" title="Eliminar objetivo">
        <i class="bi bi-trash" aria-hidden="true"></i>
      </button>
    `;
    container.appendChild(div);
    updateObjectivesPreview();
  }

  function addCompetency() {
    const container = document.getElementById('competenciesList');
    const count = container.children.length + 1;
    const div = document.createElement('div');
    div.className = 'list-item';
    div.innerHTML = `
      <input type="text" class="form-control" placeholder="Competencia ${count}"
             aria-label="Competencia ${count}">
      <button type="button" class="btn btn-outline-danger btn-sm tap-target"
              data-action="remove-list-item"
              aria-label="Eliminar la competencia ${count}" title="Eliminar competencia">
        <i class="bi bi-trash" aria-hidden="true"></i>
      </button>
    `;
    container.appendChild(div);
    updateProfilePreview();
  }

  function removeListItem(button) {
    button.parentElement.remove();
    updateObjectivesPreview();
    updateProfilePreview();
  }

  // Delegación por data-action: sustituye a los onclick en línea de la
  // plantilla y no requiere exponer nada en window (compatible con CSP).
  const ACTIONS = {
    'add-objective': addObjective,
    'add-competency': addCompetency,
    'remove-list-item': removeListItem,
  };

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-action]');
    if (!trigger) return;
    const handler = ACTIONS[trigger.dataset.action];
    if (handler) handler(trigger);
  });

  function updateObjectivesPreview() {
    const inputs = document.querySelectorAll('#objectivesList input');
    const preview = document.getElementById('objectivesPreview');
    preview.innerHTML = '';
    inputs.forEach(input => {
      if (input.value.trim()) {
        const li = document.createElement('li');
        li.textContent = input.value;
        preview.appendChild(li);
      }
    });
  }

  function updateProfilePreview() {
    const introInput = document.querySelector('[name="graduate_profile_intro"]');
    if (!introInput) return;

    const intro = introInput.value;
    const inputs = document.querySelectorAll('#competenciesList input');
    const introPreview = document.getElementById('profileIntroPreview');
    const preview = document.getElementById('competenciesPreview');

    if (introPreview) {
      introPreview.textContent = intro || 'El egresado de este programa será capaz de:';
    }

    if (preview) {
      preview.innerHTML = '';
      inputs.forEach(input => {
        if (input.value.trim()) {
          const li = document.createElement('li');
          li.textContent = input.value;
          preview.appendChild(li);
        }
      });
    }
  }

  // ============================================
  // EDITOR DE PLAN DE ESTUDIOS (CURRICULUM)
  // ============================================

  let curriculumData = {
    type: 'semestral',
    semesters: []
  };

  window.addSemester = function() {
    const semesterNumber = curriculumData.semesters.length + 1;
    curriculumData.semesters.push({
      semester: semesterNumber,
      courses: []
    });
    renderCurriculumEditor();
  };

  window.removeSemester = async function(index) {
    const ok = await siiapConfirm({
      type: 'danger',
      title: 'Eliminar semestre',
      message: '¿Eliminar este semestre y todas sus materias?',
      confirmLabel: 'Sí, eliminar',
    });
    if (!ok) return;
    curriculumData.semesters.splice(index, 1);
    // Reindexar semestres
    curriculumData.semesters.forEach((sem, idx) => {
      sem.semester = idx + 1;
    });
    renderCurriculumEditor();
  };

  window.addCourse = function(semesterIndex) {
    curriculumData.semesters[semesterIndex].courses.push({
      name: '',
      code: '',
      credits: '',
      type: 'obligatoria'
    });
    renderCurriculumEditor();
  };

  window.removeCourse = function(semesterIndex, courseIndex) {
    curriculumData.semesters[semesterIndex].courses.splice(courseIndex, 1);
    renderCurriculumEditor();
  };

  function renderCurriculumEditor() {
    const container = document.getElementById('curriculumEditorContainer');
    if (!container) return;

    if (curriculumData.semesters.length === 0) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-book-half" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin semestres configurados</p>
          <p class="empty-state__description">
            Agrega el primer semestre para empezar a construir el mapa curricular.
          </p>
          <div class="empty-state__actions">
            <button type="button" class="btn btn-primary" onclick="addSemester()">
              <i class="bi bi-plus-lg me-2" aria-hidden="true"></i>Agregar el primer semestre
            </button>
          </div>
        </div>
      `;
      return;
    }

    let html = '<div class="accordion accordion-curriculum" id="curriculumAccordion">';

    curriculumData.semesters.forEach((semester, semIdx) => {
      // Defensa: si semester es null/undefined (sparse array, JSON malformado),
      // se sustituye por un placeholder mínimo para que el render no rompa.
      if (!semester || typeof semester !== 'object') {
        semester = { semester: semIdx + 1, courses: [] };
        curriculumData.semesters[semIdx] = semester;
      }
      if (!Array.isArray(semester.courses)) semester.courses = [];
      if (!Number.isInteger(semester.semester) || semester.semester <= 0) {
        semester.semester = semIdx + 1;
      }
      const collapseId = `collapse-sem-${semIdx}`;
      const isFirst = semIdx === 0;

      html += `
        <div class="accordion-item">
          <h2 class="accordion-header" id="heading-${semIdx}">
            <button class="accordion-button ${isFirst ? '' : 'collapsed'}" type="button"
                    data-bs-toggle="collapse" data-bs-target="#${collapseId}">
              Semestre ${semester.semester}
              <span class="badge bg-secondary ms-2">${(semester.courses || []).length} ${(semester.courses || []).length === 1 ? 'materia' : 'materias'}</span>
            </button>
          </h2>
          <div id="${collapseId}" class="accordion-collapse collapse ${isFirst ? 'show' : ''}"
               data-bs-parent="#curriculumAccordion">
            <div class="accordion-body">
              <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
                <h3 class="h6 mb-0">Materias del semestre ${semester.semester}</h3>
                <div class="btn-group btn-group-sm">
                  <button type="button" class="btn btn-outline-primary" onclick="addCourse(${semIdx})">
                    <i class="bi bi-plus-lg me-1" aria-hidden="true"></i>Agregar materia
                  </button>
                  <button type="button" class="btn btn-outline-danger" onclick="removeSemester(${semIdx})">
                    <i class="bi bi-trash me-1" aria-hidden="true"></i>Eliminar semestre
                  </button>
                </div>
              </div>
      `;

      const courses = semester.courses || [];
      if (courses.length === 0) {
        html += `
          <div class="empty-state empty-state--compact">
            <div class="empty-state__icon"><i class="bi bi-journal-x" aria-hidden="true"></i></div>
            <p class="empty-state__title">Sin materias en este semestre</p>
          </div>`;
      } else {
        courses.forEach((course, courseIdx) => {
          html += `
            <div class="course-item">
              <input type="text" class="form-control form-control-sm"
                     placeholder="Nombre de la materia"
                     aria-label="Nombre de la materia ${courseIdx + 1} del semestre ${semester.semester}"
                     value="${course.name || ''}"
                     onchange="updateCourse(${semIdx}, ${courseIdx}, 'name', this.value)">
              <input type="text" class="form-control form-control-sm"
                     placeholder="Código"
                     aria-label="Código de la materia ${courseIdx + 1} del semestre ${semester.semester}"
                     value="${course.code || ''}"
                     onchange="updateCourse(${semIdx}, ${courseIdx}, 'code', this.value)">
              <input type="number" class="form-control form-control-sm"
                     placeholder="Créditos"
                     aria-label="Créditos de la materia ${courseIdx + 1} del semestre ${semester.semester}"
                     value="${course.credits || ''}"
                     onchange="updateCourse(${semIdx}, ${courseIdx}, 'credits', this.value)">
              <select class="form-select form-select-sm"
                      aria-label="Tipo de la materia ${courseIdx + 1} del semestre ${semester.semester}"
                      onchange="updateCourse(${semIdx}, ${courseIdx}, 'type', this.value)">
                <option value="obligatoria" ${course.type === 'obligatoria' ? 'selected' : ''}>Obligatoria</option>
                <option value="optativa" ${course.type === 'optativa' ? 'selected' : ''}>Optativa</option>
                <option value="electiva" ${course.type === 'electiva' ? 'selected' : ''}>Electiva</option>
              </select>
              <button type="button" class="btn btn-sm btn-outline-danger tap-target"
                      onclick="removeCourse(${semIdx}, ${courseIdx})"
                      aria-label="Eliminar la materia ${courseIdx + 1} del semestre ${semester.semester}"
                      title="Eliminar materia">
                <i class="bi bi-trash" aria-hidden="true"></i>
              </button>
            </div>
          `;
        });
      }

      html += `
            </div>
          </div>
        </div>
      `;
    });

    html += '</div>';

    // Botón para agregar más semestres
    html += `
      <div class="text-center mt-3">
        <button type="button" class="btn btn-outline-primary" onclick="addSemester()">
          <i class="bi bi-plus-lg me-2" aria-hidden="true"></i>Agregar semestre ${curriculumData.semesters.length + 1}
        </button>
      </div>
    `;

    container.innerHTML = html;
  }

  window.updateCourse = function(semesterIndex, courseIndex, field, value) {
    curriculumData.semesters[semesterIndex].courses[courseIndex][field] = value;
  };

  // ============================================
  // EDITOR DE LÍNEAS DE INVESTIGACIÓN
  // ============================================

  let researchLinesData = [];

  window.addResearchLine = function() {
    researchLinesData.push({
      name: '',
      description: ''
    });
    renderResearchEditor();
  };

  window.removeResearchLine = async function(index) {
    const ok = await siiapConfirm({
      type: 'danger',
      title: 'Eliminar línea de investigación',
      message: '¿Eliminar esta línea de investigación?',
      confirmLabel: 'Sí, eliminar',
    });
    if (!ok) return;
    researchLinesData.splice(index, 1);
    renderResearchEditor();
  };

  window.updateResearchLine = function(index, field, value) {
    researchLinesData[index][field] = value;
  };

  function renderResearchEditor() {
    const container = document.getElementById('researchEditorContainer');
    if (!container) return;

    if (researchLinesData.length === 0) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-eyedropper" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin líneas de investigación</p>
          <p class="empty-state__description">
            Agrega la primera línea para que aparezca en la página pública del programa.
          </p>
          <div class="empty-state__actions">
            <button type="button" class="btn btn-primary" onclick="addResearchLine()">
              <i class="bi bi-plus-lg me-2" aria-hidden="true"></i>Agregar la primera línea
            </button>
          </div>
        </div>
      `;
      return;
    }

    let html = '';
    researchLinesData.forEach((line, idx) => {
      html += `
        <div class="research-line-card">
          <div class="research-line-header">
            <div class="research-line-content">
              <div class="mb-2">
                <label class="form-label fw-semibold" for="researchLineName-${idx}">Nombre de la línea</label>
                <input type="text" class="form-control" id="researchLineName-${idx}"
                       placeholder="Ej: Inteligencia artificial y aprendizaje automático"
                       value="${line.name || ''}"
                       onchange="updateResearchLine(${idx}, 'name', this.value)">
              </div>
              <div>
                <label class="form-label fw-semibold" for="researchLineDesc-${idx}">Descripción</label>
                <textarea class="form-control" id="researchLineDesc-${idx}" rows="3"
                          placeholder="Descripción de la línea de investigación…"
                          onchange="updateResearchLine(${idx}, 'description', this.value)">${line.description || ''}</textarea>
              </div>
            </div>
            <div class="research-line-actions">
              <button type="button" class="btn btn-outline-danger btn-sm tap-target"
                      onclick="removeResearchLine(${idx})"
                      aria-label="Eliminar la línea de investigación ${idx + 1}"
                      title="Eliminar línea de investigación">
                <i class="bi bi-trash" aria-hidden="true"></i>
              </button>
            </div>
          </div>
        </div>
      `;
    });

    html += `
      <div class="text-center mt-3">
        <button type="button" class="btn btn-outline-primary" onclick="addResearchLine()">
          <i class="bi bi-plus-lg me-2" aria-hidden="true"></i>Agregar línea de investigación
        </button>
      </div>
    `;

    container.innerHTML = html;
  }

  // ============================================
  // INICIALIZACIÓN Y SUBMIT
  // ============================================

  function initializeEditors(programData) {
    // Inicializar curriculum
    if (programData.curriculum_structure) {
      try {
        curriculumData = typeof programData.curriculum_structure === 'string'
          ? JSON.parse(programData.curriculum_structure)
          : programData.curriculum_structure;

        // Validar estructura
        if (!curriculumData || typeof curriculumData !== 'object') {
          curriculumData = { type: 'semestral', semesters: [] };
        }
        if (!curriculumData.type) curriculumData.type = 'semestral';
        if (!Array.isArray(curriculumData.semesters)) curriculumData.semesters = [];
        // Normalizar cada semestre: filtrar null/undefined; garantizar que tenga
        // courses array y un número de semestre válido (>0). Datos legacy podían
        // tener 0/null/undefined o arrays sparse.
        //
        // El formato antiguo guardaba {number, name, subjects:["Materia", …]}.
        // Antes esta normalización dejaba courses:[] y descartaba subjects, así
        // que abrir esta pantalla y guardar borraba el plan de estudios entero
        // sin avisar. Ahora las materias antiguas se migran a courses.
        curriculumData.semesters = curriculumData.semesters
          .filter(sem => sem && typeof sem === 'object')
          .map((sem, idx) => {
            let courses = Array.isArray(sem.courses)
              ? sem.courses.filter(c => c && typeof c === 'object')
              : [];

            // El formato antiguo sólo guarda el nombre. NO se inventa código,
            // créditos ni tipo: rellenarlos con 'obligatoria' habría escrito
            // una clasificación que nadie capturó, y guardar la pantalla por
            // cualquier otro motivo la habría vuelto permanente.
            if (courses.length === 0 && Array.isArray(sem.subjects)) {
              courses = sem.subjects
                .filter(s => s)
                .map(s => (typeof s === 'string'
                  ? { code: '', name: s.trim(), credits: '', type: '' }
                  : {
                      code: s.code || '',
                      name: s.name || '',
                      credits: s.credits || '',
                      type: s.type || '',
                    }))
                .filter(c => c.name);
            }

            const number = (Number.isInteger(sem.semester) && sem.semester > 0)
              ? sem.semester
              : ((Number.isInteger(sem.number) && sem.number > 0) ? sem.number : (idx + 1));

            const { subjects, number: _legacyNumber, ...rest } = sem;
            return { ...rest, semester: number, courses };
          });
      } catch (e) {
        console.error('Error parsing curriculum:', e);
        curriculumData = { type: 'semestral', semesters: [] };
      }
    }
    renderCurriculumEditor();

    // Inicializar research lines
    if (programData.research_lines) {
      try {
        researchLinesData = typeof programData.research_lines === 'string'
          ? JSON.parse(programData.research_lines)
          : programData.research_lines;

        if (!Array.isArray(researchLinesData)) researchLinesData = [];
        // Normalizar cada entrada: data legacy puede ser string ("Inteligencia Artificial")
        // en lugar de objeto {name, description}.
        researchLinesData = researchLinesData
          .filter(line => line !== null && line !== undefined)
          .map(line => {
            if (typeof line === 'string') return { name: line, description: '' };
            if (typeof line === 'object') return { name: line.name || '', description: line.description || '' };
            return { name: String(line), description: '' };
          });
      } catch (e) {
        console.error('Error parsing research lines:', e);
        researchLinesData = [];
      }
    }
    renderResearchEditor();
  }

  function collectFormData(form) {
    const formData = new FormData(form);
    const data = {};

    // Campos simples
    for (let [key, value] of formData.entries()) {
      if (key === 'is_active' || key === 'show_curriculum' || key === 'show_hero_cards' ||
          key === 'show_objectives' || key === 'show_graduate_profile' ||
          key === 'show_research_lines' || key === 'show_contact_section' ||
          key === 'show_contact_form') {
        data[key] = true;
      } else {
        data[key] = value || null;
      }
    }

    // Campos booleanos no marcados
    ['is_active', 'show_curriculum', 'show_hero_cards', 'show_objectives',
     'show_graduate_profile', 'show_research_lines', 'show_contact_section',
     'show_contact_form'].forEach(field => {
      if (!(field in data)) {
        data[field] = false;
      }
    });

    // Convertir números
    if (data.duration_semesters) data.duration_semesters = parseInt(data.duration_semesters);
    if (data.duration_years) data.duration_years = parseFloat(data.duration_years);

    // Recopilar objetivos
    const objectives = [];
    document.querySelectorAll('#objectivesList input').forEach(input => {
      if (input.value.trim()) objectives.push(input.value.trim());
    });
    data.objectives = objectives.length > 0 ? objectives : null;

    // Recopilar competencias
    const competencies = [];
    document.querySelectorAll('#competenciesList input').forEach(input => {
      if (input.value.trim()) competencies.push(input.value.trim());
    });
    data.graduate_competencies = competencies.length > 0 ? competencies : null;

    // Agregar datos de curriculum y research desde los editores visuales
    data.curriculum_structure = curriculumData.semesters.length > 0 ? curriculumData : null;
    data.research_lines = researchLinesData.length > 0 ? researchLinesData : null;

    return data;
  }

  // Event listeners al cargar el DOM
  document.addEventListener('DOMContentLoaded', function() {
    // Listeners para vistas previas
    document.querySelectorAll('#objectivesList input').forEach(input => {
      input.addEventListener('input', updateObjectivesPreview);
    });

    document.querySelectorAll('#competenciesList input').forEach(input => {
      input.addEventListener('input', updateProfilePreview);
    });

    const profileIntroInput = document.querySelector('[name="graduate_profile_intro"]');
    if (profileIntroInput) {
      profileIntroInput.addEventListener('input', updateProfilePreview);
    }

    // Inicializar vistas previas
    updateObjectivesPreview();
    updateProfilePreview();

    // Submit del formulario
    const form = document.getElementById('programConfigForm');
    if (form) {
      form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const data = collectFormData(this);
        const programSlug = this.dataset.programSlug;

        try {
          const response = await window.apiClient.patch(`/api/v1/programs/${programSlug}`, data);
          const json = await response.json();

          if (json.flash) {
            json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
          }

          if (response.ok) {
            setTimeout(() => {
              window.location.href = `/programs/${programSlug}`;
            }, 1500);
          }
        } catch (error) {
          console.error('Error:', error);
          window.dispatchEvent(new CustomEvent('flash', {
            detail: { level: 'danger', message: 'Error al guardar los cambios.' }
          }));
        }
      });
    }
  });

  // Exponer función de inicialización
  window.initializeProgramConfigEditors = initializeEditors;
})();
