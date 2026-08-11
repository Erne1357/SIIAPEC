// app/static/js/notifications.js
/**
 * Campana, panel desplegable y FAB móvil de notificaciones.
 *
 * Contrato de accesibilidad (no romper):
 *  - #notificationBell y #notificationFab son disclosures: aria-expanded /
 *    aria-controls los sincroniza SIIAP.syncExpanded (js/base.js).
 *  - #notificationDropdown es role="dialog": Escape lo cierra y devuelve el
 *    foco al disparador que lo abrió.
 *  - Cada .notification-item se renderiza con role="button" y tabindex="0"
 *    porque abre la notificación al activarse (WCAG 2.1.1).
 *  - Clases y atributos que el CSS y otros módulos esperan y que NO deben
 *    renombrarse: .notification-item, data-id, data-action-url,
 *    .notification-icon.bg-*, .btn-mark-read, .notification-empty.
 */

/** IDs de los dos badges (cabecera y FAB móvil). */
const NOTIFICATION_BADGE_IDS = ['notificationBadge', 'notificationBadgeMobile'];
/** IDs de los dos disparadores del panel. */
const NOTIFICATION_TRIGGER_IDS = ['notificationBell', 'notificationFab'];

class NotificationManager {
    constructor() {
        this.dropdownOpen = false;
        this.isMobile = window.innerWidth < 768;
        this.unreadCount = 0;
        this.lastTrigger = null;
        this.init();
    }

    async init() {
        await this.updateBadge();
        this.wireEvents();
        this.wireFabEvents();
        this.listenWebSocket();
        window.addEventListener('resize', () => {
            this.isMobile = window.innerWidth < 768;
        });
    }

    // ── WebSocket ─────────────────────────────────────────────────────────────

    listenWebSocket() {
        /**
         * Escucha el CustomEvent global 'siiap:notification:new' emitido por socket-client.js.
         * Actualiza el badge inmediatamente y muestra un toast.
         * Si el dropdown ya está abierto, recarga la lista.
         */
        window.addEventListener('siiap:notification:new', (e) => {
            const notification = e.detail?.notification;
            if (!notification) return;

            this.incrementBadge();
            this.showToast(notification);

            if (this.dropdownOpen) {
                this.loadUnreadNotifications();
            }

            // Permite que otros módulos reaccionen al tipo de notificación
            window.dispatchEvent(new CustomEvent('siiap:notification:received', {
                detail: notification
            }));
        });
    }

    // ── Badge ─────────────────────────────────────────────────────────────────

    async updateBadge() {
        try {
            const res = await window.apiClient.get('/api/v1/notifications/unread-count');
            const json = await res.json();
            this.setBadgeCount(json.data.count);
        } catch (error) {
            console.error('Error updating notification badge:', error);
        }
    }

    setBadgeCount(count) {
        this.paintBadges(Number(count) || 0);
    }

    /**
     * Pinta los dos badges y renombra los disparadores.
     * El número va en [data-count] para no borrar el texto oculto
     * ("notificaciones sin leer") que da la unidad al anuncio.
     * @param {number} count
     */
    paintBadges(count) {
        const value = count > 99 ? '99+' : String(count);
        const show = count > 0;

        NOTIFICATION_BADGE_IDS.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            const slot = el.querySelector('[data-count]');
            if (slot) {
                slot.textContent = value;
            } else {
                el.textContent = value;
            }
            el.classList.toggle('d-none', !show);
        });

        const triggerLabel = count === 0
            ? 'Notificaciones'
            : count === 1
                ? 'Notificaciones, 1 sin leer'
                : `Notificaciones, ${value} sin leer`;

        NOTIFICATION_TRIGGER_IDS.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.setAttribute('aria-label', triggerLabel);
        });

        this.unreadCount = count;
    }

    incrementBadge() {
        this.paintBadges((this.unreadCount || 0) + 1);
    }

    // ── Toast ─────────────────────────────────────────────────────────────────

    showToast(notification) {
        const level = {
            'critical': 'danger',
            'high': 'warning',
            'medium': 'info',
            'low': 'secondary',
        }[notification.priority] || 'info';

        window.dispatchEvent(new CustomEvent('flash', {
            detail: { level, message: notification.title }
        }));
    }

    // ── Dropdown ──────────────────────────────────────────────────────────────

    /** Devuelve los disparadores presentes en la página. */
    triggers() {
        return NOTIFICATION_TRIGGER_IDS
            .map(id => document.getElementById(id))
            .filter(Boolean);
    }

    /** Propaga aria-expanded/aria-controls a campana y FAB. */
    syncTriggers(open) {
        const dropdown = document.getElementById('notificationDropdown');
        this.triggers().forEach(trigger => {
            if (window.SIIAP && typeof window.SIIAP.syncExpanded === 'function') {
                window.SIIAP.syncExpanded(trigger, dropdown, open);
            } else {
                trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (dropdown) dropdown.classList.toggle('show', !!open);
            }
        });
    }

    wireEvents() {
        const bell = document.getElementById('notificationBell');
        const dropdown = document.getElementById('notificationDropdown');

        if (!bell || !dropdown) return;

        bell.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleDropdown();
        });

        document.addEventListener('click', (e) => {
            const insideTrigger = this.triggers().some(t => t.contains(e.target));
            if (!dropdown.contains(e.target) && !insideTrigger) {
                this.closeDropdown();
            }
        });

        // Escape cierra el panel y devuelve el foco a quien lo abrió (WCAG 2.1.2)
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.dropdownOpen) {
                e.stopPropagation();
                this.closeDropdown(true);
            }
        });

        const markAllBtn = document.getElementById('markAllReadBtn');
        if (markAllBtn) {
            markAllBtn.addEventListener('click', () => this.markAllAsRead());
        }
    }

    wireFabEvents() {
        const fab = document.getElementById('notificationFab');
        const dropdown = document.getElementById('notificationDropdown');
        if (!fab || !dropdown) return;

        fab.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleDropdown(true);
        });
    }

    async toggleDropdown(fromFab = false) {
        if (this.dropdownOpen) {
            this.closeDropdown(true);
        } else {
            await this.openDropdown(fromFab);
        }
    }

    async openDropdown(fromFab = false) {
        const dropdown = document.getElementById('notificationDropdown');
        if (!dropdown) return;

        this.lastTrigger = document.getElementById(
            fromFab ? 'notificationFab' : 'notificationBell'
        );

        await this.loadUnreadNotifications();
        dropdown.classList.toggle('from-fab', !!fromFab);
        this.dropdownOpen = true;
        this.syncTriggers(true);
        this.focusFirstItem(dropdown);
    }

    /** Mueve el foco al primer elemento accionable del panel recién abierto. */
    focusFirstItem(dropdown) {
        const first = dropdown.querySelector(
            '.notification-item, .notification-dropdown-footer a, #markAllReadBtn'
        );
        if (first && typeof first.focus === 'function') first.focus();
    }

    /**
     * @param {boolean} [restoreFocus] - true cuando el cierre lo pidió el
     *   usuario (Escape o segundo clic): el foco vuelve al disparador.
     */
    closeDropdown(restoreFocus = false) {
        const dropdown = document.getElementById('notificationDropdown');
        if (!dropdown) return;

        const wasOpen = this.dropdownOpen;
        dropdown.classList.remove('from-fab');
        this.dropdownOpen = false;
        this.syncTriggers(false);

        if (restoreFocus && wasOpen) {
            const trigger = this.lastTrigger || document.getElementById('notificationBell');
            if (trigger && typeof trigger.focus === 'function') trigger.focus();
        }
    }

    // ── Lista de notificaciones ───────────────────────────────────────────────

    /** Marca el contenedor como ocupado y anuncia el estado por la región viva. */
    setListBusy(container, busy, message) {
        if (window.SIIAP && typeof window.SIIAP.setBusy === 'function') {
            window.SIIAP.setBusy(container, busy, message ? { message } : undefined);
        } else {
            container.setAttribute('aria-busy', busy ? 'true' : 'false');
        }
    }

    async loadUnreadNotifications() {
        const container = document.getElementById('notificationsList');
        if (!container) return;

        this.setListBusy(container, true, 'Cargando notificaciones…');
        container.innerHTML = `
            <div class="notification-loading">
                <div class="spinner-border spinner-border-sm" aria-hidden="true"></div>
                <span class="visually-hidden">Cargando notificaciones…</span>
            </div>
        `;

        try {
            const res = await window.apiClient.get('/api/v1/notifications?unread_only=true&limit=5');
            const json = await res.json();
            const notifications = json.data.notifications;

            if (notifications.length === 0) {
                container.innerHTML = `
                    <div class="notification-empty">
                        <i class="bi bi-bell-slash" aria-hidden="true"></i>
                        <p class="mb-0">No hay notificaciones nuevas</p>
                    </div>
                `;
                this.setListBusy(container, false, 'No hay notificaciones nuevas.');
                return;
            }

            container.innerHTML = notifications.map(n => this.renderNotificationItem(n)).join('');

            container.querySelectorAll('.notification-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (!e.target.closest('.notification-actions') && !e.target.closest('.btn-mark-read')) {
                        this.handleNotificationClick(parseInt(item.dataset.id));
                    }
                });

                // El elemento es role="button": debe responder a Enter y Espacio.
                item.addEventListener('keydown', (e) => {
                    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
                    if (e.target !== item) return;   // deja pasar los botones internos
                    e.preventDefault();
                    this.handleNotificationClick(parseInt(item.dataset.id));
                });

                const markReadBtn = item.querySelector('.btn-mark-read');
                if (markReadBtn) {
                    markReadBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.markAsRead(parseInt(item.dataset.id));
                    });
                }
            });

            container.querySelectorAll('[data-respond-invitation]').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const notifId = parseInt(btn.dataset.notificationId);
                    const response = btn.dataset.respondInvitation;
                    await this.respondInvitation(notifId, response);
                });
            });

            const total = notifications.length;
            this.setListBusy(
                container,
                false,
                total === 1
                    ? '1 notificación sin leer en el panel.'
                    : `${total} notificaciones sin leer en el panel.`
            );

        } catch (error) {
            console.error('Error loading notifications:', error);
            container.innerHTML = `
                <div class="notification-empty">
                    <i class="bi bi-exclamation-circle" aria-hidden="true"></i>
                    <p class="mb-0">No se pudieron cargar las notificaciones</p>
                </div>
            `;
            this.setListBusy(container, false, 'No se pudieron cargar las notificaciones.');
        }
    }

    renderNotificationItem(notification) {
        // icon/color come from the local lookup tables below, never from the
        // payload, so they are the only interpolations left unescaped here.
        const icon = this.getIconForType(notification.type);
        const color = this.getColorForType(notification.type);
        const unreadClass = notification.is_read ? '' : 'unread';
        const timeAgo = this.getTimeAgo(notification.created_at);
        const hasLink = !!notification.action_url;
        const readState = notification.is_read ? '' : '<span class="visually-hidden">Sin leer. </span>';
        const linkHint = hasLink
            ? '<i class="bi bi-box-arrow-up-right ms-1" aria-hidden="true"></i>' +
              '<span class="visually-hidden">Abre la página relacionada.</span>'
            : '';

        let actionsHtml = '';

        if (notification.type === 'event_invitation' && !notification.is_read && notification.related_invitation_id) {
            actionsHtml = `
                <div class="notification-actions">
                    <button type="button" class="btn btn-sm btn-success"
                            data-respond-invitation="accepted"
                            data-notification-id="${SIIAP.escapeAttr(notification.id)}">
                        <i class="bi bi-check" aria-hidden="true"></i> Aceptar invitación
                    </button>
                    <button type="button" class="btn btn-sm btn-danger"
                            data-respond-invitation="rejected"
                            data-notification-id="${SIIAP.escapeAttr(notification.id)}">
                        <i class="bi bi-x" aria-hidden="true"></i> Rechazar invitación
                    </button>
                </div>
            `;
        }

        // role="button" + tabindex="0": el elemento se activa con ratón y con
        // teclado (Enter/Espacio). Sin esto, abrir una notificación era
        // imposible sin ratón (WCAG 2.1.1).
        return `
            <div class="notification-item ${unreadClass}" data-id="${SIIAP.escapeAttr(notification.id)}"
                 data-action-url="${SIIAP.escapeAttr(notification.action_url || '')}"
                 role="button" tabindex="0">
                <div class="d-flex gap-3">
                    <div class="notification-icon bg-${color}" aria-hidden="true">
                        <i class="${icon}"></i>
                    </div>
                    <div class="notification-content flex-grow-1">
                        ${readState}<strong>${SIIAP.escapeHtml(notification.title)}</strong>
                        <p class="mb-1">${SIIAP.escapeHtml(notification.message)}</p>
                        ${actionsHtml}
                        <small>${SIIAP.escapeHtml(timeAgo)}${linkHint}</small>
                    </div>
                    ${!notification.is_read ? `
                        <button type="button" class="btn-mark-read"
                                aria-label="Marcar como leída" title="Marcar como leída">
                            <i class="bi bi-check" aria-hidden="true"></i>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
    }

    getIconForType(type) {
        const icons = {
            'document_approved': 'bi bi-check-circle',
            'document_rejected': 'bi bi-x-circle',
            'coordinator_uploaded': 'bi bi-file-earmark-arrow-up',
            'extension_approved': 'bi bi-calendar-check',
            'extension_rejected': 'bi bi-calendar-x',
            'appointment_assigned': 'bi bi-calendar-event',
            'appointment_cancelled': 'bi bi-calendar-x',
            'appointment_change_accepted': 'bi bi-calendar-check',
            'event_invitation': 'bi bi-envelope',
            'password_reset': 'bi bi-shield-lock',
            'control_number_assigned': 'bi bi-person-badge',
            'account_deactivated': 'bi bi-person-x',
            'program_changed': 'bi bi-arrow-left-right',
            // Deliberación
            'deliberation_accepted': 'bi bi-mortarboard',
            'deliberation_rejected': 'bi bi-x-octagon',
            'deliberation_corrections': 'bi bi-pencil-square',
            // Aceptación
            'acceptance_docs_ready': 'bi bi-file-earmark-check',
            'enrollment_receipt_approved': 'bi bi-check2-circle',
            'enrollment_receipt_rejected': 'bi bi-x-circle',
            // Permanencia
            'semester_enrolled': 'bi bi-journal-check',
            'enrollment_status_changed': 'bi bi-journal-x',
            'permanence_doc_approved': 'bi bi-file-check',
            'permanence_doc_rejected': 'bi bi-file-x',
            'permanence_doc_submitted': 'bi bi-file-earmark-arrow-up',
            'leave_request_approved': 'bi bi-door-open',
            'leave_request_rejected': 'bi bi-door-closed',
            'leave_request_submitted': 'bi bi-door-open',
            'deadline_created': 'bi bi-calendar-plus',
            'deadline_opened': 'bi bi-calendar-check',
            'conacyt_deadlines_created': 'bi bi-calendar-range',
            'conacyt_scholarship_changed': 'bi bi-award',
            // Deliberación extra
            'deliberation_started': 'bi bi-hourglass-split',
            'deliberation_reset': 'bi bi-arrow-counterclockwise',
            // Admisión
            'document_submitted': 'bi bi-file-earmark-arrow-up',
            'enrollment_receipt_submitted': 'bi bi-file-earmark-arrow-up',
            'extension_request_submitted': 'bi bi-calendar-plus',
            // Diferimiento
            'deferral_applied': 'bi bi-calendar2-minus',
            'deferral_rejected': 'bi bi-calendar2-x',
            'deferral_reactivated': 'bi bi-calendar2-check',
            'deferral_request_received': 'bi bi-calendar2-plus',
            'deferral_expired': 'bi bi-hourglass-bottom',
            'deferral_expiring': 'bi bi-hourglass-split',
        };
        return icons[type] || 'bi bi-bell';
    }

    getColorForType(type) {
        const colors = {
            'document_approved': 'success',
            'extension_approved': 'success',
            'appointment_change_accepted': 'success',
            'deliberation_accepted': 'success',
            'enrollment_receipt_approved': 'success',
            'semester_enrolled': 'success',
            'deferral_reactivated': 'success',
            'acceptance_docs_ready': 'success',
            'document_rejected': 'danger',
            'extension_rejected': 'danger',
            'appointment_cancelled': 'danger',
            'account_deactivated': 'danger',
            'deliberation_rejected': 'danger',
            'enrollment_receipt_rejected': 'danger',
            'deferral_expired': 'danger',
            'enrollment_status_changed': 'danger',
            'appointment_assigned': 'primary',
            'event_invitation': 'primary',
            'deliberation_corrections': 'warning',
            'deferral_applied': 'warning',
            'deferral_rejected': 'warning',
            'deferral_expiring': 'warning',
            'control_number_assigned': 'info',
            'coordinator_uploaded': 'info',
            'password_reset': 'warning',
            'program_changed': 'warning',
            'deferral_request_received': 'info',
            // Permanencia
            'permanence_doc_approved': 'success',
            'permanence_doc_rejected': 'danger',
            'permanence_doc_submitted': 'info',
            'leave_request_approved': 'success',
            'leave_request_rejected': 'danger',
            'leave_request_submitted': 'warning',
            'deadline_created': 'primary',
            'deadline_opened': 'primary',
            'conacyt_deadlines_created': 'info',
            'conacyt_scholarship_changed': 'info',
            // Deliberación extra
            'deliberation_started': 'warning',
            'deliberation_reset': 'warning',
            // Admisión (coordinador)
            'document_submitted': 'info',
            'enrollment_receipt_submitted': 'info',
            'extension_request_submitted': 'warning',
        };
        return colors[type] || 'info';
    }

    getTimeAgo(dateString) {
        const parse = (window.SIIAP && window.SIIAP.parseDate) || null;
        const date = parse ? parse(dateString) : new Date(dateString);
        if (!date || isNaN(date.getTime())) return '';

        const now = new Date();
        const seconds = Math.floor((now - date) / 1000);
        const ago = (n, one, many) => `Hace ${n} ${n === 1 ? one : many}`;

        if (seconds < 60) return 'Hace unos segundos';
        if (seconds < 3600) return ago(Math.floor(seconds / 60), 'minuto', 'minutos');
        if (seconds < 86400) return ago(Math.floor(seconds / 3600), 'hora', 'horas');
        if (seconds < 604800) return ago(Math.floor(seconds / 86400), 'día', 'días');

        if (window.SIIAP && typeof window.SIIAP.formatDate === 'function') {
            return window.SIIAP.formatDate(dateString, 'short', '');
        }
        return date.toLocaleDateString('es-MX', {
            day: 'numeric',
            month: 'short',
            year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
        });
    }

    // ── Acciones ──────────────────────────────────────────────────────────────

    async handleNotificationClick(notificationId) {
        const item = document.querySelector(`.notification-item[data-id="${notificationId}"]`);
        const actionUrl = item?.dataset?.actionUrl;

        await this.markAsRead(notificationId);
        // Sin navegación el foco quedaría huérfano: vuelve al disparador.
        this.closeDropdown(!actionUrl);

        if (actionUrl) {
            await this._navigateToUrl(actionUrl);
        }
    }

    /**
     * Rejects anything that is not http(s) before it reaches location.href.
     * A stored `javascript:` action_url would otherwise run on click.
     * @param {string} url
     * @returns {boolean}
     */
    _isNavigableUrl(url) {
        try {
            const parsed = new URL(url, window.location.origin);
            return parsed.protocol === 'http:' || parsed.protocol === 'https:';
        } catch {
            return false;
        }
    }

    async _navigateToUrl(url) {
        if (!this._isNavigableUrl(url)) {
            window.dispatchEvent(new CustomEvent('flash', {
                detail: { level: 'warning', message: 'Esta página ya no está disponible.' }
            }));
            return;
        }

        try {
            const res = await fetch(url, { method: 'HEAD', credentials: 'same-origin' });
            if (res.ok) {
                window.location.href = url;
            } else {
                window.dispatchEvent(new CustomEvent('flash', {
                    detail: { level: 'warning', message: 'Esta página ya no está disponible.' }
                }));
            }
        } catch {
            // Si hay error de red, intentar navegar de todas formas
            window.location.href = url;
        }
    }

    async markAsRead(notificationId) {
        try {
            const res = await window.apiClient.patch(`/api/v1/notifications/${notificationId}/read`);
            if (res.ok) {
                // El anuncio lo emite loadUnreadNotifications con el recuento
                // resultante: dos anuncios seguidos se pisarían entre sí.
                await this.updateBadge();
                await this.loadUnreadNotifications();
            }
        } catch (error) {
            console.error('Error marking notification as read:', error);
        }
    }

    async markAllAsRead() {
        try {
            const res = await window.apiClient.post('/api/v1/notifications/mark-all-read');
            if (res.ok) {
                const json = await res.json();
                if (json.flash) {
                    json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
                }
                await this.updateBadge();
                await this.loadUnreadNotifications();
            }
        } catch (error) {
            console.error('Error marking all as read:', error);
        }
    }

    async respondInvitation(notificationId, response) {
        try {
            const res = await window.apiClient.post(
                `/api/v1/notifications/${notificationId}/respond-invitation`,
                { response }
            );
            const json = await res.json();
            if (json.flash) {
                json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
            }
            if (res.ok) {
                await this.updateBadge();
                await this.loadUnreadNotifications();
            }
        } catch (error) {
            console.error('Error responding to invitation:', error);
        }
    }
}

// ── Inicialización ────────────────────────────────────────────────────────────

let notificationManager = null;

function initNotifications() {
    if (document.querySelector('#notificationBell')) {
        notificationManager = new NotificationManager();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNotifications);
} else {
    initNotifications();
}
