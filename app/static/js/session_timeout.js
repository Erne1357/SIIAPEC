// Session timeout con API (sesión Flask + CSRF), advertencia 1 min antes del corte
(() => {
  'use strict';

  if (typeof userLoggedIn === 'undefined' || userLoggedIn !== 'true') {
    
    return;
  }

  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };
  const csrf = getCsrf();
  const getLoginUrl = () => {
    return (typeof loginPageUrl !== 'undefined' && loginPageUrl) ? loginPageUrl : '/login';
  };
  const toLogin = () => {
    window.location.href = getLoginUrl();
  };

  const WARNING_MS = 14 * 60 * 1000; // 14 min
  const AUTO_LOGOUT_MS = 1 * 60 * 1000; // +1 min
  let warningTimer = null;
  let autoLogoutTimer = null;

  const modalEl = document.getElementById('sessionModal');
  const continueBtn = document.getElementById('continueBtn');
  const logoutBtn = document.getElementById('logoutBtn');
  let bsModal = null;

  function getBsModal() {
    if (!modalEl) return null;
    if (!bsModal && window.bootstrap && window.bootstrap.Modal) {
      bsModal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    }
    return bsModal;
  }

  function isModalShown() {
    return modalEl && modalEl.classList.contains('show');
  }

  function clearTimers() {
    if (warningTimer) { clearTimeout(warningTimer); warningTimer = null; }
    if (autoLogoutTimer) { clearTimeout(autoLogoutTimer); autoLogoutTimer = null; }
  }

  function showSessionModal() {
    const m = getBsModal();
    if (!m) return;
    m.show();
    // El diálogo aparece sin que el usuario lo pida: se anuncia por la región
    // viva única para que un lector de pantalla no lo pase por alto.
    if (window.SIIAP && typeof window.SIIAP.announce === 'function') {
      window.SIIAP.announce('Tu sesión expirará en 1 minuto por inactividad.');
    }
    autoLogoutTimer = setTimeout(doApiLogout, AUTO_LOGOUT_MS);
  }

  function hideSessionModal() {
    const m = getBsModal();
    if (!m) return;
    m.hide();
    if (autoLogoutTimer) { clearTimeout(autoLogoutTimer); autoLogoutTimer = null; }
  }

  async function doApiLogout() {
    try {
      await fetch(sessionLogoutUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf }
      });
      // NO mostrar flash aquí - dejamos que el backend maneje los mensajes
    } catch (e) {
      console.warn('[session] fallo logout API, redirigiendo igual.', e);
    } finally {
      // IMPORTANTE: Limpiar cualquier storage y forzar recarga completa
      try {
        sessionStorage.clear();
      } catch (_) {}
      
      // Usar replace() para forzar recarga completa sin caché
      window.location.replace(getLoginUrl());
    }
  }

  async function doKeepalive() {
    try {
      const res = await fetch(sessionKeepaliveUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': csrf
        }
      });
      const json = await res.json().catch(() => ({}));
      if (json && json.flash) {
        json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
      }
      if (!res.ok) throw new Error('keepalive no OK');
      resetInactivityTimers(true);
      return true;
    } catch (err) {
      console.error('[session] error keepalive:', err);
      toLogin();
      return false;
    }
  }

  function scheduleWarning() {
    warningTimer = setTimeout(showSessionModal, WARNING_MS);
  }

  let activityArm = true;
  function onActivity() {
    if (!activityArm) return;
    activityArm = false;
    setTimeout(() => (activityArm = true), 2000);
    if (isModalShown()) hideSessionModal();
    resetInactivityTimers(false);
  }

  function resetInactivityTimers(fromKeepalive) {
    clearTimers();
    scheduleWarning();
  }

  if (continueBtn) {
    continueBtn.addEventListener('click', async () => {
      hideSessionModal();
      await doKeepalive();
    });
  }
  if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
      hideSessionModal();
      doApiLogout();
    });
  }

  const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];
  ACTIVITY_EVENTS.forEach(evt => window.addEventListener(evt, onActivity, { passive: true }));
  window.addEventListener('focus', onActivity);

  
  scheduleWarning();
})();
