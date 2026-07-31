// app/static/js/admin/settings/academic_periods.js

class AcademicPeriodsManager {
    constructor() {
        this.API_BASE = '/api/v1/academic-periods';
        this.periods = [];
        this.currentDeleteId = null;
        this.modalPeriod = null;
        this.modalDelete = null;
        this.init();
    }

    init() {
        // Flag para distinguir apertura programática (editar) de apertura por
        // data-bs-toggle (nuevo periodo). openEditModal lo setea a true antes
        // de invocar show().
        this._openingForEdit = false;

        // Inicializar modales de Bootstrap
        const modalPeriodEl = document.getElementById('modalPeriod');
        const modalDeleteEl = document.getElementById('modalDelete');

        if (modalPeriodEl) {
            this.modalPeriod = new bootstrap.Modal(modalPeriodEl);
        }
        if (modalDeleteEl) {
            this.modalDelete = new bootstrap.Modal(modalDeleteEl);
        }

        this.wireEvents();
        this.loadPeriods();
    }

    wireEvents() {
        // Formulario de periodo
        const formPeriod = document.getElementById('formPeriod');
        if (formPeriod) {
            formPeriod.addEventListener('submit', (e) => this.handleSavePeriod(e));
        }

        // Boton confirmar eliminacion
        const btnConfirmDelete = document.getElementById('btnConfirmDelete');
        if (btnConfirmDelete) {
            btnConfirmDelete.addEventListener('click', () => this.handleConfirmDelete());
        }

        // Reset form cuando se abre el modal para nuevo periodo (NO si es edición)
        const modalPeriodEl = document.getElementById('modalPeriod');
        if (modalPeriodEl) {
            modalPeriodEl.addEventListener('show.bs.modal', () => {
                if (this._openingForEdit) {
                    this._openingForEdit = false;
                    return;
                }
                this.resetForm();
                document.getElementById('modalPeriodLabel').textContent = 'Nuevo periodo académico';
            });
        }
    }

    async loadPeriods() {
        const container = document.getElementById('periodsContainer');
        const alertNoPeriods = document.getElementById('alertNoPeriods');

        container.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Cargando periodos…</span>
                </div>
                <p class="text-muted mt-2">Cargando periodos…</p>
            </div>
        `;

        try {
            const response = await fetch(this.API_BASE, {
                headers: {
                    'X-CSRFToken': this.getCsrf()
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();

            if (result.error) {
                this.showFlash('danger', result.error.message);
                return;
            }

            this.periods = result.data || [];
            this.renderPeriods();

        } catch (error) {
            console.error('Error loading periods:', error);
            container.innerHTML = `
                <div class="empty-state empty-state--error">
                    <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                    <p class="empty-state__title">No se pudieron cargar los periodos académicos</p>
                    <p class="empty-state__description">Comprueba tu conexión y vuelve a intentarlo.</p>
                    <p class="empty-state__error-detail">${error.message}</p>
                </div>
            `;
        }
    }

    renderPeriods() {
        const container = document.getElementById('periodsContainer');
        const alertNoPeriods = document.getElementById('alertNoPeriods');
        const template = document.getElementById('templatePeriodCard');

        if (this.periods.length === 0) {
            container.innerHTML = '';
            alertNoPeriods.classList.remove('d-none');
            return;
        }

        alertNoPeriods.classList.add('d-none');
        container.innerHTML = '';

        // Determinar el siguiente periodo cronológico al activo (para el botón de transición)
        const activePeriod = this.periods.find(p => p.is_active);
        let nextPeriod = null;
        if (activePeriod) {
            // Ordenar los no-activos por código de forma ascendente y tomar el primero mayor al activo
            const sorted = this.periods
                .filter(p => !p.is_active)
                .sort((a, b) => a.id - b.id);
            nextPeriod = sorted.find(p => p.id > activePeriod.id) || null;
        }

        this.periods.forEach(period => {
            const card = template.content.cloneNode(true);
            const cardEl = card.querySelector('.period-card');

            cardEl.dataset.periodId = period.id;
            card.querySelector('.period-code').textContent = period.code;
            card.querySelector('.period-name').textContent = period.name;

            // Fechas
            card.querySelector('.admission-dates').textContent =
                `${this.formatDate(period.admission_start_date)} - ${this.formatDate(period.admission_end_date)}`;
            card.querySelector('.class-dates').textContent =
                `${this.formatDate(period.start_date)} - ${this.formatDate(period.end_date)}`;

            // Indicador y estado
            const indicator = card.querySelector('.period-indicator');
            const badge = card.querySelector('.period-status-badge');

            if (period.is_active) {
                indicator.classList.add('active');
                cardEl.classList.add('is-active');
                this.paintStatusBadge(badge, 'accepted', 'check-circle-fill', 'Activo');

                // Inyectar botón de transición si el módulo está disponible
                if (window.PeriodTransition && typeof window.PeriodTransition.getButtonHtml === 'function') {
                    const btnHtml = window.PeriodTransition.getButtonHtml(period, nextPeriod);
                    if (btnHtml) {
                        const desktopContainer = card.querySelector('.btn-transition-container');
                        if (desktopContainer) desktopContainer.innerHTML = btnHtml + ' ';
                        const mobileContainer = card.querySelector('.btn-transition-container-mobile');
                        if (mobileContainer) mobileContainer.innerHTML = btnHtml;
                    }
                }
            } else {
                indicator.classList.add('inactive');
                const meta = this.getStatusMeta(period.status);
                this.paintStatusBadge(badge, meta.key, meta.icon, meta.label);
                // Mostrar boton activar solo para periodos no activos
                card.querySelectorAll('.btn-activate').forEach(btn => btn.classList.remove('d-none'));
            }

            // Event listeners para los botones
            card.querySelectorAll('.btn-edit').forEach(btn => {
                btn.addEventListener('click', () => this.openEditModal(period));
            });

            card.querySelectorAll('.btn-delete').forEach(btn => {
                btn.addEventListener('click', () => this.openDeleteModal(period));
            });

            card.querySelectorAll('.btn-activate').forEach(btn => {
                btn.addEventListener('click', () => this.activatePeriod(period.id));
            });

            container.appendChild(card);
        });
    }

    formatDate(dateStr) {
        if (!dateStr) return '';
        if (window.SIIAP && window.SIIAP.formatDate) {
            return window.SIIAP.formatDate(dateStr, 'short', '');
        }
        const date = new Date(dateStr + 'T00:00:00');
        return date.toLocaleDateString('es-MX', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
        });
    }

    // Estado del periodo -> modificador del componente compartido .status-badge.
    getStatusMeta(status) {
        const meta = {
            'upcoming':          { key: 'in-progress', icon: 'calendar-plus',    label: 'Próximo' },
            'active':            { key: 'accepted',    icon: 'check-circle-fill', label: 'Activo' },
            'admission_closed':  { key: 'deliberation', icon: 'door-closed',     label: 'Admisión cerrada' },
            'completed':         { key: 'enrolled',    icon: 'flag-fill',        label: 'Completado' }
        };
        return meta[status] || { key: 'deferred', icon: 'circle', label: status };
    }

    // Pinta el chip de estado con el markup del componente (icono + texto),
    // para no depender solo del color (WCAG 1.4.1).
    paintStatusBadge(badge, key, icon, label) {
        if (!badge) return;
        badge.className = `period-status-badge status-badge status-badge--${key} status-badge--sm`;
        badge.innerHTML = '';
        const i = document.createElement('i');
        i.className = `bi bi-${icon}`;
        i.setAttribute('aria-hidden', 'true');
        const text = document.createElement('span');
        text.textContent = label;
        badge.append(i, text);
    }

    resetForm() {
        const form = document.getElementById('formPeriod');
        if (form) {
            form.reset();
        }
        document.getElementById('periodId').value = '';
    }

    openEditModal(period) {
        // Marcar que abrimos en modo edición ANTES de show, para que el
        // listener de show.bs.modal no resetee el formulario.
        this._openingForEdit = true;
        document.getElementById('modalPeriodLabel').textContent = 'Editar periodo académico';
        document.getElementById('periodId').value = period.id;
        document.getElementById('periodCode').value = period.code;
        document.getElementById('periodName').value = period.name;
        document.getElementById('startDate').value = period.start_date;
        document.getElementById('endDate').value = period.end_date;
        document.getElementById('admissionStartDate').value = period.admission_start_date;
        document.getElementById('admissionEndDate').value = period.admission_end_date;
        this.modalPeriod.show();
    }

    openDeleteModal(period) {
        this.currentDeleteId = period.id;
        document.getElementById('deletePeriodCode').textContent = period.code;
        this.modalDelete.show();
    }

    async handleSavePeriod(e) {
        e.preventDefault();

        const btn = document.getElementById('btnSavePeriod');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Guardando…';

        const periodId = document.getElementById('periodId').value;
        const data = {
            code: document.getElementById('periodCode').value.trim(),
            name: document.getElementById('periodName').value.trim(),
            start_date: document.getElementById('startDate').value,
            end_date: document.getElementById('endDate').value,
            admission_start_date: document.getElementById('admissionStartDate').value,
            admission_end_date: document.getElementById('admissionEndDate').value
        };

        console.log('Saving period data:', data);

        const url = periodId ? `${this.API_BASE}/${periodId}` : this.API_BASE;
        const method = periodId ? 'PATCH' : 'POST';
        
        console.log(`Sending ${method} request to ${url} with data:`, data);

        try {
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrf()
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (result.error) {
                this.showFlash('danger', result.error.message);
                return;
            }

            this.showFlash('success', result.flash?.[0]?.message || 'Periodo guardado exitosamente');
            this.modalPeriod.hide();
            this.loadPeriods();

        } catch (error) {
            console.error('Error saving period:', error);
            this.showFlash('danger', 'Error al guardar el periodo');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    async activatePeriod(periodId) {
        try {
            const response = await fetch(`${this.API_BASE}/${periodId}/activate`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrf()
                }
            });

            const result = await response.json();

            if (result.error) {
                this.showFlash('danger', result.error.message);
                return;
            }

            this.showFlash('success', result.flash?.[0]?.message || 'Periodo activado exitosamente');
            this.loadPeriods();

        } catch (error) {
            console.error('Error activating period:', error);
            this.showFlash('danger', 'Error al activar el periodo');
        }
    }

    async handleConfirmDelete() {
        if (!this.currentDeleteId) return;

        const btn = document.getElementById('btnConfirmDelete');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Eliminando…';

        try {
            const response = await fetch(`${this.API_BASE}/${this.currentDeleteId}`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': this.getCsrf()
                }
            });

            const result = await response.json();

            if (result.error) {
                this.showFlash('danger', result.error.message);
                return;
            }

            this.showFlash('success', result.flash?.[0]?.message || 'Periodo eliminado exitosamente');
            this.modalDelete.hide();
            this.currentDeleteId = null;
            this.loadPeriods();

        } catch (error) {
            console.error('Error deleting period:', error);
            this.showFlash('danger', 'Error al eliminar el periodo');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    showFlash(level, message) {
        // Usar el sistema de flash existente
        window.dispatchEvent(new CustomEvent('flash', {
            detail: { level, message }
        }));
    }

    getCsrf() {
        const el = document.querySelector('meta[name="csrf-token"]');
        return el ? el.getAttribute('content') : '';
    }
}

// Inicializar
let academicPeriodsManager = null;

function initAcademicPeriods() {
    if (document.getElementById('periodsContainer')) {
        academicPeriodsManager = new AcademicPeriodsManager();
        // Exponer en window para que period_transition.js pueda invocar refresh
        window.academicPeriodsManager = academicPeriodsManager;

        // Tiempo real: otro admin activó/desactivó un periodo → refrescar lista
        window.addEventListener('siiap:academic_period:changed', (e) => {
            const d = e.detail || {};
            if (typeof showFlash === 'function') {
                const action = d.action === 'activated' ? 'activado' : 'desactivado';
                showFlash('info', `Periodo "${d.name || d.code}" ${action} por otro admin. Actualizando lista...`);
            }
            if (academicPeriodsManager) academicPeriodsManager.loadPeriods();
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAcademicPeriods);
} else {
    initAcademicPeriods();
}
