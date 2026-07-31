/*
 * SIIAP — Formato único de fechas en el cliente.
 *
 * Espejo EXACTO de los filtros Jinja de app/utils/datetime_utils.py
 * (format_date_es / format_time_es / format_datetime_es), para que una fecha
 * pintada por el servidor y la misma fecha pintada por JS se lean igual.
 *
 * Se usan tablas de meses propias en lugar de Intl.DateTimeFormat porque el
 * mes abreviado de septiembre cambió en CLDR ('sep' -> 'sept'): el resultado
 * de Intl depende de la versión del navegador y dejaría de coincidir con el
 * filtro de Python. El resto del formato es idéntico al de la plantilla.
 *
 * API pública:
 *   SIIAP.formatDate(iso, style)          -> '31 de julio de 2026' | '31 jul 2026' | '31/07/2026'
 *   SIIAP.formatTime(iso)                 -> '14:30'
 *   SIIAP.formatDateTime(iso, style)      -> '31 de julio de 2026 a las 14:30'
 *   SIIAP.timeEl(iso, style, withTime)    -> <time datetime="…">…</time> (nodo DOM)
 *   SIIAP.parseDate(iso)                  -> Date | null
 *
 * Estilos admitidos: 'long' (por defecto), 'short', 'numeric'.
 */

(function (global) {
  'use strict';

  const SIIAP = global.SIIAP || (global.SIIAP = {});

  const MONTHS_ES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
  ];

  const MONTHS_ES_SHORT = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic',
  ];

  const STYLES = ['long', 'short', 'numeric'];

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  /**
   * Convierte a Date lo que devuelve la API.
   * Una fecha sin hora ('2026-07-31') la interpreta el motor como UTC y en
   * México se mostraría el día anterior; por eso se arma en hora local.
   * @param {string|number|Date} value
   * @returns {Date|null}
   */
  function parseDate(value) {
    if (value === null || value === undefined || value === '') return null;

    if (value instanceof Date) {
      return isNaN(value.getTime()) ? null : value;
    }

    if (typeof value === 'number') {
      const fromNumber = new Date(value);
      return isNaN(fromNumber.getTime()) ? null : fromNumber;
    }

    const raw = String(value).trim();
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    if (dateOnly) {
      return new Date(
        Number(dateOnly[1]),
        Number(dateOnly[2]) - 1,
        Number(dateOnly[3])
      );
    }

    const parsed = new Date(raw);
    return isNaN(parsed.getTime()) ? null : parsed;
  }

  /** Detecta si la cadena original traía información de hora. */
  function hasTimePart(value) {
    if (value instanceof Date || typeof value === 'number') return true;
    return !/^\d{4}-\d{2}-\d{2}$/.test(String(value == null ? '' : value).trim());
  }

  function normalizeStyle(style) {
    return STYLES.indexOf(style) === -1 ? 'long' : style;
  }

  SIIAP.parseDate = parseDate;

  /**
   * Fecha en español.
   * @param {string|number|Date} iso
   * @param {'long'|'short'|'numeric'} [style='long']
   * @param {string} [fallback='—'] Texto cuando el valor es nulo o inválido
   * @returns {string}
   */
  SIIAP.formatDate = function (iso, style, fallback) {
    const date = parseDate(iso);
    if (!date) return fallback === undefined ? '—' : fallback;

    const day = date.getDate();
    const month = date.getMonth();
    const year = date.getFullYear();

    switch (normalizeStyle(style)) {
      case 'numeric':
        return pad2(day) + '/' + pad2(month + 1) + '/' + year;
      case 'short':
        return day + ' ' + MONTHS_ES_SHORT[month] + ' ' + year;
      default:
        return day + ' de ' + MONTHS_ES[month] + ' de ' + year;
    }
  };

  /**
   * Hora en formato de 24 h, igual que format_time_es.
   * @param {string|number|Date} iso
   * @param {string} [fallback=''] Texto cuando no hay hora disponible
   * @returns {string}
   */
  SIIAP.formatTime = function (iso, fallback) {
    const date = parseDate(iso);
    if (!date || !hasTimePart(iso)) return fallback === undefined ? '' : fallback;
    return pad2(date.getHours()) + ':' + pad2(date.getMinutes());
  };

  /**
   * Fecha y hora en español, con el mismo enlace que el filtro de Jinja:
   *   long    -> '31 de julio de 2026 a las 14:30'
   *   short   -> '31 jul 2026, 14:30'
   *   numeric -> '31/07/2026 14:30'
   * Si el valor no trae hora, devuelve sólo la fecha.
   * @param {string|number|Date} iso
   * @param {'long'|'short'|'numeric'} [style='long']
   * @param {string} [fallback='—']
   * @returns {string}
   */
  SIIAP.formatDateTime = function (iso, style, fallback) {
    const date = parseDate(iso);
    if (!date) return fallback === undefined ? '—' : fallback;

    const key = normalizeStyle(style);
    const text = SIIAP.formatDate(date, key);
    const clock = hasTimePart(iso) ? SIIAP.formatTime(date) : '';
    if (!clock) return text;

    if (key === 'long') return text + ' a las ' + clock;
    if (key === 'short') return text + ', ' + clock;
    return text + ' ' + clock;
  };

  /**
   * Nodo <time> con el atributo datetime legible por máquinas.
   * @param {string|number|Date} iso
   * @param {'long'|'short'|'numeric'} [style='long']
   * @param {boolean} [withTime=false] Incluir la hora en el texto visible
   * @returns {HTMLTimeElement}
   */
  SIIAP.timeEl = function (iso, style, withTime) {
    const date = parseDate(iso);
    const el = document.createElement('time');
    if (date) el.setAttribute('datetime', date.toISOString());
    el.textContent = withTime
      ? SIIAP.formatDateTime(iso, style)
      : SIIAP.formatDate(iso, style);
    return el;
  };

})(window);
