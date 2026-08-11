// app/static/js/admin/settings/emails.js

class EmailConfigManager {
    constructor() {
        this.init();
    }

    init() {
        this.wireEvents();
        this.loadPendingEmails();
        this.listenWebSocket();
    }

    // ── WebSocket ─────────────────────────────────────────────────────────────

    listenWebSocket() {
        /**
         * Escucha 'siiap:email:queue_update' de socket-client.js.
         * Actualiza contadores y recarga la lista sin recargar la página.
         */
        window.addEventListener('siiap:email:queue_update', (e) => {
            const { pending, failed } = e.detail || {};
            this.updateQueueCounters(pending, failed);
            this.loadPendingEmails();
        });
    }

    updateQueueCounters(pending, failed) {
        const elPending = document.getElementById('statPending');
        const elFailed = document.getElementById('statFailed');
        if (elPending !== null && pending !== undefined) elPending.textContent = pending;
        if (elFailed !== null && failed !== undefined) elFailed.textContent = failed;
    }

    // ── Eventos de UI ─────────────────────────────────────────────────────────

    wireEvents() {
        const btnDisconnect = document.getElementById('btnDisconnect');
        if (btnDisconnect) btnDisconnect.addEventListener('click', () => this.disconnect());

        const btnProcessQueue = document.getElementById('btnProcessQueue');
        if (btnProcessQueue) btnProcessQueue.addEventListener('click', () => this.processQueue());

        const btnRetryFailed = document.getElementById('btnRetryFailed');
        if (btnRetryFailed) btnRetryFailed.addEventListener('click', () => this.retryFailed());

        const btnSendTest = document.getElementById('btnSendTest');
        if (btnSendTest) btnSendTest.addEventListener('click', () => this.sendTest());

        const btnRefreshList = document.getElementById('btnRefreshList');
        if (btnRefreshList) btnRefreshList.addEventListener('click', () => this.loadPendingEmails());
    }

    // ── Acciones ──────────────────────────────────────────────────────────────

    async disconnect() {
        const ok = await siiapConfirm({
            type: 'warning',
            title: 'Desconectar cuenta',
            message: '¿Desconectar la cuenta de Microsoft? Los correos pendientes no se enviarán hasta que vuelvas a conectar.',
            confirmLabel: 'Sí, desconectar',
        });
        if (!ok) return;

        try {
            const res = await fetch('/admin/emails/logout', {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCsrf() }
            });
            if (res.ok) {
                window.location.reload();
            } else {
                this.showFlash('error', 'Error al desconectar');
            }
        } catch (error) {
            console.error('Error:', error);
            this.showFlash('error', 'Error de conexión');
        }
    }

    async processQueue() {
        const btn = document.getElementById('btnProcessQueue');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Procesando...';

        try {
            const res = await fetch('/admin/emails/process-queue', {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCsrf() }
            });
            const json = await res.json();
            if (res.ok && json.ok) {
                const r = json.result;
                this.showFlash('success', `Procesados: ${r.processed}, Enviados: ${r.sent}, Fallidos: ${r.failed}`);
                await this.loadPendingEmails();
            } else if (json.error) {
                this.showFlash('error', json.error);
            }
        } catch (error) {
            console.error('Error:', error);
            this.showFlash('error', 'Error al procesar cola');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    async retryFailed() {
        const btn = document.getElementById('btnRetryFailed');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Reintentando...';

        try {
            const res = await fetch('/admin/emails/retry-failed', {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCsrf() }
            });
            const json = await res.json();
            if (res.ok && json.ok) {
                const r = json.result;
                this.showFlash('success', `Reintentados: ${r.processed}, Enviados: ${r.sent}`);
                await this.loadPendingEmails();
            } else if (json.error) {
                this.showFlash('error', json.error);
            }
        } catch (error) {
            console.error('Error:', error);
            this.showFlash('error', 'Error al reintentar');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    async sendTest() {
        const btn = document.getElementById('btnSendTest');
        const originalText = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Enviando...';
        }

        try {
            const res = await fetch('/api/v1/emails/test', {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCsrf() }
            });
            const json = await res.json();
            // El backend devuelve { data, flash:[{level,message}] }.
            const f = (json.flash || [])[0];
            if (res.ok && json.data && json.data.sent) {
                this.showFlash('success', f ? f.message : 'Correo de prueba enviado');
            } else {
                this.showFlash('error', f ? f.message : 'Error al enviar correo de prueba');
            }
        } catch (error) {
            console.error('Error:', error);
            this.showFlash('error', 'Error de conexión al enviar correo de prueba');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    }

    // ── Lista de correos pendientes ───────────────────────────────────────────

    async loadPendingEmails() {
        const container = document.getElementById('pendingEmailsList');
        if (!container) return;

        if (window.SIIAP && window.SIIAP.setBusy) {
            window.SIIAP.setBusy(container, true, { message: 'Cargando correos pendientes…' });
        }

        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Cargando correos…</span>
                </div>
            </div>
        `;

        try {
            const res = await fetch('/admin/emails/queue?per_page=20');
            const json = await res.json();

            if (!res.ok) throw new Error('Error al cargar correos');

            const emails = json.emails;

            if (emails.length === 0) {
                container.innerHTML = `
                    <div class="empty-state empty-state--compact">
                        <div class="empty-state__icon"><i class="bi bi-inbox" aria-hidden="true"></i></div>
                        <p class="empty-state__title">Sin correos pendientes</p>
                        <p class="empty-state__description">La cola está vacía: no hay nada por enviar.</p>
                    </div>
                `;
                this.setListIdle(container, 'La cola de correos está vacía.');
                return;
            }

            container.innerHTML = emails.map(email => this.renderEmailItem(email)).join('');
            this.setListIdle(
                container,
                emails.length === 1 ? '1 correo en la cola.' : `${emails.length} correos en la cola.`
            );

        } catch (error) {
            console.error('Error:', error);
            container.innerHTML = `
                <div class="empty-state empty-state--compact empty-state--error">
                    <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                    <p class="empty-state__title">No se pudo cargar la lista de correos</p>
                    <p class="empty-state__description">Comprueba tu conexión y vuelve a intentarlo.</p>
                </div>
            `;
            this.setListIdle(container, 'No se pudo cargar la lista de correos.');
        }
    }

    // Marca la región como ya cargada y anuncia el resultado.
    setListIdle(container, message) {
        if (window.SIIAP && window.SIIAP.setBusy) {
            window.SIIAP.setBusy(container, false, { message });
        } else {
            container.setAttribute('aria-busy', 'false');
        }
    }

    renderEmailItem(email) {
        // El chip usa el componente compartido .status-badge:
        // pendiente -> --pending, enviado -> --accepted, fallido -> --rejected.
        const STATUS_MAP = {
            pending: { key: 'pending',  label: 'Pendiente' },
            sent:    { key: 'accepted', label: 'Enviado' },
            failed:  { key: 'rejected', label: 'Fallido' },
        };
        const meta = STATUS_MAP[email.status] || { key: 'pending', label: email.status };
        const statusChip = (window.SIIAP && window.SIIAP.statusBadge)
            ? window.SIIAP.statusBadge(meta.key, meta.label)
            : `<span class="status-badge status-badge--${meta.key}"><span>${SIIAP.escapeHtml(meta.label)}</span></span>`;

        const createdAt = (window.SIIAP && window.SIIAP.formatDateTime)
            ? window.SIIAP.formatDateTime(email.created_at, 'short')
            : new Date(email.created_at).toLocaleString('es-MX', {
                day: '2-digit', month: 'short', year: 'numeric',
                hour: '2-digit', minute: '2-digit'
            });

        const errorHtml = email.error_message ? `
            <div class="mt-2">
                <small class="text-danger">
                    <i class="bi bi-exclamation-circle me-1" aria-hidden="true"></i>
                    <strong>Error:</strong> ${SIIAP.escapeHtml(email.error_message)}
                </small>
            </div>
        ` : '';

        return `
            <div class="email-item">
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
                            ${statusChip}
                            ${email.attempts > 0 ? `
                                <span class="badge bg-warning">
                                    ${SIIAP.escapeHtml(email.attempts)} ${email.attempts === 1 ? 'intento' : 'intentos'}
                                </span>
                            ` : ''}
                        </div>
                        <strong>${SIIAP.escapeHtml(email.subject)}</strong>
                        <div class="email-meta">
                            <span><i class="bi bi-envelope me-1" aria-hidden="true"></i>${SIIAP.escapeHtml(email.recipient_email)}</span>
                            <span><i class="bi bi-clock me-1" aria-hidden="true"></i>${createdAt}</span>
                        </div>
                        ${errorHtml}
                    </div>
                </div>
            </div>
        `;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    showFlash(level, message) {
        window.dispatchEvent(new CustomEvent('flash', { detail: { level, message } }));
    }

    getCsrf() {
        const el = document.querySelector('meta[name="csrf-token"]');
        return el ? el.getAttribute('content') : '';
    }
}

// ── Inicialización ────────────────────────────────────────────────────────────

let emailConfigManager = null;

function initEmailConfig() {
    emailConfigManager = new EmailConfigManager();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEmailConfig);
} else {
    initEmailConfig();
}
