/*
 * SIIAP — Toggle compartido de visibilidad de contraseña.
 *
 * Contrato único (sustituye a las tres implementaciones divergentes de
 * login.html, register.html y auth/reset_password.js):
 *
 *   <button type="button" class="btn btn-outline-secondary"
 *           data-toggle-password="#password">
 *     <i class="bi bi-eye-fill" aria-hidden="true"></i>
 *   </button>
 *
 * El valor puede ser un selector CSS ("#password") o el id pelado
 * ("password"). Si el botón todavía arrastra el contrato viejo
 * `data-target="password"`, también se resuelve desde ahí.
 *
 * El botón se anuncia como interruptor: aria-pressed refleja si la
 * contraseña está visible y aria-label alterna entre "Mostrar contraseña"
 * y "Ocultar contraseña". El icono alterna bi-eye-fill / bi-eye-slash-fill.
 *
 * Convivencia con los handlers antiguos: este módulo sólo reclama botones
 * que llevan `data-toggle-password`. Los reclama en fase de captura y
 * detiene la propagación, de modo que un handler heredado sobre el mismo
 * botón (force_password_change.js, los scripts inline de login/register)
 * no pueda dispararse y producir un doble toggle durante la migración.
 */

(function (global) {
  'use strict';

  const SIIAP = global.SIIAP || (global.SIIAP = {});

  const ATTR = 'data-toggle-password';
  const LABEL_SHOW = 'Mostrar contraseña';
  const LABEL_HIDE = 'Ocultar contraseña';
  const ICON_SHOW = 'bi-eye-fill';        /* contraseña oculta: ofrece mostrar */
  const ICON_HIDE = 'bi-eye-slash-fill';  /* contraseña visible: ofrece ocultar */

  /** Resuelve el input asociado a un botón de toggle. */
  function resolveInput(button) {
    const raw = button.getAttribute(ATTR) || button.getAttribute('data-target') || '';
    const value = raw.trim();
    if (!value) return null;

    if (/^[#.\[]/.test(value)) {
      try {
        return document.querySelector(value);
      } catch (err) {
        return null;
      }
    }
    return document.getElementById(value);
  }

  /** Devuelve el <i> del botón, creándolo si la plantilla no lo trae. */
  function resolveIcon(button) {
    let icon = button.querySelector('i.bi, i');
    if (!icon) {
      icon = document.createElement('i');
      icon.className = 'bi ' + ICON_SHOW;
      button.appendChild(icon);
    }
    icon.setAttribute('aria-hidden', 'true');
    return icon;
  }

  /** Refleja en el botón el estado real del input. */
  function paint(button, visible) {
    const icon = resolveIcon(button);
    icon.classList.add('bi');
    icon.classList.remove('bi-eye', 'bi-eye-slash', ICON_SHOW, ICON_HIDE);
    icon.classList.add(visible ? ICON_HIDE : ICON_SHOW);

    button.setAttribute('aria-pressed', visible ? 'true' : 'false');
    button.setAttribute('aria-label', visible ? LABEL_HIDE : LABEL_SHOW);
    button.setAttribute('title', visible ? LABEL_HIDE : LABEL_SHOW);
  }

  /**
   * Alterna la visibilidad de la contraseña asociada a un botón.
   * @param {Element} button - Botón con data-toggle-password
   * @param {boolean} [force] - Estado deseado; si se omite, invierte el actual
   */
  SIIAP.togglePassword = function (button, force) {
    if (!button) return;
    const input = resolveInput(button);
    if (!input) return;

    const visible = typeof force === 'boolean' ? force : input.type === 'password';
    input.type = visible ? 'text' : 'password';
    paint(button, visible);
  };

  /**
   * Prepara los botones de un contenedor (estado inicial, no ata listeners:
   * la delegación en document ya cubre los nodos insertados después).
   * @param {ParentNode} [root=document]
   */
  SIIAP.initPasswordToggles = function (root) {
    const scope = root || document;
    scope.querySelectorAll('[' + ATTR + ']').forEach(button => {
      if (!button.hasAttribute('type')) button.setAttribute('type', 'button');
      const input = resolveInput(button);
      paint(button, !!input && input.type === 'text');
    });
  };

  // Captura: corre antes que cualquier listener atado al propio botón.
  document.addEventListener('click', function (e) {
    const button = e.target.closest ? e.target.closest('[' + ATTR + ']') : null;
    if (!button) return;
    e.preventDefault();
    e.stopPropagation();
    SIIAP.togglePassword(button);
  }, true);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => SIIAP.initPasswordToggles());
  } else {
    SIIAP.initPasswordToggles();
  }

})(window);
