// ──────────────────────────────────────────────────────────────
// app/static/js/program/view.js
// Página pública de un programa.
//
// El reveal-on-scroll lo resuelve js/utils/reveal.js (atributos
// data-reveal / data-reveal-delay). Esta hoja ya no reimplementa AOS
// ni escribe estilos en línea.
// ──────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {

  const prefersReducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ── Desplazamiento suave hacia las anclas internas ──────────────────────
  // scrollIntoView respeta el contenedor con overflow (#main-content);
  // window.scrollTo no lo hacía y el salto quedaba a medias.
  document.querySelectorAll('.program-view-container a[href^="#"]').forEach(anchor => {
    if (anchor.hasAttribute('data-bs-toggle')) return;

    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (!targetId || targetId === '#') return;

      const targetElement = document.querySelector(targetId);
      if (!targetElement) return;

      e.preventDefault();
      targetElement.scrollIntoView({
        behavior: prefersReducedMotion ? 'auto' : 'smooth',
        block: 'start'
      });

      // El destino debe recibir el foco o la navegación por teclado
      // se queda donde estaba (WCAG 2.4.3).
      targetElement.setAttribute('tabindex', '-1');
      targetElement.focus({ preventScroll: true });
    });
  });

  // ── Tooltips de Bootstrap ───────────────────────────────────────────────
  if (window.bootstrap && bootstrap.Tooltip) {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
      new bootstrap.Tooltip(el);
    });
  }
});
