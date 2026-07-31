/*
 * SIIAP — Pantalla de registro de aspirantes.
 *
 * Extraído del <script> que vivía dentro de auth/register.html: la plantilla
 * sólo debe declarar variables window.* provenientes de Jinja2.
 *
 * Contratos que consume:
 *   - window.SIIAP_LOGIN_URL   (destino tras un alta correcta)
 *   - SIIAP.setButtonLoading   (js/utils/button-loading.js)
 *   - data-toggle-password     (js/utils/password-toggle.js)
 *
 * El formulario lleva `novalidate`: las burbujas nativas del navegador salen
 * en el idioma del navegador y se autoocultan, así que todos los mensajes de
 * error se emiten aquí, en español, nombrando el problema y la recuperación.
 */

(() => {
  'use strict';

  const form = document.getElementById('registerForm');
  if (!form) return;

  const submitBtn = form.querySelector('button[type="submit"]') || form.querySelector('button');

  const firstNameEl = document.getElementById('first_name');
  const lastNameEl = document.getElementById('last_name');
  const usernameEl = document.getElementById('username');
  const emailEl = document.getElementById('email');
  const passwordEl = document.getElementById('password');
  const confirmEl = document.getElementById('confirm_password');

  /** Campo -> nodo de error asociado (aria-describedby ya los enlaza). */
  const ERROR_OF = new Map([
    [firstNameEl, document.getElementById('firstNameError')],
    [lastNameEl, document.getElementById('lastNameError')],
    [usernameEl, document.getElementById('usernameError')],
    [emailEl, document.getElementById('emailError')],
    [passwordEl, document.getElementById('passwordError')],
    [confirmEl, document.getElementById('confirmPasswordError')],
  ]);

  function flash(message, level = 'success') {
    window.dispatchEvent(new CustomEvent('flash', {
      detail: { level: level, message: message }
    }));
  }

  const getCsrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  };

  /** Muestra u oculta el error de un campo y refleja aria-invalid. */
  function setFieldError(input, invalid, message) {
    if (!input) return;
    const errorEl = ERROR_OF.get(input);
    if (errorEl) {
      if (message) errorEl.textContent = message;
      errorEl.classList.toggle('d-none', !invalid);
    }
    input.classList.toggle('is-invalid', !!invalid);
    if (invalid) {
      input.setAttribute('aria-invalid', 'true');
    } else {
      input.removeAttribute('aria-invalid');
    }
  }

  function clearErrors() {
    ERROR_OF.forEach((_errorEl, input) => setFieldError(input, false));
  }

  /** Marca el campo, avisa por la región de notificaciones y le da el foco. */
  function fail(input, message) {
    setFieldError(input, true, message);
    flash(message, 'warning');
    input.focus();
    return false;
  }

  function validate() {
    clearErrors();

    if (!firstNameEl.value.trim()) {
      return fail(firstNameEl, 'Escribe tu nombre para continuar.');
    }
    if (!lastNameEl.value.trim()) {
      return fail(lastNameEl, 'Escribe tu apellido paterno para continuar.');
    }
    if (!usernameEl.value.trim()) {
      return fail(usernameEl, 'Escribe un nombre de usuario para continuar.');
    }
    if (usernameEl.value.trim().length < 5) {
      return fail(usernameEl, 'El usuario debe tener al menos 5 caracteres. Prueba con tu nombre y tu apellido, por ejemplo jperez1.');
    }
    if (!emailEl.value.trim()) {
      return fail(emailEl, 'Escribe tu correo electrónico para continuar.');
    }
    if (!emailEl.checkValidity()) {
      return fail(emailEl, 'El correo electrónico no tiene un formato válido. Escríbelo como nombre@dominio.com.');
    }
    if (!passwordEl.value) {
      return fail(passwordEl, 'Escribe una contraseña para continuar.');
    }
    if (passwordEl.value.length < 8) {
      return fail(passwordEl, 'La contraseña debe tener al menos 8 caracteres. Añade más letras o números hasta llegar a 8.');
    }
    if (passwordEl.value !== confirmEl.value) {
      return fail(confirmEl, 'Las contraseñas no coinciden. Escribe la misma contraseña en ambos campos.');
    }
    return true;
  }

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
    if (!validate()) return;

    setLoading(true, 'Registrando…');

    try {
      const response = await fetch(form.action || window.location.href, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': getCsrf(),
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: new FormData(form)
      });

      const contentType = response.headers.get('Content-Type');

      if (!contentType || !contentType.includes('application/json')) {
        const htmlText = await response.text();
        console.error('Respuesta inesperada del servidor:', htmlText.substring(0, 500));
        flash('El servidor devolvió una respuesta inesperada. Espera un momento e inténtalo de nuevo.', 'danger');
        return;
      }

      const json = await response.json();

      if (!response.ok || !json.ok) {
        const errorMsg = json?.error || json?.message
          || 'No pudimos crear la cuenta. Revisa los datos e inténtalo de nuevo.';
        flash(errorMsg, 'danger');
        return;
      }

      flash('Registro exitoso. Redirigiendo al inicio de sesión…', 'success');
      setTimeout(() => {
        window.location.href = window.SIIAP_LOGIN_URL || '/login';
      }, 150);
    } catch (err) {
      console.error('Registration error:', err);
      flash('No hubo conexión con el servidor. Revisa tu red e inténtalo de nuevo.', 'danger');
    } finally {
      setLoading(false);
    }
  });

  // Validación en vivo de la confirmación de contraseña: el aviso es textual,
  // no sólo un borde rojo (WCAG 1.4.1 y 3.3.1).
  function syncConfirmState() {
    if (!confirmEl.value) {
      setFieldError(confirmEl, false);
      return;
    }
    setFieldError(confirmEl, passwordEl.value !== confirmEl.value,
      'Las contraseñas no coinciden. Escribe la misma contraseña en ambos campos.');
  }

  confirmEl.addEventListener('input', syncConfirmState);
  passwordEl.addEventListener('input', () => {
    setFieldError(passwordEl, false);
    syncConfirmState();
  });

  [firstNameEl, lastNameEl, usernameEl, emailEl].forEach(input => {
    input.addEventListener('input', () => setFieldError(input, false));
  });
})();
