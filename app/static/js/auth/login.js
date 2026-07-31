/*
 * SIIAP — Pantalla de inicio de sesión.
 *
 * Extraído del <script type="module"> que vivía dentro de auth/login.html:
 * la plantilla sólo debe declarar variables window.* provenientes de Jinja2.
 *
 * Contratos que consume:
 *   - window.SIIAP_DASHBOARD_URL  (destino tras autenticar)
 *   - js/api/http.js              (api(): CSRF por cabecera X-CSRFToken)
 *   - SIIAP.setButtonLoading      (js/utils/button-loading.js)
 *   - data-toggle-password        (js/utils/password-toggle.js)
 */

import { api } from '../api/http.js';

(() => {
  'use strict';

  const form = document.getElementById('loginForm');
  if (!form) return;

  const submitBtn = form.querySelector('button[type="submit"]');
  const usernameEl = document.getElementById('username');
  const passwordEl = document.getElementById('password');
  const usernameError = document.getElementById('usernameError');
  const passwordError = document.getElementById('passwordError');

  function flash(message, level = 'success') {
    window.dispatchEvent(new CustomEvent('flash', {
      detail: { level: level, message: message }
    }));
  }

  /** Muestra u oculta el error de un campo y refleja aria-invalid. */
  function setFieldError(input, errorEl, invalid) {
    if (!input || !errorEl) return;
    errorEl.classList.toggle('d-none', !invalid);
    input.classList.toggle('is-invalid', !!invalid);
    if (invalid) {
      input.setAttribute('aria-invalid', 'true');
    } else {
      input.removeAttribute('aria-invalid');
    }
  }

  function clearErrors() {
    setFieldError(usernameEl, usernameError, false);
    setFieldError(passwordEl, passwordError, false);
  }

  usernameEl?.addEventListener('input', () => setFieldError(usernameEl, usernameError, false));
  passwordEl?.addEventListener('input', () => setFieldError(passwordEl, passwordError, false));

  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      try { sessionStorage.removeItem('pendingFlash'); } catch (_) { /* almacenamiento no disponible */ }
    }, 100);
  });

  function setLoading(loading, label) {
    form.setAttribute('aria-busy', loading ? 'true' : 'false');
    if (window.SIIAP && window.SIIAP.setButtonLoading) {
      window.SIIAP.setButtonLoading(submitBtn, loading, label);
    } else if (submitBtn) {
      submitBtn.disabled = loading;
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors();

    const username = usernameEl.value.trim();
    const password = passwordEl.value;

    // Validación por campo: el mensaje nombra el campo que falta y mueve el
    // foco hasta él, en vez de un aviso genérico que obliga a tabular a ciegas.
    if (!username) {
      setFieldError(usernameEl, usernameError, true);
      flash('Escribe tu nombre de usuario para continuar.', 'warning');
      usernameEl.focus();
      return;
    }
    if (!password) {
      setFieldError(passwordEl, passwordError, true);
      flash('Escribe tu contraseña para continuar.', 'warning');
      passwordEl.focus();
      return;
    }

    setLoading(true, 'Iniciando sesión…');

    try {
      await api('/auth/login', { method: 'POST', body: { username, password } });
      flash('Inicio de sesión exitoso. Redirigiendo…', 'success');
      setTimeout(() => {
        window.location.href = window.SIIAP_DASHBOARD_URL || '/';
      }, 100);
    } catch (err) {
      console.error('Login error:', err);
      const errorMsg = err?.message
        || 'No pudimos iniciar sesión. Revisa tu usuario y tu contraseña e inténtalo de nuevo.';
      flash(errorMsg, 'danger');
      setFieldError(passwordEl, passwordError, false);
      setLoading(false);
      usernameEl.focus();
    }
  });
})();
