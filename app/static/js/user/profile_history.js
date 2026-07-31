/* app/static/js/user/profile_history.js
 * Pestaña "Historial" del perfil: carga el historial de acciones del usuario
 * y lo pinta como timeline.
 *
 * El contenido viene del servidor pero incluye texto que el propio usuario
 * edita (nombres), así que TODO valor se inserta con textContent, nunca con
 * innerHTML: interpolarlo permitía XSS almacenado.
 */
(function () {
  'use strict';

  let loaded = false;

  function setBusy(el, busy, message) {
    if (window.SIIAP && typeof window.SIIAP.setBusy === 'function') {
      window.SIIAP.setBusy(el, busy, message ? { message } : undefined);
    } else if (el) {
      el.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
  }

  function formatDateTime(iso) {
    if (window.SIIAP && typeof window.SIIAP.formatDateTime === 'function') {
      return window.SIIAP.formatDateTime(iso, 'short');
    }
    const date = new Date(iso);
    if (isNaN(date.getTime())) return '—';
    return date.toLocaleDateString('es-MX', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function buildEntry(entry) {
    const item = el('li', 'timeline-item');
    item.appendChild(el('span', 'timeline-marker'));

    const content = el('div', 'timeline-content');
    const head = el('div', 'd-flex justify-content-between align-items-start gap-2 flex-wrap');

    const main = el('div');
    main.appendChild(el('p', 'fw-semibold mb-1', entry.action_label || 'Acción registrada'));
    if (entry.formatted_description) {
      main.appendChild(el('p', 'mb-1 small text-muted', entry.formatted_description));
    }
    head.appendChild(main);

    if (entry.timestamp) {
      let time;
      if (window.SIIAP && typeof window.SIIAP.timeEl === 'function') {
        time = window.SIIAP.timeEl(entry.timestamp, 'short', true);
        time.className = 'timeline-date';
      } else {
        time = el('time', 'timeline-date', formatDateTime(entry.timestamp));
        time.setAttribute('datetime', entry.timestamp);
      }
      head.appendChild(time);
    }

    content.appendChild(head);
    if (entry.admin_name) {
      content.appendChild(el('p', 'small text-muted mb-0', 'Por: ' + entry.admin_name));
    }

    item.appendChild(content);
    return item;
  }

  function renderEmpty(timeline) {
    timeline.replaceChildren();
    const box = el('div', 'empty-state empty-state--compact');
    const icon = el('div', 'empty-state__icon');
    const i = el('i', 'bi bi-inbox');
    i.setAttribute('aria-hidden', 'true');
    icon.appendChild(i);
    box.appendChild(icon);
    box.appendChild(el('h3', 'empty-state__title', 'Sin acciones registradas'));
    box.appendChild(el('p', 'empty-state__description',
      'Cuando se registren movimientos en tu expediente aparecerán aquí.'));
    timeline.appendChild(box);
  }

  function renderError(timeline, detail) {
    timeline.replaceChildren();
    const box = el('div', 'empty-state empty-state--error empty-state--compact');
    const icon = el('div', 'empty-state__icon');
    const i = el('i', 'bi bi-exclamation-octagon');
    i.setAttribute('aria-hidden', 'true');
    icon.appendChild(i);
    box.appendChild(icon);
    box.appendChild(el('h3', 'empty-state__title', 'No pudimos cargar tu historial'));
    box.appendChild(el('p', 'empty-state__description',
      'Revisa tu conexión e inténtalo de nuevo.'));
    if (detail) box.appendChild(el('p', 'empty-state__error-detail', detail));

    const actions = el('div', 'empty-state__actions');
    const retry = el('button', 'btn btn-outline-primary');
    retry.type = 'button';
    const retryIcon = el('i', 'bi bi-arrow-clockwise me-2');
    retryIcon.setAttribute('aria-hidden', 'true');
    retry.appendChild(retryIcon);
    retry.appendChild(el('span', 'btn-label', 'Reintentar'));
    retry.addEventListener('click', () => { loaded = false; loadUserHistory(); });
    actions.appendChild(retry);
    box.appendChild(actions);

    timeline.appendChild(box);
  }

  async function loadUserHistory() {
    const region = document.getElementById('historyContent');
    const timeline = document.getElementById('historyTimeline');
    if (!region || !timeline) return;

    setBusy(region, true, 'Cargando tu historial…');

    try {
      const res = await fetch('/api/v1/users/me/history', { credentials: 'same-origin' });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error?.message || 'Error ' + res.status);

      const history = (json.data && json.data.history) || [];

      if (!history.length) {
        renderEmpty(timeline);
        loaded = true;
        setBusy(region, false, 'No hay acciones registradas en tu historial.');
        return;
      }

      const list = el('ol', 'timeline list-unstyled mb-0');
      history.forEach(entry => list.appendChild(buildEntry(entry)));
      timeline.replaceChildren(list);
      loaded = true;
      setBusy(region, false, history.length + ' movimiento(s) cargados en tu historial.');

    } catch (error) {
      renderError(timeline, error.message);
      loaded = false;
      setBusy(region, false, 'No se pudo cargar el historial.');
    }
  }

  function init() {
    const historyTab = document.getElementById('history-tab');
    if (!historyTab) return;
    historyTab.addEventListener('shown.bs.tab', () => {
      if (!loaded) loadUserHistory();
    });
    if (historyTab.classList.contains('active')) loadUserHistory();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
