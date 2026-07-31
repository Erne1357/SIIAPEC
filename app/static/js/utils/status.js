/*
 * SIIAP — Helper compartido para renderizar StatusBadge desde JS.
 * Mantener sincronizado con _STATUS_META en app/templates/_macros.html
 * y con los modificadores de app/static/css/components/_status-badge.css.
 *
 * API pública:
 *   SIIAP.statusBadgeEl(status, label, size) -> HTMLElement  (preferida: sin XSS)
 *   SIIAP.statusBadge(status, label, size)   -> string HTML   (legado)
 *   SIIAP.statusLabel(status)                -> string en español
 *   SIIAP.statusKey(status)                  -> clave normalizada con guion bajo
 *   SIIAP.STATUS_META                        -> mapa de iconos/etiquetas
 */

(function (global) {
  'use strict';

  const SIIAP = global.SIIAP || (global.SIIAP = {});

  const STATUS_META = {
    in_progress:         { icon: 'arrow-repeat',         label: 'En proceso' },
    interview_completed: { icon: 'mic-fill',             label: 'Entrevista completada' },
    deliberation:        { icon: 'hourglass-split',      label: 'En deliberación' },
    accepted:            { icon: 'check-circle-fill',    label: 'Aceptado' },
    approved:            { icon: 'check-circle-fill',    label: 'Aprobado' },
    rejected:            { icon: 'x-circle-fill',        label: 'Rechazado' },
    deferred:            { icon: 'arrow-right-circle',   label: 'Diferido' },
    enrolled:            { icon: 'mortarboard-fill',     label: 'Inscrito' },
    pending:             { icon: 'clock',                label: 'Pendiente' },
    review:              { icon: 'eye-fill',             label: 'En revisión' },
  };

  const FALLBACK = { icon: 'circle', label: '' };

  SIIAP.STATUS_META = STATUS_META;

  /**
   * Normaliza cualquier variante que devuelva la API ('In-Progress',
   * 'in progress', 'IN_PROGRESS') a la clave canónica con guion bajo.
   * @param {string} status
   * @returns {string}
   */
  function statusKey(status) {
    return String(status == null ? '' : status)
      .trim()
      .toLowerCase()
      .replace(/[-\s]/g, '_');
  }

  /** Clave canónica convertida al modificador BEM (guiones). */
  function statusModifier(status) {
    return statusKey(status).replace(/_/g, '-');
  }

  function metaFor(status) {
    const key = statusKey(status);
    return STATUS_META[key] || FALLBACK;
  }

  /**
   * Etiqueta en español de un estado. Si el estado es desconocido devuelve
   * el propio código legible (guiones bajos convertidos en espacios).
   * @param {string} status
   * @returns {string}
   */
  SIIAP.statusLabel = function (status) {
    const meta = STATUS_META[statusKey(status)];
    if (meta) return meta.label;
    const raw = String(status == null ? '' : status).trim();
    return raw ? raw.replace(/_/g, ' ') : '—';
  };

  /* Alias histórico: algunos módulos lo pidieron con este nombre. */
  SIIAP.STATUS_LABEL = SIIAP.statusLabel;
  SIIAP.statusKey = statusKey;

  /**
   * Construye un status-badge como nodo DOM real.
   * Usa textContent, por lo que es seguro con etiquetas provenientes del
   * usuario o de la base de datos (a diferencia de statusBadge()).
   * @param {string} status - Código del estado (e.g. 'accepted', 'in-progress')
   * @param {string} [label] - Etiqueta personalizada (opcional)
   * @param {string} [size] - 'sm' | 'lg' | '' (opcional)
   * @returns {HTMLElement} <span class="status-badge status-badge--…">
   */
  SIIAP.statusBadgeEl = function (status, label, size) {
    const meta = metaFor(status);

    const el = document.createElement('span');
    el.className = 'status-badge status-badge--' + statusModifier(status);
    if (size) el.classList.add('status-badge--' + size);

    const icon = document.createElement('i');
    icon.className = 'bi bi-' + meta.icon;
    icon.setAttribute('aria-hidden', 'true');
    el.appendChild(icon);

    const text = document.createElement('span');
    text.textContent = label || SIIAP.statusLabel(status);
    el.appendChild(text);

    return el;
  };

  /**
   * Variante en cadena para plantillas de template literal.
   * Prefiere statusBadgeEl() siempre que el destino sea un nodo: esta versión
   * escapa el texto, pero sigue insertándose con innerHTML.
   * @param {string} status
   * @param {string} [label]
   * @param {string} [size]
   * @returns {string} HTML del badge
   */
  SIIAP.statusBadge = function (status, label, size) {
    return SIIAP.statusBadgeEl(status, label, size).outerHTML;
  };

})(window);
