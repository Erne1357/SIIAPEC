/* app/static/js/base.js
 * Lógica global del layout: dropdowns del sidebar, offcanvas móvil y logout.
 * Depende de `window.SIIAP_BASE` (definido inline en base.html con url_for/csrf).
 *
 * Expone además los helpers de accesibilidad compartidos por toda la app:
 *   SIIAP.announce(mensaje)              → región viva única (WCAG 4.1.3)
 *   SIIAP.setBusy(el, busy, opts)        → aria-busy + anuncio de carga
 *   SIIAP.syncExpanded(trigger, target, open) → aria-expanded/aria-controls
 */
(function () {
  'use strict';

  const CFG = window.SIIAP_BASE || {};
  const SIIAP = window.SIIAP || (window.SIIAP = {});

  function getCsrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute('content') : '';
  }

  // ── Región viva única para toda la app (WCAG 4.1.3) ────────────────────
  // Una sola región evita que varios anuncios simultáneos se pisen entre sí.
  const LIVE_REGION_ID = 'siiap-live';

  function getLiveRegion() {
    let region = document.getElementById(LIVE_REGION_ID) ||
                 document.getElementById('siiapLiveRegion');
    if (region) return region;
    if (!document.body) return null;

    region = document.createElement('div');
    region.id = LIVE_REGION_ID;
    region.className = 'visually-hidden';
    region.setAttribute('role', 'status');
    region.setAttribute('aria-live', 'polite');
    region.setAttribute('aria-atomic', 'true');
    document.body.appendChild(region);
    return region;
  }

  /**
   * Anuncia un mensaje a los lectores de pantalla sin alterar la interfaz.
   * @param {string} message - Texto en español, ya redactado para el usuario.
   */
  SIIAP.announce = function (message) {
    if (!message) return;
    const region = getLiveRegion();
    if (!region) return;
    // Limpiar primero fuerza el reanuncio cuando el texto se repite.
    region.textContent = '';
    window.setTimeout(() => { region.textContent = String(message); }, 60);
  };

  /**
   * Marca un contenedor como ocupado mientras se carga su contenido.
   * Propaga aria-busy a la .skeleton-region más cercana (o al propio nodo)
   * para que el esqueleto y el contenido real compartan un mismo estado.
   * @param {Element} el - Contenedor afectado
   * @param {boolean} busy - true al iniciar la carga, false al terminar
   * @param {{message?: string}} [opts] - Mensaje opcional a anunciar
   */
  SIIAP.setBusy = function (el, busy, opts) {
    if (!el) return;
    const value = busy ? 'true' : 'false';
    el.setAttribute('aria-busy', value);

    const region = (el.closest && el.closest('.skeleton-region')) || el;
    if (region !== el) region.setAttribute('aria-busy', value);

    if (opts && opts.message) SIIAP.announce(opts.message);
  };

  /**
   * Sincroniza el estado de un disparador de tipo disclosure con su destino.
   * @param {Element} trigger - Botón o enlace que abre el panel
   * @param {Element} target - Panel controlado
   * @param {boolean} open - Estado deseado
   */
  SIIAP.syncExpanded = function (trigger, target, open) {
    if (!trigger) return;
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (target && target.id) trigger.setAttribute('aria-controls', target.id);
    if (target) target.classList.toggle('show', !!open);
  };

  // ── Dropdowns del sidebar ──────────────────────────────────────────────
  function submenuFor(trigger) {
    const targetId = trigger.getAttribute('data-dropdown');
    return targetId ? document.getElementById(targetId) : null;
  }

  /** Cierra todos los submenús del sidebar salvo el indicado. */
  function closeSubmenus(except) {
    document.querySelectorAll('[data-dropdown]').forEach(t => {
      const submenu = submenuFor(t);
      if (submenu && submenu === except) return;
      t.classList.remove('expanded');
      SIIAP.syncExpanded(t, submenu, false);
    });
    // Submenús sin disparador asociado (defensivo).
    document.querySelectorAll('.dropdown-submenu.show').forEach(m => {
      if (m !== except) m.classList.remove('show');
    });
  }

  function initDropdownMenus() {
    document.querySelectorAll('[data-dropdown]').forEach(trigger => {
      const newTrigger = trigger.cloneNode(true);
      trigger.parentNode.replaceChild(newTrigger, trigger);

      // Estado inicial explícito: sin esto el lector de pantalla no sabe
      // que el enlace controla un panel plegable.
      const submenu = submenuFor(newTrigger);
      SIIAP.syncExpanded(
        newTrigger,
        submenu,
        !!(submenu && submenu.classList.contains('show'))
      );

      newTrigger.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();

        const panel = submenuFor(this);
        if (!panel) return;

        const willOpen = !panel.classList.contains('show');

        closeSubmenus(panel);

        this.classList.toggle('expanded', willOpen);
        SIIAP.syncExpanded(this, panel, willOpen);
      });
    });
  }

  // ── Offcanvas móvil ────────────────────────────────────────────────────
  function initOffcanvasLinks() {
    const offcanvasElement = document.getElementById('mobileSidebar');
    if (!offcanvasElement) return;

    const links = offcanvasElement.querySelectorAll(
      'a[href]:not(.dropdown-toggle-custom)'
    );

    links.forEach(link => {
      link.addEventListener('click', function () {
        const href = this.getAttribute('href');
        if (href && href !== '#' && href !== '') {
          const bsOffcanvas = bootstrap.Offcanvas.getInstance(offcanvasElement);
          if (bsOffcanvas) bsOffcanvas.hide();
        }
      });
    });
  }

  // ── Body class para el offcanvas (permite ocultar el footer vía CSS) ─
  function initOffcanvasBodyClass() {
    const offcanvasElement = document.getElementById('mobileSidebar');
    if (!offcanvasElement) return;

    offcanvasElement.addEventListener('show.bs.offcanvas', () =>
      document.body.classList.add('offcanvas-open')
    );
    offcanvasElement.addEventListener('hidden.bs.offcanvas', () =>
      document.body.classList.remove('offcanvas-open')
    );
  }

  // ── Cerrar dropdowns al click fuera ────────────────────────────────────
  function initCloseOnOutsideClick() {
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.dropdown-menu-container')) closeSubmenus(null);
    });

    // Escape cierra el submenú abierto y devuelve el foco al disparador.
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      const openTrigger = document.querySelector(
        '[data-dropdown][aria-expanded="true"]'
      );
      if (!openTrigger) return;
      closeSubmenus(null);
      openTrigger.focus();
    });
  }

  // ── Logout vía API ─────────────────────────────────────────────────────
  function wireLogout() {
    document.querySelectorAll('.js-api-logout').forEach(el => {
      el.addEventListener('click', async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();

        // Cerrar offcanvas si está abierto
        const offcanvas = document.getElementById('mobileSidebar');
        if (offcanvas) {
          const bsOffcanvas = bootstrap.Offcanvas.getInstance(offcanvas);
          if (bsOffcanvas) bsOffcanvas.hide();
        }

        try {
          const res = await fetch(CFG.logoutUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': getCsrf() },
          });
          const json = await res.json().catch(() => ({}));
          if (json.flash) {
            json.flash.forEach(f =>
              window.dispatchEvent(new CustomEvent('flash', { detail: f }))
            );
          }
          if (!res.ok) throw new Error('Error al cerrar sesión');
        } catch (e) {
          console.error(e);
        } finally {
          window.location.href = CFG.loginUrl;
        }
      });
    });
  }

  function init() {
    getLiveRegion();
    initDropdownMenus();
    initOffcanvasLinks();
    initOffcanvasBodyClass();
    initCloseOnOutsideClick();
    wireLogout();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
