/*
 * SIIAP — Pantalla «Establecer contraseña» (alta por token).
 *
 * Contratos que consume:
 *   - window.SIIAP_RESET_TOKEN  (token de un solo uso)
 *   - window.SIIAP_LOGIN_URL    (destino tras establecer la contraseña)
 *   - SIIAP.setButtonLoading    (js/utils/button-loading.js)
 *   - data-toggle-password      (js/utils/password-toggle.js) — el toggle de
 *     visibilidad ya NO se implementa aquí: lo resuelve la utilidad común,
 *     que además mantiene aria-pressed y aria-label.
 */

(() => {
  'use strict';

  const TOKEN = window.SIIAP_RESET_TOKEN;
  const LOGIN_URL = window.SIIAP_LOGIN_URL || '/login';

  const subtitle = document.getElementById('resetSubtitle');
  const invalid = document.getElementById('resetTokenInvalid');
  const invalidTitle = document.getElementById('resetInvalidTitle');
  const invalidDesc = document.getElementById('resetInvalidDesc');
  const form = document.getElementById('resetPasswordForm');
  const newPw = document.getElementById('newPassword');
  const confirmPw = document.getElementById('confirmPassword');
  const mismatch = document.getElementById('resetMismatch');
  const submitBtn = document.getElementById('resetSubmitBtn');

  /**
   * Muestra la pantalla de enlace no utilizable y LLEVA EL FOCO hasta ella:
   * sin esto, quien usa lector de pantalla sólo oye «Verificando enlace…» y
   * nunca se entera de que el enlace murió ni de qué hacer a continuación.
   */
  function showInvalid(title, desc) {
    invalidTitle.textContent = title;
    invalidDesc.textContent = desc;
    subtitle.textContent = desc;
    form.classList.add('d-none');
    invalid.classList.remove('d-none');
    invalid.setAttribute('tabindex', '-1');
    invalid.focus();
  }

  function showFlash(level, message) {
    if (window.showFlash) window.showFlash(level, message);
  }

  // Verificar token al cargar
  async function verifyToken() {
    if (!TOKEN) {
      showInvalid('Enlace inválido', 'No se proporcionó un token. Verifica que abriste el enlace completo enviado por correo.');
      return;
    }
    try {
      const res = await fetch(`/api/v1/auth/reset-password/${encodeURIComponent(TOKEN)}/info`);
      const json = await res.json();
      if (!res.ok || json.error) {
        const code = json.error?.code || 'TOKEN_NOT_FOUND';
        const map = {
          TOKEN_NOT_FOUND: ['Enlace inválido', 'El enlace no es válido o ya no existe. Solicita uno nuevo a la coordinación de Posgrado.'],
          TOKEN_EXPIRED: ['Enlace expirado', 'Este enlace ha expirado. Contacta al administrador para que te envíe uno nuevo.'],
          TOKEN_USED: ['Enlace ya utilizado', 'Este enlace ya fue utilizado para establecer una contraseña. Inicia sesión normalmente.'],
        };
        const [title, desc] = map[code] || ['Enlace no disponible', json.error?.message || 'No fue posible validar el enlace.'];
        showInvalid(title, desc);
        return;
      }
      const data = json.data;
      subtitle.textContent = `Hola, ${data.first_name}. Configura tu contraseña para iniciar sesión como ${data.username}.`;
      form.classList.remove('d-none');
    } catch (e) {
      showInvalid('Error de conexión', 'No se pudo verificar el enlace. Revisa tu conexión a internet e inténtalo de nuevo.');
    }
  }

  function passwordsMatch() {
    return newPw.value && newPw.value === confirmPw.value;
  }

  /** El aviso es textual y está enlazado por aria-describedby, no sólo color. */
  function updateMismatchHint() {
    const invalidPair = !!confirmPw.value && !passwordsMatch();
    mismatch.classList.toggle('d-none', !invalidPair);
    confirmPw.classList.toggle('is-invalid', invalidPair);
    if (invalidPair) {
      confirmPw.setAttribute('aria-invalid', 'true');
    } else {
      confirmPw.removeAttribute('aria-invalid');
    }
  }

  function setLoading(loading, label) {
    form.setAttribute('aria-busy', loading ? 'true' : 'false');
    if (window.SIIAP && window.SIIAP.setButtonLoading) {
      window.SIIAP.setButtonLoading(submitBtn, loading, label);
    } else {
      submitBtn.disabled = loading;
    }
  }

  newPw.addEventListener('input', updateMismatchHint);
  confirmPw.addEventListener('input', updateMismatchHint);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!TOKEN) {
      showInvalid('Enlace inválido', 'No se proporcionó un token. Verifica que abriste el enlace completo enviado por correo.');
      return;
    }
    if (!newPw.value) {
      showFlash('warning', 'Escribe tu nueva contraseña para continuar.');
      newPw.focus();
      return;
    }
    if (!passwordsMatch()) {
      updateMismatchHint();
      showFlash('warning', 'Las contraseñas no coinciden. Escribe la misma contraseña en ambos campos.');
      confirmPw.focus();
      return;
    }

    setLoading(true, 'Estableciendo…');
    try {
      const res = await fetch(`/api/v1/auth/reset-password/${encodeURIComponent(TOKEN)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          new_password: newPw.value,
          confirm_password: confirmPw.value,
        }),
      });
      const json = await res.json();
      (json.flash || []).forEach(f => showFlash(f.level, f.message));
      if (res.ok && !json.error) {
        setTimeout(() => { window.location.href = LOGIN_URL; }, 1200);
        return;
      }
      // Si el token ya no es válido, mostrar la pantalla de error
      const code = json.error?.code;
      if (['TOKEN_NOT_FOUND', 'TOKEN_EXPIRED', 'TOKEN_USED'].includes(code)) {
        showInvalid('Enlace no disponible', json.error?.message || 'El enlace ya no es válido. Solicita uno nuevo a la coordinación de Posgrado.');
      }
    } catch (err) {
      showFlash('danger', 'No hubo conexión con el servidor. Revisa tu red e inténtalo de nuevo.');
    } finally {
      setLoading(false);
    }
  });

  // Iniciar
  verifyToken();
})();
