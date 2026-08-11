// app/static/js/user/profile_notifications.js

class ProfileNotificationsManager {
    // La prioridad se muestra al usuario: siempre en español.
    static PRIORITY_LABELS = {
        low: 'Baja',
        normal: 'Normal',
        medium: 'Media',
        high: 'Alta',
        critical: 'Crítica',
    };

    constructor() {
        this.currentFilter = 'all';
        this.currentPage = 1;
        this.limit = 20;
        this.init();
    }

    init() {
        this.wireEvents();
        this.loadNotifications();
    }

    wireEvents() {
        // Botón marcar todas como leídas
        const markAllBtn = document.getElementById('markAllNotificationsRead');
        if (markAllBtn) {
            markAllBtn.addEventListener('click', () => this.markAllAsRead());
        }

        // Filtro de todas/no leídas
        const filterAllBtn = document.getElementById('filterAllNotifications');
        const filterUnreadBtn = document.getElementById('filterUnreadNotifications');
        
        if (filterAllBtn) {
            filterAllBtn.addEventListener('click', () => {
                this.currentFilter = 'all';
                this.currentPage = 1;
                this.updateFilterButtons();
                this.loadNotifications();
            });
        }

        if (filterUnreadBtn) {
            filterUnreadBtn.addEventListener('click', () => {
                this.currentFilter = 'unread';
                this.currentPage = 1;
                this.updateFilterButtons();
                this.loadNotifications();
            });
        }

        // Limpiar notificaciones leídas
        const clearReadBtn = document.getElementById('clearReadNotifications');
        if (clearReadBtn) {
            clearReadBtn.addEventListener('click', () => this.clearReadNotifications());
        }

        // Filtro por tipo
        const typeFilter = document.getElementById('filterNotificationType');
        if (typeFilter) {
            typeFilter.addEventListener('change', () => {
                this.currentPage = 1;
                this.loadNotifications();
            });
        }

        // Búsqueda
        const searchInput = document.getElementById('searchNotifications');
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener('input', () => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    this.currentPage = 1;
                    this.loadNotifications();
                }, 500);
            });
        }
    }

    /** El estado activo del toggle se expone con aria-pressed, no solo con la
     *  clase .active, que es puramente visual (WCAG 4.1.2). */
    updateFilterButtons() {
        const filterAllBtn = document.getElementById('filterAllNotifications');
        const filterUnreadBtn = document.getElementById('filterUnreadNotifications');
        const allActive = this.currentFilter === 'all';

        filterAllBtn?.classList.toggle('active', allActive);
        filterAllBtn?.setAttribute('aria-pressed', allActive ? 'true' : 'false');
        filterUnreadBtn?.classList.toggle('active', !allActive);
        filterUnreadBtn?.setAttribute('aria-pressed', allActive ? 'false' : 'true');
    }

    /** Marca la región asíncrona como ocupada/lista y lo anuncia (WCAG 4.1.3). */
    setBusy(el, busy, message) {
        if (window.SIIAP && typeof window.SIIAP.setBusy === 'function') {
            window.SIIAP.setBusy(el, busy, message ? { message } : undefined);
        } else if (el) {
            el.setAttribute('aria-busy', busy ? 'true' : 'false');
        }
    }

    async loadNotifications() {
        const container = document.getElementById('notificationsFull');
        if (!container) return;

        this.setBusy(container, true, 'Cargando notificaciones…');
        container.innerHTML = `
            <div class="notification-loading">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Cargando notificaciones…</span>
                </div>
            </div>`;

        try {
            const unreadOnly = this.currentFilter === 'unread';
            const offset = (this.currentPage - 1) * this.limit;
            
            // Usar ApiClient para construir URL segura
            const res = await window.apiClient.get(`/api/v1/notifications?unread_only=${unreadOnly}&limit=${this.limit}&offset=${offset}`);
            const json = await res.json();
            
            const notifications = json.data.notifications;
            const total = json.data.total;

            // Habilitar/deshabilitar botón de marcar todas
            const markAllBtn = document.getElementById('markAllNotificationsRead');
            if (markAllBtn) {
                markAllBtn.disabled = json.data.unread_count === 0;
            }

            if (notifications.length === 0) {
                const emptyMsg = unreadOnly
                    ? 'No tienes notificaciones sin leer.'
                    : 'Aún no has recibido notificaciones.';
                container.innerHTML = `
                    <div class="empty-state empty-state--compact">
                        <div class="empty-state__icon"><i class="bi bi-bell-slash" aria-hidden="true"></i></div>
                        <h3 class="empty-state__title">Sin notificaciones</h3>
                        <p class="empty-state__description">${emptyMsg}</p>
                    </div>
                `;
                this.renderPagination(0);
                this.setBusy(container, false, emptyMsg);
                return;
            }

            // Agrupar por fecha
            const grouped = this.groupByDate(notifications);
            
            let html = '';
            for (const [dateLabel, items] of Object.entries(grouped)) {
                html += `
                    <div class="notification-group">
                        <h6 class="notification-date-header">${dateLabel}</h6>
                        <div class="notification-items">
                            ${items.map(n => this.renderFullNotification(n)).join('')}
                        </div>
                    </div>
                `;
            }

            container.innerHTML = html;

            // Wire eventos
            this.wireNotificationEvents();

            // Renderizar paginación
            this.renderPagination(total);
            this.setBusy(container, false, `${notifications.length} notificación(es) cargadas.`);

        } catch (error) {
            console.error('Error loading notifications:', error);
            container.innerHTML = `
                <div class="empty-state empty-state--error empty-state--compact">
                    <div class="empty-state__icon"><i class="bi bi-exclamation-octagon" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">No pudimos cargar tus notificaciones</h3>
                    <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
                    <p class="empty-state__error-detail">${SIIAP.escapeHtml(error.message || 'Error de red')}</p>
                    <div class="empty-state__actions">
                        <button type="button" id="retryNotifications" class="btn btn-outline-primary">
                            <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Reintentar
                        </button>
                    </div>
                </div>
            `;
            document.getElementById('retryNotifications')
                ?.addEventListener('click', () => this.loadNotifications());
            this.setBusy(container, false, 'No se pudieron cargar las notificaciones.');
        }
    }

    groupByDate(notifications) {
        const groups = {};
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);

        for (const notif of notifications) {
            const date = new Date(notif.created_at);
            const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());

            let label;
            if (dateOnly.getTime() === today.getTime()) {
                label = 'Hoy';
            } else if (dateOnly.getTime() === yesterday.getTime()) {
                label = 'Ayer';
            } else {
                label = date.toLocaleDateString('es-MX', { 
                    day: 'numeric', 
                    month: 'long',
                    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
                });
            }

            if (!groups[label]) {
                groups[label] = [];
            }
            groups[label].push(notif);
        }

        return groups;
    }

    renderFullNotification(notification) {
        // icon/color come from the local lookup tables below, never from the
        // payload, so they are the only interpolations left unescaped here.
        const icon = this.getIconForType(notification.type);
        const color = this.getColorForType(notification.type);
        const unreadClass = notification.is_read ? '' : 'unread';
        const priorityClass = notification.priority;
        const time = new Date(notification.created_at).toLocaleTimeString('es-MX', {
            hour: '2-digit',
            minute: '2-digit'
        });
        const hasLink = !!notification.action_url;
        const priorityLabel = ProfileNotificationsManager.PRIORITY_LABELS[priorityClass] || priorityClass;

        let actionsHtml = '';

        if (notification.type === 'event_invitation' && !notification.is_read && notification.related_invitation_id) {
            actionsHtml = `
                <div class="notification-actions">
                    <button type="button" class="btn btn-sm btn-success respond-invitation"
                            data-notification-id="${SIIAP.escapeAttr(notification.id)}"
                            data-response="accepted">
                        <i class="bi bi-check" aria-hidden="true"></i> Aceptar
                    </button>
                    <button type="button" class="btn btn-sm btn-outline-danger respond-invitation"
                            data-notification-id="${SIIAP.escapeAttr(notification.id)}"
                            data-response="rejected">
                        <i class="bi bi-x" aria-hidden="true"></i> Rechazar
                    </button>
                </div>
            `;
        }

        const linkHint = hasLink
            ? `<span class="ms-2 text-muted small">
                   <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Ver página
               </span>`
            : '';

        return `
            <div class="notification-item ${unreadClass}${hasLink ? ' cursor-pointer' : ''}"
                 data-id="${SIIAP.escapeAttr(notification.id)}"
                 data-action-url="${SIIAP.escapeAttr(notification.action_url || '')}">
                <div class="notification-full-item">
                    <div class="notification-icon bg-${color}" aria-hidden="true">
                        <i class="${icon}"></i>
                    </div>
                    <div class="notification-content flex-grow-1">
                        <p class="fw-semibold mb-1">${SIIAP.escapeHtml(notification.title)}</p>
                        <p class="mb-2">${SIIAP.escapeHtml(notification.message)}</p>
                        ${actionsHtml}
                        <div class="notification-meta">
                            <span>
                                <span class="notification-priority ${SIIAP.escapeAttr(priorityClass)}">${SIIAP.escapeHtml(priorityLabel)}</span>
                                <span class="ms-2">${SIIAP.escapeHtml(time)}</span>
                                ${linkHint}
                            </span>
                            <div class="d-flex gap-1">
                                ${!notification.is_read ? `
                                    <button type="button" class="btn btn-sm btn-outline-primary mark-read-btn"
                                            data-id="${SIIAP.escapeAttr(notification.id)}">
                                        <i class="bi bi-check" aria-hidden="true"></i> Marcar leída
                                    </button>
                                ` : ''}
                                <button type="button" class="btn btn-sm btn-outline-danger delete-btn tap-target"
                                        data-id="${SIIAP.escapeAttr(notification.id)}"
                                        aria-label="Eliminar la notificación «${SIIAP.escapeAttr(notification.title)}»"
                                        title="Eliminar notificación">
                                    <i class="bi bi-trash" aria-hidden="true"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    wireNotificationEvents() {
        // Clic en el item → navegar si tiene action_url
        document.querySelectorAll('.notification-item[data-action-url]').forEach(item => {
            item.addEventListener('click', async (e) => {
                if (e.target.closest('button')) return;
                const actionUrl = item.dataset.actionUrl;
                if (!actionUrl) return;
                const id = parseInt(item.dataset.id);
                await this.markAsRead(id);
                await this._navigateToUrl(actionUrl);
            });
        });

        // Marcar como leída
        document.querySelectorAll('.mark-read-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.id);
                await this.markAsRead(id);
            });
        });

        // Eliminar
        document.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.id);
                const ok = await siiapConfirm({
                    type: 'danger',
                    title: 'Eliminar notificación',
                    message: '¿Eliminar esta notificación?',
                    confirmLabel: 'Eliminar',
                });
                if (ok) await this.deleteNotification(id);
            });
        });

        // Responder invitación
        document.querySelectorAll('.respond-invitation').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.notificationId);
                const response = btn.dataset.response;
                await this.respondInvitation(id, response);
            });
        });
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
            window.location.href = url;
        }
    }

    renderPagination(total) {
        const container = document.getElementById('notificationsPagination');
        if (!container) return;

        const totalPages = Math.ceil(total / this.limit);
        
        if (totalPages <= 1) {
            container.innerHTML = '';
            return;
        }

        let html = '';
        
        // Anterior
        html += `
            <li class="page-item ${this.currentPage === 1 ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${this.currentPage - 1}">Anterior</a>
            </li>
        `;

        // Páginas
        for (let i = 1; i <= totalPages; i++) {
            if (i === 1 || i === totalPages || (i >= this.currentPage - 2 && i <= this.currentPage + 2)) {
                html += `
                    <li class="page-item ${i === this.currentPage ? 'active' : ''}">
                        <a class="page-link" href="#" data-page="${i}">${i}</a>
                    </li>
                `;
            } else if (i === this.currentPage - 3 || i === this.currentPage + 3) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        // Siguiente
        html += `
            <li class="page-item ${this.currentPage === totalPages ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${this.currentPage + 1}">Siguiente</a>
            </li>
        `;

        container.innerHTML = html;

        // Wire eventos
        container.querySelectorAll('a.page-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const page = parseInt(link.dataset.page);
                if (page > 0 && page <= totalPages) {
                    this.currentPage = page;
                    this.loadNotifications();
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });
    }

    async markAsRead(id) {
        try {
            const res = await window.apiClient.patch(`/api/v1/notifications/${id}/read`);

            if (res.ok) {
                await this.loadNotifications();
                // Actualizar badge del header
                if (window.notificationManager) {
                    window.notificationManager.updateBadge();
                }
            }
        } catch (error) {
            console.error('Error:', error);
        }
    }

    async deleteNotification(id) {
        try {
            const res = await window.apiClient.delete(`/api/v1/notifications/${id}`);

            const json = await res.json();
            if (json.flash) {
                json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
            }

            if (res.ok) {
                await this.loadNotifications();
            }
        } catch (error) {
            console.error('Error:', error);
        }
    }

    async markAllAsRead() {
        try {
            const res = await window.apiClient.post(`/api/v1/notifications/mark-all-read`);

            const json = await res.json();
            if (json.flash) {
                json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
            }

            if (res.ok) {
                await this.loadNotifications();
                if (window.notificationManager) {
                    window.notificationManager.updateBadge();
                }
            }
        } catch (error) {
            console.error('Error:', error);
        }
    }

    async clearReadNotifications() {
        const ok = await siiapConfirm({
            type: 'danger',
            title: 'Limpiar notificaciones leídas',
            message: '¿Eliminar todas las notificaciones leídas? Esta acción no se puede deshacer.',
            confirmLabel: 'Sí, eliminar todas',
        });
        if (!ok) return;

        try {
            const res = await window.apiClient.post(`/api/v1/notifications/clear-read`);

            const json = await res.json();
            if (json.flash) {
                json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
            }

            if (res.ok) {
                await this.loadNotifications();
            }
        } catch (error) {
            console.error('Error:', error);
        }
    }

    async respondInvitation(notificationId, response) {
        try {
            const res = await window.apiClient.post(`/api/v1/notifications/${notificationId}/respond-invitation`, { response });

            const json = await res.json();
            if (json.flash) {
                json.flash.forEach(f => window.dispatchEvent(new CustomEvent('flash', { detail: f })));
            }

            if (res.ok) {
                await this.loadNotifications();
                if (window.notificationManager) {
                    window.notificationManager.updateBadge();
                }
            }
        } catch (error) {
            console.error('Error:', error);
        }
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
        };
        return colors[type] || 'info';
    }
}

// Inicializar solo si estamos en el tab de notificaciones
function initProfileNotifications() {
    const container = document.getElementById('notificationsFull');
    if (container) {
        new ProfileNotificationsManager();
    }
}

// Bootstrap tabs event
const notificationsTab = document.getElementById('notifications-tab');
if (notificationsTab) {
    notificationsTab.addEventListener('shown.bs.tab', initProfileNotifications);
    
    // Si el tab está activo al cargar
    if (notificationsTab.classList.contains('active')) {
        initProfileNotifications();
    }
}