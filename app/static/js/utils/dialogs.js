/* app/static/js/utils/dialogs.js
 * Reemplazo de window.confirm / window.alert con un modal Bootstrap reutilizable.
 *
 *   await siiapConfirm('¿Eliminar este archivo?');          // true | false
 *   await siiapConfirm({
 *     title: 'Eliminar política',
 *     message: '¿Seguro? Esta acción no se puede deshacer.',
 *     type: 'danger',
 *     confirmLabel: 'Sí, eliminar',
 *   });
 *
 *   await siiapAlert('Mensaje enviado exitosamente.');
 *   await siiapAlert({ title: 'Listo', message: '...', type: 'success' });
 */
(function () {
  'use strict';

  const ICONS = {
    primary: 'bi-question-circle',
    danger:  'bi-exclamation-triangle-fill',
    warning: 'bi-exclamation-triangle-fill',
    success: 'bi-check-circle-fill',
    info:    'bi-info-circle-fill',
  };

  const HEADER_CLASS = {
    primary: 'bg-primary text-white',
    danger:  'bg-danger text-white',
    warning: 'bg-warning text-dark',
    success: 'bg-success text-white',
    info:    'bg-info text-dark',
  };

  const CLOSE_WHITE_TYPES = new Set(['primary', 'danger', 'success']);

  function ensureModal() {
    let el = document.getElementById('siiapDialogModal');
    if (el) return el;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
      <div class="modal fade" id="siiapDialogModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header" data-role="header">
              <h5 class="modal-title" data-role="title"></h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button>
            </div>
            <div class="modal-body">
              <div class="d-flex gap-3 align-items-start">
                <i class="bi" data-role="icon"></i>
                <div data-role="body" class="flex-grow-1"></div>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" data-bs-dismiss="modal" data-role="cancel">Cancelar</button>
              <button type="button" class="btn btn-primary" data-role="confirm">Aceptar</button>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(wrapper.firstElementChild);
    return document.getElementById('siiapDialogModal');
  }

  function show(opts) {
    return new Promise(resolve => {
      const modal = ensureModal();
      const header = modal.querySelector('[data-role=header]');
      const closeBtn = header.querySelector('.btn-close');
      const title = modal.querySelector('[data-role=title]');
      const body = modal.querySelector('[data-role=body]');
      const icon = modal.querySelector('[data-role=icon]');
      const cancelBtn = modal.querySelector('[data-role=cancel]');
      const confirmBtn = modal.querySelector('[data-role=confirm]');

      const type = opts.type || 'primary';
      const headerCls = HEADER_CLASS[type] || HEADER_CLASS.primary;

      header.className = `modal-header ${headerCls}`;
      closeBtn.classList.toggle('btn-close-white', CLOSE_WHITE_TYPES.has(type));

      title.textContent = opts.title || '';
      body.innerHTML = opts.html
        ? opts.html
        : (opts.message ? SIIAP.escapeHtml(opts.message).replace(/\n/g, '<br>') : '');

      const iconClass = opts.icon || ICONS[type] || ICONS.primary;
      icon.className = `bi ${iconClass} icon-2xl text-${type}`;

      const isAlert = opts.kind === 'alert';
      cancelBtn.classList.toggle('d-none', isAlert);
      cancelBtn.textContent = opts.cancelLabel || 'Cancelar';

      confirmBtn.textContent = opts.confirmLabel || (isAlert ? 'Entendido' : 'Aceptar');
      const btnKind = type === 'info' ? 'primary' : type;
      confirmBtn.className = `btn btn-${btnKind}`;

      const bsModal = bootstrap.Modal.getOrCreateInstance(modal);
      let resolved = false;

      const onConfirm = () => {
        resolved = true;
        bsModal.hide();
        resolve(true);
      };

      const onHidden = () => {
        if (!resolved) resolve(false);
        confirmBtn.removeEventListener('click', onConfirm);
        modal.removeEventListener('hidden.bs.modal', onHidden);
      };

      confirmBtn.addEventListener('click', onConfirm);
      modal.addEventListener('hidden.bs.modal', onHidden);
      bsModal.show();
    });
  }

  function normalizeConfirmOpts(opts) {
    if (typeof opts === 'string') opts = { message: opts };
    return Object.assign(
      { kind: 'confirm', type: 'warning', title: '¿Estás seguro?' },
      opts
    );
  }

  function normalizeAlertOpts(opts) {
    if (typeof opts === 'string') opts = { message: opts };
    return Object.assign(
      { kind: 'alert', type: 'info', title: 'Aviso' },
      opts
    );
  }

  window.siiapConfirm = (opts) => show(normalizeConfirmOpts(opts));
  window.siiapAlert   = (opts) => show(normalizeAlertOpts(opts));
})();
