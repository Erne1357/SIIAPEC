/**
 * celery_worker.js
 * Panel de control del worker Celery.
 *
 * Funcionalidades:
 *  - Estado del worker en tiempo real (Socket.IO /worker + polling fallback)
 *  - Historial de tareas con filtros y paginación
 *  - Gestión de schedules redbeat (listar, editar, habilitar/deshabilitar)
 *  - Ejecución manual de tareas
 *  - Envío masivo de notificaciones
 */

'use strict';

// ─── Estado global ────────────────────────────────────────────────────────────

const STATE = {
  histPage:        1,
  histPerPage:     20,
  histTotal:       0,
  schedules:       [],
  pendingTaskKey:  null,
  pendingKwargs:   {},
  socket:          null,
};

// ─── Inicialización ───────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initSocket();
  loadWorkerStatus();
  loadHistorial();
  loadSummaryStats();

  // Tabs — cargar schedules la primera vez que se abre esa pestaña
  document.getElementById('tab-schedules').addEventListener('shown.bs.tab', () => {
    if (STATE.schedules.length === 0) loadSchedules();
  });

  // Filtros de historial
  document.getElementById('btnApplyFilters').addEventListener('click', () => {
    STATE.histPage = 1;
    loadHistorial();
  });
  document.getElementById('btnClearFilters').addEventListener('click', clearFilters);

  // Refresh buttons
  document.getElementById('btnRefreshStatus').addEventListener('click', loadWorkerStatus);
  document.getElementById('btnRefreshSchedules').addEventListener('click', loadSchedules);

  // Botones de ejecución manual (tarjetas de mantenimiento)
  document.querySelectorAll('.btn-run-task').forEach(btn => {
    btn.addEventListener('click', () => {
      const key  = btn.dataset.taskKey;
      const name = btn.dataset.taskName;
      openConfirmRun(key, name, {});
    });
  });

  // Limpieza de notificaciones — usa el campo "días"
  document.getElementById('btnCleanupNotif').addEventListener('click', () => {
    const days = parseInt(document.getElementById('notifDays').value) || 30;
    openConfirmRun('cleanup_old_notifications', 'Limpiar notificaciones antiguas', { days });
  });

  // Modal de confirmación: ejecutar
  document.getElementById('btnConfirmRunTask').addEventListener('click', confirmRunTask);

  // Modal de schedule: guardar
  document.getElementById('btnSaveSchedule').addEventListener('click', saveSchedule);

  // Envío masivo: toggle campo de valor
  document.getElementById('bulkFilterType').addEventListener('change', onBulkFilterTypeChange);
  document.getElementById('btnResetBulk').addEventListener('click', resetBulkForm);
  document.getElementById('btnSendBulk').addEventListener('click', sendBulkNotification);
});

// ─── Socket.IO ────────────────────────────────────────────────────────────────

function initSocket() {
  if (typeof io === 'undefined') return;

  STATE.socket = io(SOCKET_NS, { transports: ['websocket', 'polling'] });

  STATE.socket.on('connect', () => {
    console.log('[worker] Socket.IO conectado al namespace /worker');
  });

  STATE.socket.on('task_started', data => onTaskEvent(data, 'started'));
  STATE.socket.on('task_success', data => onTaskEvent(data, 'success'));
  STATE.socket.on('task_failure', data => onTaskEvent(data, 'failure'));
  STATE.socket.on('task_retry',   data => onTaskEvent(data, 'retry'));

  STATE.socket.on('disconnect', () => {
    console.log('[worker] Socket.IO desconectado');
  });
}

function onTaskEvent(data, status) {
  // Mostrar en el live feed
  appendLiveFeed(data, status);

  // Refrescar historial si la primera tab está visible
  const histTab = document.getElementById('tab-historial');
  if (histTab.classList.contains('active')) {
    loadHistorial(false);   // silencioso (sin spinner)
    loadSummaryStats();
  }
}

// Estados de tarea Celery -> componente compartido .status-badge--task-*.
const TASK_STATUS_META = {
  success: { icon: 'check-circle-fill',  label: 'Exitosa' },
  failure: { icon: 'x-circle-fill',      label: 'Fallida' },
  started: { icon: 'hourglass-split',    label: 'En ejecución' },
  retry:   { icon: 'arrow-repeat',       label: 'Reintentando' },
  pending: { icon: 'clock',              label: 'Pendiente' },
  revoked: { icon: 'slash-circle',       label: 'Revocada' },
};

function taskStatusBadge(status) {
  const key  = String(status || 'pending').toLowerCase();
  const meta = TASK_STATUS_META[key] || { icon: 'circle', label: key };
  return `<span class="status-badge status-badge--task-${key}">` +
         `<i class="bi bi-${meta.icon}" aria-hidden="true"></i>` +
         `<span>${SIIAP.escapeHtml(meta.label)}</span></span>`;
}

function appendLiveFeed(data, status) {
  const section = document.getElementById('liveFeedSection');
  const feed    = document.getElementById('liveFeed');

  section.classList.remove('d-none');

  const time = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const icon = {
    started: '<i class="bi bi-hourglass-split text-brand-primary me-1" aria-hidden="true"></i>',
    success: '<i class="bi bi-check-circle-fill text-success-strong me-1" aria-hidden="true"></i>',
    failure: '<i class="bi bi-x-circle-fill text-danger-strong me-1" aria-hidden="true"></i>',
    retry:   '<i class="bi bi-arrow-repeat text-warning-strong me-1" aria-hidden="true"></i>',
  }[status] || '';

  const taskDisplayName = getDisplayName(data.task_name || '');

  const el = document.createElement('div');
  el.className = `live-event live-event--${status} small py-1 px-2 mb-1 rounded`;
  el.innerHTML = `${icon}<strong>${time}</strong> — ${SIIAP.escapeHtml(taskDisplayName)}
    <span class="ms-1">${taskStatusBadge(status)}</span>
    ${data.error_message ? `<span class="text-danger-strong ms-1">(${SIIAP.escapeHtml(data.error_message)})</span>` : ''}`;

  feed.insertBefore(el, feed.firstChild);

  // Mantener máximo 20 eventos en el feed
  while (feed.children.length > 20) feed.removeChild(feed.lastChild);
}

// ─── Estado del worker ────────────────────────────────────────────────────────

async function loadWorkerStatus() {
  const badge = document.getElementById('workerStatusBadge');
  badge.className = 'badge rounded-pill bg-secondary fs-6 px-3 py-2';
  badge.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Verificando…';

  try {
    const res  = await apiFetch(`${API_BASE}/status`);
    const data = res.data;

    document.getElementById('cardActive').textContent  = data.active_tasks.length;
    document.getElementById('cardWorkers').textContent = data.workers.length;

    if (data.online) {
      badge.className = 'badge rounded-pill bg-success fs-6 px-3 py-2';
      badge.innerHTML = `<i class="bi bi-circle-fill me-1" aria-hidden="true"></i>En línea (${data.workers.length} worker${data.workers.length !== 1 ? 's' : ''})`;
    } else {
      badge.className = 'badge rounded-pill bg-danger fs-6 px-3 py-2';
      badge.innerHTML = '<i class="bi bi-circle-fill me-1" aria-hidden="true"></i>Worker sin conexión';
    }
  } catch (e) {
    badge.className = 'badge rounded-pill bg-danger fs-6 px-3 py-2';
    badge.innerHTML = '<i class="bi bi-exclamation-circle me-1" aria-hidden="true"></i>Sin respuesta';
  }
}

// ─── Resumen de stats (success/failure totales) ───────────────────────────────

async function loadSummaryStats() {
  try {
    const [sucRes, failRes] = await Promise.all([
      apiFetch(`${API_BASE}/tasks?status=success&per_page=1`),
      apiFetch(`${API_BASE}/tasks?status=failure&per_page=1`),
    ]);
    document.getElementById('cardSuccess').textContent  = sucRes.meta.total  ?? '—';
    document.getElementById('cardFailures').textContent = failRes.meta.total ?? '—';
  } catch (_) {}
}

// ─── Historial ────────────────────────────────────────────────────────────────

async function loadHistorial(showSpinner = true) {
  const tbody = document.getElementById('historialBody');

  if (showSpinner) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4">
      <div class="spinner-border text-primary" role="status"></div>
      <p class="text-muted mt-2 mb-0">Cargando historial…</p>
    </td></tr>`;
  }

  const params = buildHistorialParams();

  try {
    const res   = await apiFetch(`${API_BASE}/tasks?${params}`);
    const items = res.data || [];
    const meta  = res.meta || {};

    STATE.histTotal = meta.total || 0;

    tbody.innerHTML = '';

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6">
        <div class="empty-state empty-state--inline">
          <div class="empty-state__icon"><i class="bi bi-inbox" aria-hidden="true"></i></div>
          <p class="empty-state__title">Sin ejecuciones registradas</p>
          <p class="empty-state__description">Ninguna tarea coincide con los filtros aplicados.</p>
        </div>
      </td></tr>`;
    } else {
      items.forEach(t => tbody.insertAdjacentHTML('beforeend', renderTaskRow(t)));
    }

    renderPagination(meta);
    document.getElementById('historialInfo').textContent =
      `${meta.total || 0} registros · página ${meta.page || 1} de ${meta.pages || 1}`;

  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6">
      <div class="empty-state empty-state--inline empty-state--error">
        <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
        <p class="empty-state__title">No se pudo cargar el historial</p>
        <p class="empty-state__description">Vuelve a intentarlo en unos segundos.</p>
        <p class="empty-state__error-detail">${SIIAP.escapeHtml(e.message || '')}</p>
      </div>
    </td></tr>`;
  }
}

function buildHistorialParams() {
  const p = new URLSearchParams();
  p.set('page',     STATE.histPage);
  p.set('per_page', STATE.histPerPage);

  const status    = document.getElementById('filterStatus').value;
  const triggered = document.getElementById('filterTriggeredBy').value;
  const taskName  = document.getElementById('filterTaskName').value;

  if (status)    p.set('status',       status);
  if (triggered) p.set('triggered_by', triggered);
  if (taskName)  p.set('task_name',    taskName);

  return p.toString();
}

function renderTaskRow(t) {
  const statusBadge = taskStatusBadge(t.status);

  const triggeredBadge = t.triggered_by === 'manual'
    ? `<span class="badge bg-primary-soft"><i class="bi bi-hand-index me-1" aria-hidden="true"></i>Manual</span>`
    : `<span class="badge bg-info-soft"><i class="bi bi-clock me-1" aria-hidden="true"></i>Programada</span>`;

  const startedAt = t.started_at
    ? (window.SIIAP && window.SIIAP.formatDateTime
        ? window.SIIAP.formatDateTime(t.started_at, 'short')
        : new Date(t.started_at).toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'medium' }))
    : '<span class="text-muted">—</span>';

  const duration = t.duration_seconds != null
    ? `${t.duration_seconds}s`
    : '<span class="text-muted">—</span>';

  let resultCell = '<span class="text-muted">—</span>';
  if (t.status === 'failure' && t.error_message) {
    resultCell = `<span class="text-danger small" title="${SIIAP.escapeAttr(t.error_message)}">
      <i class="bi bi-x-circle me-1"></i>${SIIAP.escapeHtml(t.error_message.slice(0, 60))}${t.error_message.length > 60 ? '…' : ''}
    </span>`;
  } else if (t.result) {
    const resultStr = JSON.stringify(t.result);
    resultCell = `<span class="text-success small" title="${SIIAP.escapeAttr(resultStr)}">
      <i class="bi bi-check-circle me-1"></i>${SIIAP.escapeHtml(resultStr.slice(0, 60))}${resultStr.length > 60 ? '…' : ''}
    </span>`;
  }

  return `<tr>
    <th scope="row" class="fw-normal"><span class="fw-medium small">${SIIAP.escapeHtml(t.display_name)}</span></th>
    <td>${statusBadge}</td>
    <td>${triggeredBadge}</td>
    <td class="small text-muted">${startedAt}</td>
    <td class="small text-muted">${duration}</td>
    <td>${resultCell}</td>
  </tr>`;
}

function renderPagination(meta) {
  const ul = document.getElementById('historialPages');
  ul.innerHTML = '';

  if (!meta.pages || meta.pages <= 1) return;

  const prevDisabled = !meta.has_prev ? 'disabled' : '';
  const nextDisabled = !meta.has_next ? 'disabled' : '';

  ul.insertAdjacentHTML('beforeend', `
    <li class="page-item ${prevDisabled}">
      <a class="page-link" href="#" data-page="${meta.page - 1}">&laquo;</a>
    </li>`);

  const start = Math.max(1, meta.page - 2);
  const end   = Math.min(meta.pages, meta.page + 2);

  for (let p = start; p <= end; p++) {
    ul.insertAdjacentHTML('beforeend', `
      <li class="page-item ${p === meta.page ? 'active' : ''}">
        <a class="page-link" href="#" data-page="${p}">${p}</a>
      </li>`);
  }

  ul.insertAdjacentHTML('beforeend', `
    <li class="page-item ${nextDisabled}">
      <a class="page-link" href="#" data-page="${meta.page + 1}">&raquo;</a>
    </li>`);

  ul.querySelectorAll('[data-page]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const p = parseInt(link.dataset.page);
      if (!isNaN(p) && p !== STATE.histPage) {
        STATE.histPage = p;
        loadHistorial();
      }
    });
  });
}

function clearFilters() {
  document.getElementById('filterStatus').value    = '';
  document.getElementById('filterTriggeredBy').value = '';
  document.getElementById('filterTaskName').value  = '';
  STATE.histPage = 1;
  loadHistorial();
}

// ─── Schedules (redbeat) ──────────────────────────────────────────────────────

async function loadSchedules() {
  const loading = document.getElementById('schedulesLoading');
  const wrapper = document.getElementById('schedulesTableWrapper');
  const errDiv  = document.getElementById('schedulesError');

  loading.classList.remove('d-none');
  wrapper.classList.add('d-none');
  errDiv.classList.add('d-none');

  try {
    const res = await apiFetch(`${API_BASE}/schedules`);
    STATE.schedules = res.data || [];

    const tbody = document.getElementById('schedulesBody');
    tbody.innerHTML = '';

    STATE.schedules.forEach(s => {
      tbody.insertAdjacentHTML('beforeend', renderScheduleRow(s));
    });

    // Listeners para editar
    tbody.querySelectorAll('.btn-edit-schedule').forEach(btn => {
      btn.addEventListener('click', () => openEditSchedule(btn.dataset.name));
    });

    loading.classList.add('d-none');
    wrapper.classList.remove('d-none');

  } catch (e) {
    loading.classList.add('d-none');
    errDiv.classList.remove('d-none');
    document.getElementById('schedulesErrorMsg').textContent =
      `Error al cargar schedules: ${e.message}`;
  }
}

function renderScheduleRow(s) {
  const enabledBadge = s.enabled
    ? '<span class="status-badge status-badge--accepted status-badge--sm">' +
      '<i class="bi bi-play-circle-fill" aria-hidden="true"></i><span>Activa</span></span>'
    : '<span class="status-badge status-badge--deferred status-badge--sm">' +
      '<i class="bi bi-pause-circle-fill" aria-hidden="true"></i><span>Pausada</span></span>';

  const lastRun = s.last_run_at
    ? (window.SIIAP && window.SIIAP.formatDateTime
        ? window.SIIAP.formatDateTime(s.last_run_at, 'short')
        : new Date(s.last_run_at).toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' }))
    : '<span class="text-muted">—</span>';

  const cronExpr = formatCronExpression(s.cron_fields);

  const displayName = getDisplayName(s.task);

  return `<tr>
    <th scope="row" class="fw-medium">${SIIAP.escapeHtml(s.name)}</th>
    <td class="small text-muted">${SIIAP.escapeHtml(displayName)}</td>
    <td><code class="small">${SIIAP.escapeHtml(cronExpr)}</code></td>
    <td class="small text-muted">${lastRun}</td>
    <td class="text-center">${s.total_run_count ?? 0}</td>
    <td>${enabledBadge}</td>
    <td class="text-end">
      <button type="button" class="btn btn-sm btn-outline-primary btn-edit-schedule tap-target"
              data-name="${SIIAP.escapeAttr(s.name)}"
              aria-label="Editar la programación de ${SIIAP.escapeAttr(s.name)}"
              title="Editar la programación de ${SIIAP.escapeAttr(s.name)}">
        <i class="bi bi-pencil" aria-hidden="true"></i>
      </button>
    </td>
  </tr>`;
}

function formatCronExpression(cf) {
  if (!cf) return '—';
  const m   = cf.minute        || '*';
  const h   = cf.hour          || '*';
  const dow = cf.day_of_week   || '*';
  const dom = cf.day_of_month  || '*';
  const moy = cf.month_of_year || '*';
  return `${m} ${h} ${dom} ${moy} ${dow}`;
}

function openEditSchedule(name) {
  const entry = STATE.schedules.find(s => s.name === name);
  if (!entry) return;

  document.getElementById('editScheduleName').value  = entry.name;
  document.getElementById('editScheduleLabel').value = entry.name;
  document.getElementById('editEnabled').checked     = entry.enabled;

  const cf = entry.cron_fields || {};
  document.getElementById('editCronMinute').value = cf.minute        || '*';
  document.getElementById('editCronHour').value   = cf.hour          || '*';
  document.getElementById('editCronDow').value    = cf.day_of_week   || '*';
  document.getElementById('editCronDom').value    = cf.day_of_month  || '*';

  document.getElementById('editScheduleAlert').classList.add('d-none');

  new bootstrap.Modal(document.getElementById('modalEditSchedule')).show();
}

async function saveSchedule() {
  const name    = document.getElementById('editScheduleName').value;
  const enabled = document.getElementById('editEnabled').checked;
  const alert   = document.getElementById('editScheduleAlert');

  const cronFields = {
    minute:        document.getElementById('editCronMinute').value.trim(),
    hour:          document.getElementById('editCronHour').value.trim(),
    day_of_week:   document.getElementById('editCronDow').value.trim(),
    day_of_month:  document.getElementById('editCronDom').value.trim(),
    month_of_year: '*',
  };

  const btn = document.getElementById('btnSaveSchedule');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Guardando…';
  alert.classList.add('d-none');

  try {
    await apiFetch(`${API_BASE}/schedules/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body:   JSON.stringify({ enabled, cron_fields: cronFields }),
    });

    bootstrap.Modal.getInstance(document.getElementById('modalEditSchedule')).hide();
    await loadSchedules();
    showToast('Schedule actualizado exitosamente.', 'success');
  } catch (e) {
    alert.textContent = `Error: ${e.message}`;
    alert.classList.remove('d-none');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Guardar cambios';
  }
}

// ─── Ejecución manual ─────────────────────────────────────────────────────────

function openConfirmRun(taskKey, taskName, kwargs) {
  STATE.pendingTaskKey = taskKey;
  STATE.pendingKwargs  = kwargs;
  document.getElementById('confirmTaskName').textContent = taskName;
  new bootstrap.Modal(document.getElementById('modalConfirmRun')).show();
}

async function confirmRunTask() {
  const btn = document.getElementById('btnConfirmRunTask');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Encolando…';

  try {
    await apiFetch(`${API_BASE}/tasks/run`, {
      method: 'POST',
      body:   JSON.stringify({
        task_key: STATE.pendingTaskKey,
        kwargs:   STATE.pendingKwargs,
      }),
    });

    bootstrap.Modal.getInstance(document.getElementById('modalConfirmRun')).hide();

    // Cambiar a tab de historial para ver el progreso
    bootstrap.Tab.getOrCreateInstance(document.getElementById('tab-historial')).show();
    showToast('Tarea encolada. Aparecerá en el historial en breve.', 'success');

    setTimeout(() => loadHistorial(), 1500);
  } catch (e) {
    showToast(`Error al encolar tarea: ${e.message}`, 'danger');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Sí, ejecutar';
  }
}

// ─── Envío masivo de notificaciones ──────────────────────────────────────────

function onBulkFilterTypeChange() {
  const val     = document.getElementById('bulkFilterType').value;
  const wrapper = document.getElementById('bulkFilterValueWrapper');
  const label   = document.getElementById('bulkFilterValueLabel');

  if (!val || val === 'all') {
    wrapper.style.display = 'none';
    return;
  }

  wrapper.style.display = '';
  const labels = {
    role:    'Nombre del rol (ej: applicant)',
    program: 'ID o slug del programa',
    process: 'Estado del proceso (ej: in_progress)',
  };
  label.textContent = labels[val] || 'Valor del filtro';
}

function resetBulkForm() {
  document.getElementById('formBulkNotif').reset();
  document.getElementById('bulkFilterValueWrapper').style.display = 'none';
}

async function sendBulkNotification() {
  const filterType  = document.getElementById('bulkFilterType').value;
  const filterValue = document.getElementById('bulkFilterValue').value.trim();
  const title       = document.getElementById('bulkTitle').value.trim();
  const message     = document.getElementById('bulkMessage').value.trim();
  const priority    = document.getElementById('bulkPriority').value;
  const actionUrl   = document.getElementById('bulkActionUrl').value.trim();

  if (!filterType)  return showToast('Selecciona el tipo de destinatarios.', 'warning');
  if (!title)       return showToast('El título es requerido.', 'warning');
  if (!message)     return showToast('El mensaje es requerido.', 'warning');
  if (filterType !== 'all' && !filterValue)
    return showToast('Ingresa el valor del filtro.', 'warning');

  const btn = document.getElementById('btnSendBulk');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Encolando…';

  try {
    await apiFetch(`${API_BASE}/tasks/run`, {
      method: 'POST',
      body: JSON.stringify({
        task_key: 'send_bulk_notification_by_filter',
        kwargs: {
          filter_type:       filterType,
          filter_value:      filterValue || '',
          notification_type: 'admin_broadcast',
          title,
          message,
          priority,
          action_url:        actionUrl || null,
        },
      }),
    });

    showToast('Envío masivo encolado exitosamente.', 'success');
    resetBulkForm();
    bootstrap.Tab.getOrCreateInstance(document.getElementById('tab-historial')).show();
    setTimeout(() => loadHistorial(), 1500);
  } catch (e) {
    showToast(`Error: ${e.message}`, 'danger');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-send me-1"></i>Encolar envío masivo';
  }
}

// ─── Utilidades ───────────────────────────────────────────────────────────────

const DISPLAY_NAMES = {
  'app.tasks.maintenance.cleanup_expired_admission_files': 'Limpieza de archivos expirados',
  'app.tasks.maintenance.apply_retention_policies':        'Políticas de retención',
  'app.tasks.maintenance.cleanup_old_notifications':       'Notificaciones antiguas',
  'app.tasks.notifications.send_bulk_notification':        'Envío masivo',
  'app.tasks.notifications.send_bulk_notification_by_filter': 'Envío masivo por filtro',
};

function getDisplayName(taskName) {
  return DISPLAY_NAMES[taskName] || taskName;
}

async function apiFetch(url, options = {}) {
  const defaults = {
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken':  CSRF_TOKEN,
    },
  };
  const merged = { ...defaults, ...options };
  if (options.headers) merged.headers = { ...defaults.headers, ...options.headers };

  const res = await fetch(url, merged);
  const json = await res.json();

  if (!res.ok) {
    throw new Error(json?.error?.message || `HTTP ${res.status}`);
  }
  return json;
}

function showToast(message, type = 'info') {
  // Si ya existe un contenedor de toasts, usarlo; si no, crearlo
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    document.body.appendChild(container);
  }

  const bgClass = {
    success: 'bg-success text-white',
    danger:  'bg-danger text-white',
    warning: 'bg-warning',
    info:    'bg-info text-white',
  }[type] || 'bg-secondary text-white';

  const id = `toast-${Date.now()}`;
  container.insertAdjacentHTML('beforeend', `
    <div id="${id}" class="toast align-items-center ${bgClass} border-0" role="alert" aria-live="assertive">
      <div class="d-flex">
        <div class="toast-body">${SIIAP.escapeHtml(message)}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto"
                data-bs-dismiss="toast"></button>
      </div>
    </div>`);

  const el = document.getElementById(id);
  new bootstrap.Toast(el, { delay: 4000 }).show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}
