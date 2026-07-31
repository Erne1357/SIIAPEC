// app/static/js/program/program_transfer.js
(function() {
  'use strict';
  
  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };
  
  function flash(message, level = 'success') {
    window.dispatchEvent(new CustomEvent('flash', { 
      detail: { level: level, message: message } 
    }));
  }
  
  // State global
  let currentAnalysis = null;
  let currentFromProgram = null;
  let currentToProgram = null;
  
  // ==================== INICIALIZAR ====================
  document.addEventListener('DOMContentLoaded', () => {
    initTransferButton();
    initModalEvents();
  });
  
  // ==================== BOTÓN DE CAMBIO ====================
  function initTransferButton() {
    const transferBtn = document.getElementById('btnRequestProgramChange');
    if (!transferBtn) return;
    
    transferBtn.addEventListener('click', async () => {
      const programId = transferBtn.dataset.programId;
      const programSlug = transferBtn.dataset.programSlug;
      
      if (!programId) {
        flash('Error: No se pudo identificar el programa actual', 'danger');
        return;
      }
      
      currentFromProgram = { id: parseInt(programId), slug: programSlug };
      
      // Cargar programas disponibles
      await loadAvailablePrograms();
      
      // Mostrar modal de selección
      const modal = new bootstrap.Modal(document.getElementById('selectProgramModal'));
      modal.show();
    });
  }
  
  // ==================== CARGAR PROGRAMAS ====================
  async function loadAvailablePrograms() {
    const list = document.getElementById('availableProgramsList');
    if (list) {
      list.setAttribute('aria-busy', 'true');
      list.innerHTML = '<div class="skeleton skeleton-card skeleton-card--sm"></div>';
    }

    try {
      const res = await fetch('/api/v1/programs', {
        credentials: 'same-origin'
      });
      
      if (!res.ok) throw new Error('Error cargando programas');
      
      const json = await res.json();
      const programs = json.data || [];
      
      const container = document.getElementById('availableProgramsList');
      container.innerHTML = '';

      let shown = 0;
      programs.forEach(prog => {
        // No mostrar el programa actual
        if (prog.id === currentFromProgram.id) return;
        shown += 1;

        const card = document.createElement('div');
        card.className = 'program-option card mb-2';
        card.innerHTML = `
          <div class="card-body">
            <div class="form-check">
              <input class="form-check-input" type="radio" name="targetProgram"
                     id="prog-${prog.id}" value="${prog.id}" data-slug="${prog.slug}">
              <label class="form-check-label" for="prog-${prog.id}">
                <strong>${prog.name}</strong>
                <p class="small text-muted mb-0">${prog.description || ''}</p>
              </label>
            </div>
          </div>
        `;
        container.appendChild(card);
      });

      if (!shown) {
        container.innerHTML = `
          <div class="empty-state empty-state--compact">
            <div class="empty-state__icon"><i class="bi bi-inbox" aria-hidden="true"></i></div>
            <h4 class="empty-state__title">No hay otros programas disponibles</h4>
            <p class="empty-state__description">Por ahora no existe otro programa al que puedas cambiarte.</p>
          </div>
        `;
      }

      container.setAttribute('aria-busy', 'false');
      if (window.SIIAP && SIIAP.announce) {
        SIIAP.announce(shown === 1
          ? '1 programa disponible para cambio'
          : `${shown} programas disponibles para cambio`);
      }

    } catch (err) {
      console.error('Error loading programs:', err);
      const container = document.getElementById('availableProgramsList');
      if (container) {
        container.setAttribute('aria-busy', 'false');
        container.innerHTML = `
          <div class="empty-state empty-state--compact empty-state--error">
            <div class="empty-state__icon"><i class="bi bi-wifi-off" aria-hidden="true"></i></div>
            <h4 class="empty-state__title">No pudimos cargar los programas</h4>
            <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
          </div>
        `;
      }
      flash('Error al cargar programas disponibles', 'danger');
    }
  }
  
  // ==================== EVENTOS DEL MODAL ====================
  function initModalEvents() {
    // Botón "Continuar" en modal de selección
    const btnContinueSelection = document.getElementById('btnContinueSelection');
    if (btnContinueSelection) {
      btnContinueSelection.addEventListener('click', async () => {
        const selected = document.querySelector('input[name="targetProgram"]:checked');
        if (!selected) {
          flash('Selecciona un programa', 'warning');
          return;
        }
        
        currentToProgram = {
          id: parseInt(selected.value),
          slug: selected.dataset.slug
        };
        
        // Cerrar modal de selección
        bootstrap.Modal.getInstance(document.getElementById('selectProgramModal')).hide();
        
        // Analizar transferencia
        await analyzeTransfer();
      });
    }
    
    // Botón "Confirmar Cambio" en modal de análisis
    const btnConfirmTransfer = document.getElementById('btnConfirmTransfer');
    if (btnConfirmTransfer) {
      btnConfirmTransfer.addEventListener('click', async () => {
        await executeTransfer();
      });
    }
    
    // Botones de cierre del modal de análisis (pie y cabecera).
    // El modal usa data-bs-backdrop="static", así que necesita salidas explícitas.
    ['btnCancelTransfer', 'btnCancelTransferHeader'].forEach(id => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener('click', () => {
        const modalEl = document.getElementById('analysisModal');
        const inst = bootstrap.Modal.getInstance(modalEl);
        if (inst) inst.hide();
      });
    });
  }
  
  // ==================== ANALIZAR TRANSFERENCIA ====================
  async function analyzeTransfer() {
    const analysisModal = new bootstrap.Modal(document.getElementById('analysisModal'));
    
    // Estado de carga anunciado
    const analysisContainer = document.getElementById('analysisContent');
    analysisContainer.setAttribute('aria-busy', 'true');
    analysisContainer.innerHTML = `
      <p class="mb-3">Analizando el cambio de programa…</p>
      <div class="skeleton skeleton-card skeleton-card--md"></div>
    `;

    analysisModal.show();
    
    try {
      const res = await fetch('/api/v1/program-changes/analyze', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf()
        },
        body: JSON.stringify({
          from_program_id: currentFromProgram.id,
          to_program_id: currentToProgram.id
        })
      });
      
      const json = await res.json();
      
      if (!res.ok || !json.ok) {
        throw new Error(json.error || 'Error al analizar');
      }
      
      currentAnalysis = json.analysis;
      renderAnalysis(currentAnalysis);
      
    } catch (err) {
      console.error('Analysis error:', err);
      analysisContainer.setAttribute('aria-busy', 'false');
      analysisContainer.innerHTML = `
        <div class="alert alert-danger" role="alert">
          <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
          No pudimos analizar el cambio de programa. Inténtalo de nuevo más tarde.
        </div>
      `;
      document.getElementById('btnConfirmTransfer').disabled = true;
    }
  }
  
  // ==================== RENDERIZAR ANÁLISIS ====================
  function renderAnalysis(analysis) {
    const container = document.getElementById('analysisContent');
    
    let html = '<div class="analysis-results">';
    
    // 1. Resumen general
    html += `
      <div class="alert alert-info mb-4">
        <h4 class="h6 mb-2"><i class="bi bi-info-circle-fill me-2" aria-hidden="true"></i>Resumen del cambio</h4>
        <ul class="mb-0 small">
          <li><strong>${analysis.reusable_docs.length}</strong> documento(s) se conservarán</li>
          <li><strong>${analysis.incompatible_docs.length}</strong> documento(s) se eliminarán</li>
          <li><strong>${analysis.missing_docs.length}</strong> documento(s) nuevo(s) requerido(s)</li>
        </ul>
      </div>
    `;

    // 2. Documentos que se conservan
    if (analysis.reusable_docs.length > 0) {
      html += `
        <div class="mb-4">
          <h4 class="h6 text-success-strong">
            <i class="bi bi-check-circle-fill me-2" aria-hidden="true"></i>
            Documentos que se conservarán (${analysis.reusable_docs.length})
          </h4>
          <div class="siiap-table-wrapper">
            <table class="table siiap-table table-sm table-hover mb-0">
              <caption class="visually-hidden">Documentos que se conservarán al cambiar de programa</caption>
              <thead class="table-light">
                <tr>
                  <th scope="col">Documento</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Paso actual</th>
                  <th scope="col">Paso nuevo</th>
                </tr>
              </thead>
              <tbody>
      `;

      analysis.reusable_docs.forEach(doc => {
        // Mismo componente que emite Jinja: nunca `badge bg-*` para un estado.
        const statusBadge = doc.status === 'approved'
          ? SIIAP.statusBadge('approved')
          : doc.status === 'rejected'
          ? SIIAP.statusBadge('rejected')
          : SIIAP.statusBadge('review');

        const matchType = doc.is_same_file
          ? '<i class="bi bi-equals text-success-strong" aria-hidden="true"></i><span class="visually-hidden">Archivo idéntico</span>'
          : '<i class="bi bi-arrow-left-right text-info-strong" aria-hidden="true"></i><span class="visually-hidden">Archivo equivalente</span>';

        html += `
          <tr>
            <td>
              ${doc.name} ${matchType}
              <div class="small text-muted">ID: ${doc.archive_id} → ${doc.target_archive_id}</div>
            </td>
            <td>${statusBadge}</td>
            <td class="small text-muted">${doc.from_step}</td>
            <td class="small text-muted">${doc.to_step}</td>
          </tr>
        `;
      });

      html += `
              </tbody>
            </table>
          </div>
          <p class="small text-muted mb-0 mt-2">
            <i class="bi bi-info-circle-fill me-1" aria-hidden="true"></i>
            Estos documentos serán reutilizados en el nuevo programa.
            Los aprobados volverán al estado «Pendiente» para una nueva revisión.
          </p>
        </div>
      `;
    }
    
    // 3. Documentos que se eliminarán
    if (analysis.incompatible_docs.length > 0) {
      html += `
        <div class="mb-4">
          <h4 class="h6 text-danger-strong">
            <i class="bi bi-x-circle-fill me-2" aria-hidden="true"></i>
            Documentos que se eliminarán (${analysis.incompatible_docs.length})
          </h4>
          <div class="alert alert-warning" role="alert">
            <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
            <strong>Atención:</strong> estos archivos no son compatibles con el nuevo programa
            y serán <strong>eliminados permanentemente</strong>.
          </div>
          <ul class="list-group">
      `;
      
      analysis.incompatible_docs.forEach(doc => {
        html += `
          <li class="list-group-item d-flex justify-content-between align-items-start">
            <div>
              <strong>${doc.name}</strong>
              <div class="small text-muted">Paso: ${doc.step_name}</div>
              <div class="small text-muted">ID: ${doc.archive_id}</div>
            </div>
            <i class="bi bi-trash text-danger" aria-hidden="true"></i>
          </li>
        `;
      });
      
      html += `
          </ul>
        </div>
      `;
    }
    
    // 4. Documentos faltantes
    if (analysis.missing_docs.length > 0) {
      html += `
        <div class="mb-4">
          <h4 class="h6 text-brand-primary">
            <i class="bi bi-file-earmark-medical-fill me-2" aria-hidden="true"></i>
            Documentos nuevos requeridos (${analysis.missing_docs.length})
          </h4>
          <div class="alert alert-info" role="alert">
            <i class="bi bi-info-circle-fill me-2" aria-hidden="true"></i>
            Deberás subir estos documentos después del cambio.
          </div>
          <ul class="list-group">
      `;
      
      analysis.missing_docs.forEach(doc => {
        html += `
          <li class="list-group-item">
            <strong>${doc.name}</strong>
            <div class="small text-muted">${doc.step_name}</div>
            <div class="small text-muted">ID requerido: ${doc.archive_id}</div>
          </li>
        `;
      });
      
      html += `
          </ul>
        </div>
      `;
    }
    
    // 5. Estado de entrevista
    if (analysis.interview_status.has_interview) {
      const willCancel = analysis.interview_status.will_cancel;
      html += `
        <div class="alert ${willCancel ? 'alert-danger' : 'alert-success'} mb-4" role="alert">
          <h4 class="h6 mb-2">
            <i class="bi bi-calendar3 me-2" aria-hidden="true"></i>
            Estado de la entrevista
          </h4>
          ${willCancel
            ? `<p class="mb-0">
                <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>
                <strong>Tu entrevista será cancelada.</strong><br>
                Motivo: ${analysis.interview_status.reason}
              </p>`
            : `<p class="mb-0">
                <i class="bi bi-check-circle-fill me-2" aria-hidden="true"></i>
                Tu entrevista se mantendrá activa en el nuevo programa.
              </p>`
          }
        </div>
      `;
    }

    // 6. Campo de razón
    html += `
      <div class="mb-3">
        <label class="form-label" for="transferReason">
          <strong>Razón del cambio</strong> <span class="text-muted">(opcional)</span>
        </label>
        <textarea class="form-control" id="transferReason" rows="3"
                  placeholder="Explica brevemente por qué deseas cambiar de programa..."></textarea>
      </div>
    `;

    html += '</div>';

    container.innerHTML = html;
    container.setAttribute('aria-busy', 'false');
    document.getElementById('btnConfirmTransfer').disabled = false;
  }
  
  // ==================== EJECUTAR TRANSFERENCIA ====================
  async function executeTransfer() {
    const reason = document.getElementById('transferReason')?.value || '';
    const confirmBtn = document.getElementById('btnConfirmTransfer');
    const originalText = confirmBtn.innerHTML;
    
    // Deshabilitar botón y mostrar loading
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<i class="bi bi-arrow-repeat bi-spin me-2" aria-hidden="true"></i>Procesando...';
    
    try {
      const res = await fetch('/api/v1/program-changes/execute', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrf()
        },
        body: JSON.stringify({
          from_program_id: currentFromProgram.id,
          to_program_id: currentToProgram.id,
          reason: reason
        })
      });
      
      const json = await res.json();
      
      if (!res.ok || !json.ok) {
        throw new Error(json.error || 'Error al ejecutar el cambio');
      }
      
      // Éxito
      bootstrap.Modal.getInstance(document.getElementById('analysisModal')).hide();
      
      flash('Cambio de programa completado exitosamente', 'success');
      
      // Redirigir al nuevo programa después de 2 segundos
      setTimeout(() => {
        window.location.href = `/programs/admission/${currentToProgram.slug}`;
      }, 2000);
      
    } catch (err) {
      console.error('Transfer error:', err);
      flash(`Error: ${err.message}`, 'danger');
      confirmBtn.innerHTML = originalText;
      confirmBtn.disabled = false;
    }
  }
  
})();