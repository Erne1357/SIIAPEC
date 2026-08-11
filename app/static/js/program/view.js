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

  function getCsrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  }

  // ── Desplazamiento suave hacia las anclas internas ──────────────────────
  // scrollIntoView respeta el contenedor con overflow (#main-content);
  // window.scrollTo no lo hacía y el salto quedaba a medias.
  //
  // El selector cuelga de .program-landing y no de .program-view-container: el
  // hero vive FUERA del contenedor centrado para poder sangrar a todo el ancho,
  // y es justo donde están los tres enlaces de navegación de la página.
  document.querySelectorAll('.program-landing a[href^="#"]').forEach(anchor => {
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

  // ── "Avísame cuando abra" ───────────────────────────────────────────────
  // Es la acción real que sustituye a un botón que sólo podía decir que no.
  const interestBtn = document.getElementById('admissionInterestBtn');
  const feedback = document.getElementById('admissionInterestFeedback');

  if (interestBtn && feedback) {
    const originalHtml = interestBtn.innerHTML;

    function showFeedback(message, variant) {
      feedback.classList.remove(
        'admission-closed__feedback--ok',
        'admission-closed__feedback--error');
      feedback.classList.add('admission-closed__feedback--' + variant);
      feedback.textContent = message;
      if (window.SIIAP && SIIAP.announce) SIIAP.announce(message);
    }

    // El botón NO se quita: quitarlo tira el foco al <body> dentro de un
    // diálogo modal y el usuario de teclado se queda sin posición. Se
    // desactiva, se reetiqueta y el foco pasa al mensaje de confirmación.
    function settle(btn, label) {
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-check-lg me-2" aria-hidden="true"></i>' + label;
      feedback.focus({ preventScroll: true });
    }

    interestBtn.addEventListener('click', async function () {
      const programId = this.dataset.programId;
      if (!programId) return;

      this.disabled = true;
      this.innerHTML =
        '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Activando…';

      try {
        const response = await fetch(
          `/api/v1/programs/${encodeURIComponent(programId)}/admission-interest`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCsrf()
            }
          }
        );

        let payload = null;
        try {
          payload = await response.json();
        } catch (_) {
          payload = null;
        }

        if (response.ok) {
          showFeedback(
            'Listo. Te avisaremos por correo y aquí en tus notificaciones ' +
            'en cuanto abra la convocatoria.',
            'ok'
          );
          settle(this, 'Aviso activado');
          return;
        }

        // 409 = ya estaba activado. No es un error del usuario.
        const variant = response.status === 409 ? 'ok' : 'error';
        const message = (payload && payload.error && payload.error.message)
          || 'No pudimos activar el aviso. Inténtalo de nuevo en un momento.';
        showFeedback(message, variant);

        if (response.status === 409) {
          settle(this, 'Aviso activado');
          return;
        }
      } catch (err) {
        showFeedback(
          'No pudimos activar el aviso: revisa tu conexión e inténtalo de nuevo.',
          'error'
        );
      }

      this.disabled = false;
      this.innerHTML = originalHtml;
    });
  }

  // ── Tooltips de Bootstrap ───────────────────────────────────────────────
  if (window.bootstrap && bootstrap.Tooltip) {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
      new bootstrap.Tooltip(el);
    });
  }
});
