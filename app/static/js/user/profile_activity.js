/* app/static/js/user/profile_activity.js
 * Carga "Actividad Reciente", "Próximos Eventos" y la pestaña de Documentos
 * históricos del perfil. Usa endpoints en /api/v1/users/me/*.
 */
(function () {
  'use strict';

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** Marca la región asíncrona como ocupada/lista y lo anuncia (WCAG 4.1.3). */
  function setBusy(el, busy, message) {
    if (window.SIIAP && typeof window.SIIAP.setBusy === 'function') {
      window.SIIAP.setBusy(el, busy, message ? { message } : undefined);
    } else if (el) {
      el.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
  }

  /** Estado vacío de error con botón de reintento. */
  function errorState(title, detail, retryId) {
    return `
      <div class="empty-state empty-state--error empty-state--compact">
        <div class="empty-state__icon"><i class="bi bi-exclamation-octagon" aria-hidden="true"></i></div>
        <h3 class="empty-state__title">${escHtml(title)}</h3>
        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
        ${detail ? `<p class="empty-state__error-detail">${escHtml(detail)}</p>` : ''}
        <div class="empty-state__actions">
          <button type="button" id="${escHtml(retryId)}" class="btn btn-outline-primary">
            <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
          </button>
        </div>
      </div>`;
  }

  function formatRelativeOrDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 1) return 'Hace unos segundos';
    if (diffMin < 60) return `Hace ${diffMin} min`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `Hace ${diffHr} h`;
    const diffDays = Math.round(diffHr / 24);
    if (diffDays < 7) return `Hace ${diffDays} día${diffDays === 1 ? '' : 's'}`;
    if (window.SIIAP && typeof window.SIIAP.formatDate === 'function') {
      return window.SIIAP.formatDate(iso, 'short', '');
    }
    return d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  function formatEventDate(iso) {
    if (!iso) return '';
    if (window.SIIAP && typeof window.SIIAP.formatDateTime === 'function') {
      return window.SIIAP.formatDateTime(iso, 'long', '');
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString('es-MX', {
      day: '2-digit', month: 'long', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function formatShortDate(iso) {
    if (!iso) return '';
    if (window.SIIAP && typeof window.SIIAP.formatDate === 'function') {
      return window.SIIAP.formatDate(iso, 'short', '');
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  // ── Actividad Reciente ─────────────────────────────────────────────────
  async function loadActivity() {
    const container = document.getElementById('profileActivityContainer');
    if (!container) return;

    setBusy(container, true, 'Cargando tu actividad reciente…');

    try {
      const res = await fetch('/api/v1/users/me/activity?limit=6');
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      renderActivity(json.data || []);
    } catch (e) {
      container.innerHTML = errorState('No pudimos cargar tu actividad reciente.', e.message, 'retryActivity');
      document.getElementById('retryActivity')?.addEventListener('click', loadActivity);
      setBusy(container, false, 'No se pudo cargar la actividad reciente.');
    }
  }

  function renderActivity(items) {
    const container = document.getElementById('profileActivityContainer');
    if (!container) return;

    if (!items.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin actividad reciente</h3>
          <p class="empty-state__description">Tus acciones aparecerán aquí.</p>
        </div>`;
      setBusy(container, false, 'No hay actividad reciente.');
      return;
    }

    const rows = items.map(it => {
      const url = it.url ? `<a href="${escHtml(it.url)}" class="text-reset text-decoration-none">` : '';
      const closeUrl = it.url ? `</a>` : '';
      return `
        <li class="d-flex mb-3">
          <div class="bg-${escHtml(it.icon_color || 'primary')} bg-opacity-10 p-2 rounded-circle me-3 flex-shrink-0">
            <i class="bi ${escHtml(it.icon || 'bi-clock-history')} text-${escHtml(it.icon_color || 'primary')}" aria-hidden="true"></i>
          </div>
          <div class="flex-grow-1">
            ${url}<p class="mb-0 fw-medium">${escHtml(it.title)}</p>${closeUrl}
            ${it.description ? `<small class="text-muted d-block">${escHtml(it.description)}</small>` : ''}
            <small class="text-muted">${escHtml(formatRelativeOrDate(it.timestamp))}</small>
          </div>
        </li>`;
    }).join('');

    container.innerHTML = `<ul class="list-unstyled mb-0">${rows}</ul>`;
    setBusy(container, false, `${items.length} movimiento(s) recientes cargados.`);
  }

  // ── Próximos Eventos ──────────────────────────────────────────────────
  async function loadUpcoming() {
    const container = document.getElementById('profileUpcomingEventsContainer');
    if (!container) return;

    setBusy(container, true, 'Cargando tus próximos eventos…');

    try {
      const res = await fetch('/api/v1/users/me/upcoming-events?limit=5');
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      renderUpcoming(json.data || []);
    } catch (e) {
      container.innerHTML = errorState('No pudimos cargar tus próximos eventos.', e.message, 'retryUpcoming');
      document.getElementById('retryUpcoming')?.addEventListener('click', loadUpcoming);
      setBusy(container, false, 'No se pudieron cargar los próximos eventos.');
    }
  }

  function renderUpcoming(items) {
    const container = document.getElementById('profileUpcomingEventsContainer');
    if (!container) return;

    if (!items.length) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin eventos próximos</h3>
          <p class="empty-state__description">No estás inscrito a eventos futuros.</p>
        </div>`;
      setBusy(container, false, 'No tienes eventos próximos.');
      return;
    }

    const rows = items.map(ev => `
      <li class="d-flex mb-3">
        <div class="bg-primary bg-opacity-10 p-2 rounded-circle me-3 flex-shrink-0">
          <i class="bi bi-calendar-event text-primary" aria-hidden="true"></i>
        </div>
        <div class="flex-grow-1">
          <a href="${escHtml(ev.url)}" class="text-reset text-decoration-none">
            <p class="mb-0 fw-medium">${escHtml(ev.title)}</p>
          </a>
          <small class="text-muted d-block">${escHtml(formatEventDate(ev.event_date))}</small>
          ${ev.location ? `<small class="text-muted"><i class="bi bi-geo-alt me-1" aria-hidden="true"></i>${escHtml(ev.location)}</small>` : ''}
        </div>
      </li>
    `).join('');

    container.innerHTML = `<ul class="list-unstyled mb-0">${rows}</ul>`;
    setBusy(container, false, `${items.length} evento(s) próximos cargados.`);
  }

  // ── Documentos históricos por fase ─────────────────────────────────────
  async function loadDocumentsHistory() {
    const container = document.getElementById('profileDocumentsHistoryContainer');
    if (!container) return;

    setBusy(container, true, 'Cargando tus documentos históricos…');

    try {
      const res = await fetch('/api/v1/users/me/documents-history');
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      renderDocumentsHistory(json.data || {});
    } catch (e) {
      container.innerHTML = errorState('No pudimos cargar tus documentos.', e.message, 'retryDocumentsHistory');
      document.getElementById('retryDocumentsHistory')?.addEventListener('click', loadDocumentsHistory);
      setBusy(container, false, 'No se pudieron cargar los documentos históricos.');
    }
  }

  /** Chip de estado del sistema de diseño (con icono y texto, nunca solo color). */
  function statusBadge(status) {
    if (window.SIIAP && typeof window.SIIAP.statusBadge === 'function') {
      return window.SIIAP.statusBadge(status || 'pending', null, 'sm');
    }
    const labels = {
      review: 'En revisión', approved: 'Aprobado',
      rejected: 'Rechazado', pending: 'Pendiente',
    };
    const key = status || 'pending';
    return `<span class="status-badge status-badge--${escHtml(key)} status-badge--sm">${escHtml(labels[key] || key)}</span>`;
  }

  function renderDocList(docs) {
    if (!docs || !docs.length) {
      return `<div class="text-muted small px-3 py-2">No hay documentos en esta fase.</div>`;
    }
    return `
      <ul class="list-group list-group-flush">
        ${docs.map(d => `
          <li class="list-group-item d-flex justify-content-between align-items-center flex-wrap gap-2">
            <div>
              <div class="fw-medium">${escHtml(d.archive_name || 'Documento')}</div>
              <small class="text-muted">
                ${escHtml(formatShortDate(d.upload_date))}
                ${d.semester ? ` · Semestre ${escHtml(String(d.semester))}` : ''}
              </small>
            </div>
            <div class="d-flex align-items-center gap-2">
              ${statusBadge(d.status)}
              ${d.file_url ? `<a href="${escHtml(d.file_url)}" target="_blank" rel="noopener"
                 class="btn btn-sm btn-outline-secondary tap-target"
                 aria-label="Ver ${escHtml(d.archive_name || 'el documento')}"
                 title="Ver documento">
                <i class="bi bi-eye" aria-hidden="true"></i>
              </a>` : ''}
            </div>
          </li>
        `).join('')}
      </ul>`;
  }

  function renderDocumentsHistory(grouped) {
    const container = document.getElementById('profileDocumentsHistoryContainer');
    if (!container) return;

    const admission = grouped.admission || [];
    const conclusion = grouped.conclusion || [];
    const permanence = grouped.permanence || {};
    const other = grouped.other || [];

    const semesters = Object.keys(permanence).sort((a, b) => {
      const na = parseInt(a, 10);
      const nb = parseInt(b, 10);
      if (isNaN(na) || isNaN(nb)) return String(a).localeCompare(String(b));
      return na - nb;
    });

    const isEmpty = !admission.length && !conclusion.length && !semesters.length && !other.length;
    if (isEmpty) {
      container.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-folder-x" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">Sin documentos históricos</h3>
          <p class="empty-state__description">Aún no has subido documentos en ninguna fase.</p>
        </div>`;
      setBusy(container, false, 'No hay documentos históricos.');
      return;
    }

    const permanenceCount = semesters.reduce((acc2, k) => acc2 + (permanence[k]?.length || 0), 0);

    const permanenceBody = semesters.length
      ? semesters.map(sem => {
          const docs = permanence[sem] || [];
          const semLabel = sem === 'sin_semestre' ? 'Sin semestre asignado' : `Semestre ${sem}`;
          return `
            <div class="border-bottom">
              <div class="px-3 py-2 bg-body-tertiary fw-semibold small">${escHtml(semLabel)} · ${docs.length} documento(s)</div>
              ${renderDocList(docs)}
            </div>`;
        }).join('')
      : `<div class="text-muted small px-3 py-2">No hay documentos en permanencia.</div>`;

    const tabBtn = (id, title, count, active) => `
      <li class="nav-item" role="presentation">
        <button class="nav-link ${active ? 'active' : ''}" type="button"
                id="docHist-${id}-tab"
                data-bs-toggle="tab" data-bs-target="#docHist-${id}-pane"
                role="tab" aria-controls="docHist-${id}-pane"
                aria-selected="${active ? 'true' : 'false'}">
          <i class="bi bi-folder2-open me-1" aria-hidden="true"></i>${escHtml(title)}
          <span class="badge bg-secondary ms-2">${count}</span>
        </button>
      </li>`;

    const tabPane = (id, body, active) => `
      <div class="tab-pane fade ${active ? 'show active' : ''}" id="docHist-${id}-pane"
           role="tabpanel" aria-labelledby="docHist-${id}-tab" tabindex="0">
        ${body}
      </div>`;

    container.innerHTML = `
      <ul class="nav nav-tabs mb-3" role="tablist" aria-label="Documentos por fase">
        ${tabBtn('admission', 'Admisión', admission.length, true)}
        ${tabBtn('permanence', 'Permanencia', permanenceCount, false)}
        ${tabBtn('conclusion', 'Conclusión', conclusion.length, false)}
        ${other.length ? tabBtn('other', 'Otros', other.length, false) : ''}
      </ul>
      <div class="tab-content">
        ${tabPane('admission', renderDocList(admission), true)}
        ${tabPane('permanence', permanenceBody, false)}
        ${tabPane('conclusion', renderDocList(conclusion), false)}
        ${other.length ? tabPane('other', renderDocList(other), false) : ''}
      </div>`;
    setBusy(container, false, 'Documentos históricos cargados.');
  }

  // ── Init ───────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('profileActivityContainer')) loadActivity();
    if (document.getElementById('profileUpcomingEventsContainer')) loadUpcoming();
    if (document.getElementById('profileDocumentsHistoryContainer')) loadDocumentsHistory();

    document.getElementById('btnRefreshActivity')?.addEventListener('click', loadActivity);
    document.getElementById('btnRefreshUpcoming')?.addEventListener('click', loadUpcoming);

    // Lazy-load documents tab when first opened (una sola petición por visita)
    const docsTab = document.getElementById('documents-tab');
    if (docsTab) {
      let docsLoaded = false;
      docsTab.addEventListener('shown.bs.tab', () => {
        if (docsLoaded) return;
        if (document.getElementById('profileDocumentsHistoryContainer')) {
          docsLoaded = true;
          loadDocumentsHistory();
        }
      });
    }
  });
})();
