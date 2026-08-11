/* app/static/js/utils/escape.js
 * SIIAP — Canonical output-encoding helpers for JS-built HTML.
 *
 * This is the ONLY place in the codebase allowed to define an escaping helper.
 * Every module that builds markup with template literals must call these
 * through the shared namespace:
 *
 *   SIIAP.escapeHtml(value)   -> HTML text / element content
 *   SIIAP.escapeAttr(value)   -> value of a double-quoted HTML attribute
 *
 * Loaded globally and FIRST (see app/templates/base.html and auth_base.html),
 * so it is available to every page script without any import.
 *
 * ---------------------------------------------------------------------------
 * WHY THESE ARE WRITTEN AS EXPLICIT CHARACTER REPLACEMENT
 * ---------------------------------------------------------------------------
 * A very common shortcut looks like this:
 *
 *     function escapeHtml(text) {          // DO NOT REINTRODUCE THIS
 *       const div = document.createElement('div');
 *       div.textContent = text;
 *       return div.innerHTML;
 *     }
 *
 * The HTML serializer behind `innerHTML` only escapes `&`, `<` and `>` inside
 * a text node. It leaves `"` and `'` untouched, because inside text content
 * quotes are harmless. They are NOT harmless once the result is pasted into an
 * attribute value, which is exactly what our template literals do:
 *
 *     `<div data-name="${escapeHtml(user.first_name)}">`
 *
 * With the textContent trick, a first_name of  " onmouseover=alert(1) x="
 * closes the attribute and injects a new one. That is a stored XSS running in
 * a staff session. Hence: never round-trip through the DOM, always replace the
 * characters by hand.
 *
 * ---------------------------------------------------------------------------
 * WHAT escapeAttr DOES *NOT* FIX
 * ---------------------------------------------------------------------------
 * escapeAttr makes a value safe inside a double-quoted attribute. It does NOT
 * make it safe inside a JS string that lives inside an attribute:
 *
 *     `<button onclick="openUser('${anything}')">`   // UNSAFE, no exceptions
 *
 * There is no escaper that fixes that sink, because the browser HTML-decodes
 * the attribute before the JS parser ever sees it: `&#39;` becomes a real
 * quote and terminates the string literal. The only correct fix is to delete
 * the inline handler and use data-* attributes plus one delegated listener:
 *
 *     `<button type="button" data-action="open-user"
 *              data-user-id="${SIIAP.escapeAttr(u.id)}">`
 *
 *     container.addEventListener('click', (e) => {
 *       const btn = e.target.closest('[data-action="open-user"]');
 *       if (!btn) return;
 *       openUser(btn.dataset.userId);
 *     });
 *
 * Never build an `onclick=` / `onchange=` / `href="javascript:"` string from
 * database values.
 */
(function (global) {
  'use strict';

  const SIIAP = global.SIIAP || (global.SIIAP = {});

  /** Coerces any input to a string; null and undefined become ''. */
  function toText(value) {
    return value == null ? '' : String(value);
  }

  /**
   * Escapes a value for HTML text / element content.
   * Also covers the attribute-breaking quotes, so it is safe (if slightly
   * verbose) when the same string ends up in either context.
   *
   * @param {*} value - Any value; null/undefined yield ''.
   * @returns {string} Escaped text, safe to interpolate into innerHTML.
   */
  SIIAP.escapeHtml = function (value) {
    return toText(value)
      .replace(/&/g, '&amp;')   // must run first: it rewrites the & of the others
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  /**
   * Escapes a value for the body of a double-quoted HTML attribute, e.g.
   *   `<button data-label="${SIIAP.escapeAttr(x)}" aria-label="${SIIAP.escapeAttr(y)}">`
   *
   * Escapes & < > " ' and additionally / so a stray sequence can never help
   * close a tag early inside an unquoted or sloppily quoted attribute.
   * The attribute MUST still be written with double quotes in the template.
   *
   * @param {*} value - Any value; null/undefined yield ''.
   * @returns {string} Escaped attribute value.
   */
  SIIAP.escapeAttr = function (value) {
    return toText(value)
      .replace(/&/g, '&amp;')   // must run first
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/\//g, '&#47;');
  };

})(window);
