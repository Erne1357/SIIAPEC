/* app/static/js/utils/student_record_button.js
 * Global helper: returns HTML for the "Ver expediente completo" button
 * to be used inside any student-row action column.
 *
 * Usage in any table renderer:
 *   const html = window.siiapStudentRecordBtn(userId, { size: 'sm' });
 *   row.innerHTML += html;
 */
(function () {
  'use strict';
  if (window.siiapStudentRecordBtn) return;

  window.siiapStudentRecordBtn = function (userId, opts) {
    // `userId` es el identificador PÚBLICO del usuario (UUID en cadena). Ya no
    // existe el caso «id entero 0», así que basta la comprobación de vacío.
    if (!userId) return '';
    const o = opts || {};
    const sizeClass = o.size === 'lg' ? '' : 'btn-sm';
    const cls = `btn ${sizeClass} btn-outline-secondary ${o.extraClass || ''}`.trim();
    const title = o.title || 'Ver expediente completo';
    return `<a href="/students/${encodeURIComponent(userId)}/record"
              class="${cls}"
              title="${title}"
              data-student-record-btn="1"
              target="${o.newTab === false ? '_self' : '_blank'}">
              <i class="bi bi-folder2-open"></i>${o.label ? ` ${o.label}` : ''}
           </a>`;
  };
})();
