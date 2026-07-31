// app/static/js/force_password_change.js
/**
 * Gestiona el modal de cambio de contraseña obligatorio
 * Se muestra cuando el usuario tiene must_change_password=true
 */

(function() {
    'use strict';

    // Función para obtener el CSRF token
    const getCsrf = () => {
        const el = document.querySelector('meta[name="csrf-token"]');
        return el ? el.getAttribute('content') : '';
    };

    // Función para mostrar mensajes flash desde JS
    const showFlash = (level, message) => {
        window.dispatchEvent(new CustomEvent('flash', {
            detail: { level, message }
        }));
    };

    // Clase principal para manejar el cambio de contraseña
    class ForcePasswordChange {
        constructor() {
            this.modal = null;
            this.form = null;
            this.submitBtn = null;
            this.isSubmitting = false;
            
            this.init();
        }

        init() {
            // Verificar si el usuario debe cambiar contraseña
            this.checkPasswordChangeRequired();
        }

        async checkPasswordChangeRequired() {
            try {
                const response = await fetch('/api/v1/auth/me', {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: {
                        'X-CSRFToken': getCsrf()
                    }
                });

                const json = await response.json();
                
                // Sincronizar token CSRF si el servidor devuelve uno diferente
                if (json.data && json.data.csrf_token) {
                    const metaTag = document.querySelector('meta[name="csrf-token"]');
                    if (metaTag && metaTag.getAttribute('content') !== json.data.csrf_token) {
                        metaTag.setAttribute('content', json.data.csrf_token);
                        console.log('Token CSRF actualizado desde /me');
                    }
                }
                
                if (json.data && json.data.must_change_password === true) {
                    this.showModal();
                }
            } catch (error) {
                console.error('Error verificando estado de contraseña:', error);
            }
        }

        showModal() {
            const modalElement = document.getElementById('forcePasswordChangeModal');
            
            if (!modalElement) {
                console.error('Modal de cambio de contraseña no encontrado');
                return;
            }

            this.modal = new bootstrap.Modal(modalElement, {
                backdrop: 'static',
                keyboard: false
            });

            this.form = document.getElementById('forcePasswordChangeForm');
            this.submitBtn = document.getElementById('btnChangePassword');

            if (this.form) {
                this.form.addEventListener('submit', (e) => this.handleSubmit(e));
                
                // Validación en tiempo real
                const newPassword = document.getElementById('new_password');
                const confirmPassword = document.getElementById('confirm_password');
                
                if (newPassword) {
                    newPassword.addEventListener('input', () => this.validatePasswordStrength());
                }
                
                if (confirmPassword) {
                    confirmPassword.addEventListener('input', () => this.validatePasswordMatch());
                }
            }

            this.modal.show();
        }

        validatePasswordStrength() {
            const password = document.getElementById('new_password')?.value || '';
            const feedback = document.getElementById('password_strength_feedback');
            
            if (!feedback) return true;

            const requirements = {
                length: password.length >= 8,
                uppercase: /[A-Z]/.test(password),
                lowercase: /[a-z]/.test(password),
                number: /\d/.test(password),
                special: /[!@#$%^&*(),.?":{}|<>]/.test(password)
            };

            const allValid = Object.values(requirements).every(v => v);

            /**
             * Una fila de requisito. El icono (no solo el color) indica el
             * estado y el texto oculto lo verbaliza: WCAG 1.4.1.
             */
            const rule = (ok, text) => `
                <li class="${ok ? 'text-success' : ''}">
                    <i class="bi ${ok ? 'bi-check-circle-fill' : 'bi-circle'} me-1" aria-hidden="true"></i>
                    <span class="visually-hidden">${ok ? 'Cumplido:' : 'Pendiente:'}</span>${text}
                </li>
            `;

            feedback.innerHTML = `
                <div class="small">
                    <strong>Requisitos de contraseña:</strong>
                    <ul class="list-unstyled mb-0 mt-1">
                        ${rule(requirements.length, 'Mínimo 8 caracteres')}
                        ${rule(requirements.uppercase, 'Una letra mayúscula')}
                        ${rule(requirements.lowercase, 'Una letra minúscula')}
                        ${rule(requirements.number, 'Un número')}
                        ${rule(requirements.special, 'Un carácter especial')}
                    </ul>
                </div>
            `;

            return allValid;
        }

        validatePasswordMatch() {
            const newPassword = document.getElementById('new_password')?.value || '';
            const confirmPassword = document.getElementById('confirm_password')?.value || '';
            const feedback = document.getElementById('password_match_feedback');

            if (!feedback) return true;

            if (confirmPassword.length === 0) {
                feedback.textContent = '';
                return false;
            }

            if (newPassword === confirmPassword) {
                feedback.innerHTML =
                    '<i class="bi bi-check-circle-fill me-1" aria-hidden="true"></i>Las contraseñas coinciden';
                feedback.className = 'small text-success mt-1';
                return true;
            }

            feedback.innerHTML =
                '<i class="bi bi-exclamation-circle-fill me-1" aria-hidden="true"></i>Las contraseñas no coinciden';
            feedback.className = 'small text-danger mt-1';
            return false;
        }

        async handleSubmit(e) {
            e.preventDefault();

            if (this.isSubmitting) return;

            // Validar antes de enviar
            if (!this.validatePasswordStrength()) {
                showFlash('danger', 'La contraseña no cumple con los requisitos de seguridad');
                return;
            }

            if (!this.validatePasswordMatch()) {
                showFlash('danger', 'Las contraseñas no coinciden');
                return;
            }

            this.isSubmitting = true;
            this.submitBtn.disabled = true;
            this.submitBtn.innerHTML =
                '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Cambiando…';

            const formData = new FormData(this.form);
            const data = {
                current_password: formData.get('current_password'),
                new_password: formData.get('new_password'),
                confirm_password: formData.get('confirm_password')
            };

            try {
                const response = await fetch('/api/v1/auth/change-password', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrf()
                    },
                    body: JSON.stringify(data)
                });

                const json = await response.json();

                // Mostrar mensajes flash
                if (json.flash && Array.isArray(json.flash)) {
                    json.flash.forEach(f => showFlash(f.level, f.message));
                }

                if (response.ok) {
                    // Contraseña cambiada exitosamente
                    this.modal.hide();
                    
                    // Recargar página después de 1 segundo para aplicar cambios
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                } else {
                    // Error al cambiar contraseña
                    this.isSubmitting = false;
                    this.submitBtn.disabled = false;
                    this.submitBtn.textContent = 'Cambiar Contraseña';
                }
            } catch (error) {
                console.error('Error al cambiar contraseña:', error);
                showFlash('danger', 'Error al cambiar la contraseña. Intenta de nuevo.');
                
                this.isSubmitting = false;
                this.submitBtn.disabled = false;
                this.submitBtn.textContent = 'Cambiar Contraseña';
            }
        }
    }

    // Inicializar cuando el DOM esté listo
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new ForcePasswordChange();
        });
    } else {
        new ForcePasswordChange();
    }
})();

/*
 * El toggle de visibilidad de contraseña vive ahora en
 * js/utils/password-toggle.js (contrato data-toggle-password="#id"), que ya
 * gestiona aria-pressed, aria-label y el icono. La implementación local
 * (togglePasswordVisibility + delegación sobre .js-toggle-password) se
 * eliminó para no duplicar handlers sobre el mismo botón.
 */