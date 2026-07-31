/* app/static/js/user/postgraduate_admin_dashboard.js
 * Dashboard del administrador de posgrado: recordatorios (estado de correo +
 * documentos pendientes) al abrir la pestaña.
 *
 * Depende de window.SIIAP_POSTGRAD_DASH = { emailConfigUrl, pendingReviews }.
 */
(function () {
  'use strict';

  const CFG = window.SIIAP_POSTGRAD_DASH || {};
  const EMAIL_CONFIG_URL = CFG.emailConfigUrl || '#';
  const PENDING_REVIEWS = Number(CFG.pendingReviews || 0);

  const ALERT_CLASSES = {
    success: 'alert-success',
    warning: 'alert-warning',
    info: 'alert-info',
    danger: 'alert-danger',
  };

  function escHtml(value) {
    return String(value == null ? '' : value)
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

  function renderReminders(list, reminders) {
    if (!reminders.length) {
      list.innerHTML = `
        <div class="empty-state empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-check-circle" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">¡Todo en orden!</h3>
          <p class="empty-state__description">No hay recordatorios pendientes.</p>
        </div>`;
      setBusy(list, false, 'No hay recordatorios pendientes.');
      return;
    }

    const html = reminders.map(r => {
      const cls = ALERT_CLASSES[r.type] || 'alert-info';
      const btnType = r.type === 'warning' ? 'warning' : r.type;
      const action = r.action
        ? `<a href="${escHtml(r.action.url)}" class="btn btn-sm btn-${escHtml(btnType)} mt-2">
             ${escHtml(r.action.text)} <i class="bi bi-arrow-right ms-1" aria-hidden="true"></i>
           </a>`
        : '';
      return `
        <div class="alert ${cls} d-flex align-items-start mb-3" role="note">
          <div class="flex-shrink-0 me-3">
            <i class="bi ${escHtml(r.icon)} icon-2xl" aria-hidden="true"></i>
          </div>
          <div class="flex-grow-1">
            <p class="alert-heading fw-semibold mb-1">${escHtml(r.title)}</p>
            <p class="mb-0">${escHtml(r.message)}</p>
            ${action}
          </div>
        </div>`;
    }).join('');

    list.innerHTML = html;
    setBusy(list, false, `${reminders.length} recordatorio(s) cargados.`);
  }

  async function checkEmailConfiguration() {
    const list = document.getElementById('reminders-list');
    if (!list) return;
    setBusy(list, true, 'Verificando la configuración del sistema…');

    try {
      const response = await fetch('/api/v1/emails/status');
      const data = await response.json();
      const reminders = [];

      if (!data.data?.connected || !data.data?.account) {
        reminders.push({
          type: 'warning',
          icon: 'bi-envelope-x',
          title: 'Correo no configurado',
          message: 'No hay una cuenta de correo configurada. Los correos no se enviarán hasta que configures Microsoft Graph.',
          action: { text: 'Configurar ahora', url: EMAIL_CONFIG_URL },
        });
      } else {
        reminders.push({
          type: 'success',
          icon: 'bi-envelope-check',
          title: 'Correo configurado exitosamente',
          message: `Cuenta activa: ${data.data.account.username || 'No disponible'}`,
          action: null,
        });
      }

      if (PENDING_REVIEWS > 0) {
        reminders.push({
          type: 'info',
          icon: 'bi-hourglass-split',
          title: 'Documentos pendientes de revisión',
          message: `Hay ${PENDING_REVIEWS} documentos esperando revisión en todos los programas.`,
          action: null,
        });
      }

      renderReminders(list, reminders);
    } catch (error) {
      console.error('Error al verificar configuración:', error);
      list.innerHTML = `
        <div class="empty-state empty-state--error empty-state--compact">
          <div class="empty-state__icon"><i class="bi bi-exclamation-octagon" aria-hidden="true"></i></div>
          <h3 class="empty-state__title">No pudimos cargar los recordatorios</h3>
          <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
          <p class="empty-state__error-detail">${escHtml(error.message || 'Error de red')}</p>
          <div class="empty-state__actions">
            <button type="button" id="retryReminders" class="btn btn-outline-primary">
              <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
            </button>
          </div>
        </div>`;
      document.getElementById('retryReminders')
        ?.addEventListener('click', checkEmailConfiguration);
      setBusy(list, false, 'No se pudieron cargar los recordatorios.');
    }
  }

  function init() {
    // shown.bs.tab (no 'click'): evita un refetch por cada pulsación repetida.
    const tab = document.getElementById('reminders-tab');
    if (tab) tab.addEventListener('shown.bs.tab', checkEmailConfiguration);

    // ==================== TIEMPO REAL ====================
    // Reload tras toast (delay 3s) cuando suceden eventos que afectan los KPIs.
    // Eventos que llegan a este rol:
    //   - submission:new (role:coordinator — postgrad también tiene coordinator.page.view)
    //   - acceptance:updated, deliberation:updated (idem)
    //   - email:queue_update (role:postgraduate_admin) → refresca recordatorios
    let reloadScheduled = false;
    const scheduleReload = (msg) => {
      if (reloadScheduled) return;
      reloadScheduled = true;
      if (typeof showFlash === 'function') showFlash('info', msg);
      setTimeout(() => { window.location.reload(); }, 3000);
    };

    window.addEventListener('siiap:submission:new',       () => scheduleReload('Nuevo documento pendiente de revisión. Actualizando...'));
    window.addEventListener('siiap:acceptance:updated',   () => scheduleReload('Cambio en aceptación. Actualizando...'));
    window.addEventListener('siiap:deliberation:updated', () => scheduleReload('Cambio en deliberación. Actualizando...'));

    // email:queue_update: no reload, sólo refresca recordatorios si la pestaña
    // de recordatorios está visible.
    window.addEventListener('siiap:email:queue_update', () => {
      const pane = document.getElementById('reminders');
      if (pane && pane.classList.contains('active')) {
        checkEmailConfiguration();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
