// app/static/js/flash.js

/**
 * Muestra un mensaje flash dinámico
 * @param {string} level - Nivel del mensaje (success, danger, warning, info)
 * @param {string} message - Mensaje a mostrar
 */
function showFlash(level, message) {
  const container = document.getElementById('flash-container');
  if (!container) return;

  const alertTypes = {
    'success': 'success',
    'error': 'danger',
    'danger': 'danger',
    'warning': 'warning',
    'info': 'info'
  };
 
  // Bootstrap Icons: la app nunca carga Font Awesome, así que un mapa fa-*
  // dejaba los flashes sin icono (WCAG 1.4.1: el color era el único indicador).
  const iconTypes = {
    'success': 'bi-check-circle-fill',
    'danger': 'bi-exclamation-circle-fill',
    'warning': 'bi-exclamation-triangle-fill',
    'info': 'bi-info-circle-fill'
  };

  const alertType = alertTypes[level] || 'info';
  const iconClass = iconTypes[alertType] || 'bi-info-circle-fill';

  // Sin role="alert": #flash-container ya es role="region" aria-live="polite".
  // Anidar una región assertive dentro de una polite hace que NVDA y JAWS
  // anuncien el mismo aviso dos veces.
  const alertEl = document.createElement('div');
  alertEl.className = `alert alert-${alertType} alert-dismissible fade show`;
  alertEl.innerHTML = `
    <i class="bi ${iconClass} me-2" aria-hidden="true"></i>
    ${SIIAP.escapeHtml(message)}
    <button type="button" class="btn-close tap-target" data-bs-dismiss="alert" aria-label="Cerrar"></button>
  `;

  container.appendChild(alertEl);

  // Auto-remover después de 5 segundos
  setTimeout(() => {
    alertEl.classList.add('fade-out');
    setTimeout(() => {
      if (alertEl.parentElement) {
        alertEl.remove();
      }
    }, 500);
  }, 5000);
}

/**
 * Escucha eventos de flash personalizados
 */
window.addEventListener('flash', (event) => {
  const { level, message } = event.detail;
  if (message) {
    showFlash(level, message);
  }
});

/**
 * Auto-cerrar alertas después de 5 segundos
 */
document.addEventListener('DOMContentLoaded', () => {
  // Solo alertas dentro del contenedor de flash
  const flashContainer = document.getElementById('flash-container');
  if (flashContainer) {
    const alerts = flashContainer.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(alert => {
      setTimeout(() => {
        alert.classList.add('fade-out');
        setTimeout(() => {
          if (alert.parentElement) {
            alert.remove();
          }
        }, 500);
      }, 5000);
    });
  }

  // Verificar si hay mensajes flash en cola (por ejemplo, tras una redirección)
  try {
    const queue = sessionStorage.getItem('flashQueue');
    if (queue) {
      const flashes = JSON.parse(queue);
      if (Array.isArray(flashes)) {
        flashes.forEach(f => showFlash(f.level || 'info', f.message));
      }
      sessionStorage.removeItem('flashQueue');
    }
  } catch (e) {
    console.error('Error processing flash queue:', e);
  }
});

