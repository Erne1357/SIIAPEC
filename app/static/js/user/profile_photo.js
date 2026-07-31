/* app/static/js/user/profile_photo.js
 * Profile photo flow: upload (one-time or after coordinator authorization)
 * + request-change when not allowed.
 */
(function () {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  function flashMsg(level, msg) {
    if (typeof showFlash === 'function') showFlash(level, msg);
  }

  /** El nombre accesible y el tooltip se mantienen sincronizados: `title` solo
   *  no es fiable en móvil ni en VoiceOver (WCAG 4.1.2). */
  function setButtonLabel(btn, label) {
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }

  async function loadPhotoStatus() {
    const btn = document.getElementById('btnPhotoAction');
    const statusEl = document.getElementById('photoStatusText');
    if (!btn) return;

    try {
      const res = await fetch('/api/v1/users/me/photo-status');
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error?.message || 'Error');
      const d = json.data || {};
      btn.classList.remove('d-none');
      btn.dataset.canUpload = d.can_upload ? '1' : '0';
      btn.dataset.requested = d.photo_change_requested_at ? '1' : '0';

      if (d.can_upload) {
        btn.innerHTML = '<i class="bi bi-camera-fill" aria-hidden="true"></i>';
        setButtonLabel(btn, d.has_photo ? 'Subir una nueva foto de perfil' : 'Subir tu foto de perfil');
        btn.classList.remove('btn-warning');
        btn.classList.add('btn-primary');
        if (statusEl) {
          statusEl.innerHTML = d.has_photo
            ? '<span class="text-success-strong"><i class="bi bi-check-circle me-1" aria-hidden="true"></i>Cambio autorizado</span>'
            : '';
        }
      } else if (d.photo_change_requested_at) {
        btn.innerHTML = '<i class="bi bi-hourglass-split" aria-hidden="true"></i>';
        setButtonLabel(btn, 'Solicitud de cambio de foto pendiente');
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-warning');
        if (statusEl) {
          statusEl.innerHTML = '<span class="text-warning-strong"><i class="bi bi-clock-history me-1" aria-hidden="true"></i>Solicitud pendiente de revisión</span>';
        }
      } else {
        btn.innerHTML = '<i class="bi bi-pencil-fill" aria-hidden="true"></i>';
        setButtonLabel(btn, 'Solicitar el cambio de tu foto de perfil');
        btn.classList.remove('btn-warning');
        btn.classList.add('btn-primary');
        if (statusEl) statusEl.innerHTML = '';
      }
    } catch (e) {
      // Silently fail; button stays hidden
    }
  }

  function bindButton() {
    const btn = document.getElementById('btnPhotoAction');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const canUpload = btn.dataset.canUpload === '1';
      const requested = btn.dataset.requested === '1';
      if (canUpload) {
        const modalEl = document.getElementById('uploadPhotoModal');
        new bootstrap.Modal(modalEl).show();
      } else if (requested) {
        flashMsg('info', 'Tu solicitud de cambio sigue pendiente de revisión.');
      } else {
        const modalEl = document.getElementById('requestPhotoChangeModal');
        new bootstrap.Modal(modalEl).show();
      }
    });
  }

  function bindUploadForm() {
    const form = document.getElementById('uploadPhotoForm');
    if (!form) return;
    const fileInput = document.getElementById('photoFile');
    const preview = document.getElementById('photoPreview');

    fileInput?.addEventListener('change', () => {
      const f = fileInput.files?.[0];
      if (f && preview) {
        preview.src = URL.createObjectURL(f);
        preview.classList.remove('d-none');
      }
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = fileInput?.files?.[0];
      if (!f) return;

      const submitBtn = document.getElementById('btnUploadPhotoSubmit');
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Subiendo…';
      }

      const fd = new FormData();
      fd.append('photo', f);

      try {
        const res = await fetch('/api/v1/users/me/photo', {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
          body: fd,
        });
        const json = await res.json();
        if (json.flash) json.flash.forEach(fl => flashMsg(fl.level, fl.message));

        if (res.ok && !json.error) {
          const newUrl = json.data?.avatar_url;
          if (newUrl) {
            const img = document.getElementById('profileAvatarImg');
            if (img) img.src = newUrl + '?t=' + Date.now();
          }
          bootstrap.Modal.getInstance(document.getElementById('uploadPhotoModal'))?.hide();
          form.reset();
          document.getElementById('photoPreview')?.classList.add('d-none');
          loadPhotoStatus();
        }
      } catch (err) {
        flashMsg('danger', 'Error de red al subir foto.');
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<i class="bi bi-upload me-1" aria-hidden="true"></i>Subir foto';
        }
      }
    });
  }

  function bindRequestForm() {
    const form = document.getElementById('requestPhotoChangeForm');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const reason = document.getElementById('photoChangeReason')?.value.trim() || '';

      try {
        const res = await fetch('/api/v1/users/me/photo/request-change', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ reason }),
        });
        const json = await res.json();
        if (json.flash) json.flash.forEach(fl => flashMsg(fl.level, fl.message));

        if (res.ok && !json.error) {
          bootstrap.Modal.getInstance(document.getElementById('requestPhotoChangeModal'))?.hide();
          form.reset();
          loadPhotoStatus();
        }
      } catch (err) {
        flashMsg('danger', 'Error de red al enviar solicitud.');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindButton();
    bindUploadForm();
    bindRequestForm();
    loadPhotoStatus();
  });
})();
