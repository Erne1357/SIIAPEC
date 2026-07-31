/**
 * Deliberation Management for Coordinators
 */
class DeliberationManager {
    constructor() {
        this.programSelector = document.getElementById('programSelector');
        this.statsContainer = document.getElementById('statsContainer');
        this.currentProgramId = this.programSelector?.value || '';

        // Modals
        this.decisionModal = new bootstrap.Modal(document.getElementById('decisionModal'));
        this.startDeliberationModal = new bootstrap.Modal(document.getElementById('startDeliberationModal'));

        this.init();
    }

    init() {
        this.bindEvents();
        this.loadStats();
        this.loadPendingInterview();
        this.joinDeliberationRoom();
        this.listenWebSocket();
    }

    // ── Helpers para modo "Todos los programas" ─────────────────────────────
    _isAllMode() { return !this.currentProgramId; }

    _targetProgramIds() {
        if (this.currentProgramId) return [parseInt(this.currentProgramId)];
        return (window.COORDINATOR_PROGRAMS || []).map(p => p.id);
    }

    _programName(pid) {
        const p = (window.COORDINATOR_PROGRAMS || []).find(x => x.id === pid);
        return p ? p.name : '—';
    }

    async _fanFetch(urlBuilder) {
        const ids = this._targetProgramIds();
        return Promise.all(ids.map(async (pid) => {
            try {
                const res = await fetch(urlBuilder(pid));
                const json = await res.json();
                if (!res.ok || json.error) return { pid, data: null, meta: null };
                return { pid, data: json.data, meta: json.meta };
            } catch (e) {
                return { pid, data: null, meta: null };
            }
        }));
    }

    _toggleProgramHeader(tableId) {
        const thead = document.querySelector(`#${tableId} thead tr`);
        if (!thead) return;
        const existing = thead.querySelector('th.program-col');
        const want = this._isAllMode();
        if (want && !existing) {
            const th = document.createElement('th');
            th.className = 'program-col';
            th.scope = 'col';
            th.textContent = 'Programa';
            thead.insertBefore(th, thead.firstChild);
        } else if (!want && existing) {
            existing.remove();
        }
    }

    /** Fila de carga accesible dentro de un <tbody>. */
    _loadingRow(colspan, message) {
        return `
            <tr>
                <td colspan="${colspan}" class="text-center py-4">
                    <div class="spinner-border spinner-border-sm text-primary" role="status">
                        <span class="visually-hidden">${message}</span>
                    </div>
                    <span class="ms-2 text-secondary">${message}</span>
                </td>
            </tr>`;
    }

    /** Estado vacío en tabla, con el componente compartido completo. */
    _emptyRow(colspan, icon, title, description) {
        return `
            <tr>
                <td colspan="${colspan}">
                    <div class="empty-state empty-state--inline">
                        <div class="empty-state__icon"><i class="bi bi-${icon}" aria-hidden="true"></i></div>
                        <h3 class="empty-state__title">${title}</h3>
                        <p class="empty-state__description">${description}</p>
                    </div>
                </td>
            </tr>`;
    }

    /** Estado de error en tabla, con acción de recuperación. */
    _errorRow(colspan, detail) {
        return `
            <tr>
                <td colspan="${colspan}">
                    <div class="empty-state empty-state--inline empty-state--error">
                        <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                        <h3 class="empty-state__title">No se pudieron cargar los aspirantes</h3>
                        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
                        <p class="empty-state__error-detail">${detail || ''}</p>
                    </div>
                </td>
            </tr>`;
    }

    /** Fecha corta en español; delega en el helper compartido. */
    _formatDate(value) {
        return window.SIIAP && SIIAP.formatDate
            ? SIIAP.formatDate(value, 'numeric', '-')
            : (value || '-');
    }

    /** Número real de columnas del <thead> (incluye la de Programa inyectada). */
    _colspanFor(tableId, fallback) {
        const count = document.querySelectorAll(`#${tableId} thead tr th`).length;
        return count || (this._isAllMode() ? fallback + 1 : fallback);
    }

    _announce(message) {
        if (window.SIIAP && SIIAP.announce) SIIAP.announce(message);
    }

    /** Escapa un valor para usarlo dentro de un atributo HTML. */
    _escAttr(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** Celda de notas: dos líneas legibles + texto completo en el title. */
    _notesCell(text) {
        const value = text || '-';
        return `<td class="notes-cell" title="${this._escAttr(String(value).replace(/<[^>]*>/g, ' '))}">${value}</td>`;
    }

    // ── WebSocket ─────────────────────────────────────────────────────────────

    joinDeliberationRoom() {
        /**
         * Une al coordinador a la sala Socket.IO del programa para recibir
         * actualizaciones en tiempo real cuando otro coordinador toma una decisión.
         * En modo "Todos" se une a todos los programas accesibles.
         */
        if (!window.siiapSocket) return;
        this._targetProgramIds().forEach(pid => {
            window.siiapSocket.emit('join_deliberation', { program_id: pid });
        });
    }

    listenWebSocket() {
        window.addEventListener('siiap:deliberation:updated', (e) => {
            const data = e.detail || {};

            // En modo específico, sólo reaccionar al programa actual.
            // En modo "Todos", reaccionar a cualquier programa accesible.
            if (this.currentProgramId &&
                data.program_id &&
                String(data.program_id) !== String(this.currentProgramId)) return;

            // Recargar stats y la pestaña activa para reflejar el nuevo estado
            this.loadStats();
            this.loadCurrentTab();

            // Notificar visualmente
            window.dispatchEvent(new CustomEvent('flash', {
                detail: {
                    level: 'info',
                    message: `Estado actualizado: ${data.user_name || 'aspirante'} → ${data.status}`
                }
            }));
        });
    }

    bindEvents() {
        // Program selector change
        if (this.programSelector) {
            this.programSelector.addEventListener('change', () => {
                this.currentProgramId = this.programSelector.value;
                this.loadStats();
                this.loadCurrentTab();
                this.joinDeliberationRoom();
            });
        }

        // Tab changes
        document.querySelectorAll('#statusTabs button[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => {
                const status = e.target.dataset.status;
                if (status === 'pending_interview') {
                    this.loadPendingInterview();
                } else {
                    this.loadApplicants(status);
                }
            });
        });

        // Rejection type change
        document.getElementById('rejectionType')?.addEventListener('change', (e) => {
            const correctionSection = document.getElementById('correctionSection');
            const isPartial = e.target.value === 'partial';
            correctionSection.classList.toggle('d-none', !isPartial);
            if (isPartial) {
                this.loadProgramArchivesForRejection();
            }
        });

        // Confirm decision
        document.getElementById('confirmDecisionBtn')?.addEventListener('click', () => {
            this.submitDecision();
        });

        // Confirm start deliberation
        document.getElementById('confirmStartDelibBtn')?.addEventListener('click', () => {
            this.startDeliberation();
        });
    }

    loadCurrentTab() {
        const activeTab = document.querySelector('#statusTabs button.active');
        if (activeTab) {
            const status = activeTab.dataset.status;
            if (status === 'pending_interview') {
                this.loadPendingInterview();
            } else {
                this.loadApplicants(status);
            }
        }
    }

    async loadPendingInterview() {
        const tbody = document.querySelector('#pendingInterviewTable tbody');
        if (!tbody) return;
        this._toggleProgramHeader('pendingInterviewTable');

        const colspan = this._colspanFor('pendingInterviewTable', 5);
        tbody.innerHTML = this._loadingRow(colspan, 'Cargando aspirantes…');

        try {
            const results = await this._fanFetch(pid => `/api/v1/deliberation/program/${pid}/pending-interview`);
            const items = [];
            results.forEach(r => {
                if (!r.data) return;
                const programName = this._programName(r.pid);
                r.data.forEach(it => { it.__program_name = programName; items.push(it); });
            });

            document.getElementById('pendingInterviewCount').textContent = items.length;

            if (!items.length) {
                tbody.innerHTML = this._emptyRow(
                    colspan, 'calendar-check', 'Sin entrevistas por marcar',
                    'Ningún aspirante tiene una entrevista agendada pendiente de marcar como completada.'
                );
                this._announce('Sin aspirantes con entrevista agendada.');
                return;
            }

            tbody.innerHTML = items.map(item => this.renderPendingInterviewRow(item)).join('');
            this._announce(`${items.length} aspirante(s) con entrevista agendada.`);

        } catch (error) {
            console.error('Error loading pending interviews:', error);
            tbody.innerHTML = this._errorRow(colspan, error.message);
            this._announce('No se pudieron cargar los aspirantes.');
        }
    }

    renderPendingInterviewRow(item) {
        const user = item.user;
        const up = item.user_program;
        const formatDate = (dateStr) => this._formatDate(dateStr);
        const programCell = this._isAllMode()
            ? `<td class="text-muted small">${item.__program_name || ''}</td>` : '';

        return `
            <tr>
                ${programCell}
                <td class="applicant-name">${user.full_name}</td>
                <td class="applicant-email">${user.email}</td>
                <td>${user.curp || '-'}</td>
                <td class="text-center">${formatDate(up.enrollment_date)}</td>
                <td class="text-center">
                    <button class="btn btn-sm btn-success btn-action"
                            onclick="deliberationManager.markInterviewCompleted(${user.id}, ${up.program_id}, '${user.full_name}')">
                        <i class="bi bi-check2-circle"></i> Marcar Completada
                    </button>
                    ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(user.id) : ''}
                </td>
            </tr>
        `;
    }

    markInterviewCompleted(userId, programId, applicantName) {
        this.showConfirm(
            'Confirmar entrevista completada',
            `¿Confirmas que ${applicantName} completó su entrevista?`,
            () => this._doMarkInterviewCompleted(userId, programId),
            'btn-success'
        );
    }

    async _doMarkInterviewCompleted(userId, programId) {
        try {
            const response = await fetch(`/api/v1/deliberation/user/${userId}/program/${programId}/interview-completed`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                }
            });

            const result = await response.json();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadPendingInterview();
            }
        } catch (error) {
            console.error('Error marking interview completed:', error);
            showFlash('danger', 'Error al marcar la entrevista');
        }
    }

    async loadStats() {
        try {
            const results = await this._fanFetch(pid => `/api/v1/deliberation/program/${pid}/stats`);
            const stats = { interview_completed: 0, deliberation: 0, accepted: 0, rejected: 0 };
            results.forEach(r => {
                if (!r.data) return;
                stats.interview_completed += r.data.interview_completed || 0;
                stats.deliberation        += r.data.deliberation        || 0;
                stats.accepted            += r.data.accepted            || 0;
                stats.rejected            += r.data.rejected            || 0;
            });

            // Update tab badges
            document.getElementById('interviewCompletedCount').textContent = stats.interview_completed;
            document.getElementById('deliberationCount').textContent = stats.deliberation;
            document.getElementById('acceptedCount').textContent = stats.accepted;
            document.getElementById('rejectedCount').textContent = stats.rejected;

            // Tarjetas de indicadores (componente compartido .stat-card).
            // El tono nunca es el único portador de significado: cada tarjeta
            // lleva icono + etiqueta de texto.
            if (this.statsContainer) {
                const cards = [
                    { tone: 'warning', icon: 'mic-fill',          value: stats.interview_completed || 0, label: 'Entrevista completada' },
                    { tone: 'info',    icon: 'hourglass-split',   value: stats.deliberation || 0,        label: 'En deliberación' },
                    { tone: 'success', icon: 'check-circle-fill', value: stats.accepted || 0,            label: 'Aceptados' },
                    { tone: 'danger',  icon: 'x-circle-fill',     value: stats.rejected || 0,            label: 'Rechazados' },
                ];
                this.statsContainer.innerHTML = cards.map(c => `
                    <div class="stat-card stat-card--${c.tone}">
                        <i class="bi bi-${c.icon} stat-card__icon" aria-hidden="true"></i>
                        <p class="stat-card__value">${c.value}</p>
                        <p class="stat-card__label">${c.label}</p>
                    </div>
                `).join('');
            }

            // Also load pending interview count separately
            this.loadPendingInterviewCount();
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    async loadPendingInterviewCount() {
        try {
            const results = await this._fanFetch(pid => `/api/v1/deliberation/program/${pid}/pending-interview`);
            let count = 0;
            results.forEach(r => { count += (r.data || []).length; });
            document.getElementById('pendingInterviewCount').textContent = count;
        } catch (error) {
            console.error('Error loading pending interview count:', error);
        }
    }

    async loadApplicants(status) {
        const tableId = this.getTableIdForStatus(status);
        const tbody = document.querySelector(`#${tableId} tbody`);
        if (!tbody) return;
        this._toggleProgramHeader(tableId);

        const colspan = this._colspanFor(tableId, 6);
        tbody.innerHTML = this._loadingRow(colspan, 'Cargando aspirantes…');

        try {
            const results = await this._fanFetch(pid => `/api/v1/deliberation/program/${pid}/by-status/${status}`);
            const items = [];
            results.forEach(r => {
                if (!r.data) return;
                const programName = this._programName(r.pid);
                r.data.forEach(it => { it.__program_name = programName; items.push(it); });
            });

            if (!items.length) {
                tbody.innerHTML = this._emptyRow(
                    colspan, 'inbox', 'Sin aspirantes en este estado',
                    'Cuando un aspirante llegue a esta etapa aparecerá aquí.'
                );
                this._announce('Sin aspirantes en este estado.');
                return;
            }

            tbody.innerHTML = items.map(item => this.renderApplicantRow(item, status)).join('');
            this._announce(`${items.length} aspirante(s) cargado(s).`);

        } catch (error) {
            console.error('Error loading applicants:', error);
            tbody.innerHTML = this._errorRow(colspan, error.message);
            this._announce('No se pudieron cargar los aspirantes.');
        }
    }

    getTableIdForStatus(status) {
        const mapping = {
            'interview_completed': 'interviewCompletedTable',
            'deliberation': 'deliberationTable',
            'accepted': 'acceptedTable',
            'rejected': 'rejectedTable'
        };
        return mapping[status] || 'interviewCompletedTable';
    }

    renderApplicantRow(item, status) {
        const user = item.user;
        const up = item.user_program;
        const formatDate = (dateStr) => this._formatDate(dateStr);
        const programCell = this._isAllMode()
            ? `<td class="text-muted small">${item.__program_name || ''}</td>` : '';

        switch (status) {
            case 'interview_completed':
                return `
                    <tr>
                        ${programCell}
                        <td class="applicant-name">${user.full_name}</td>
                        <td class="applicant-email">${user.email}</td>
                        <td>${user.curp || '-'}</td>
                        <td class="text-center">${formatDate(up.enrollment_date)}</td>
                        <td class="text-center">
                            <div class="btn-group-actions">
                                <button class="btn btn-sm btn-primary btn-action"
                                        onclick="deliberationManager.showStartDeliberation(${user.id}, ${up.program_id}, '${user.full_name}')">
                                    <i class="bi bi-play-fill"></i> Iniciar
                                </button>
                                ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(user.id) : ''}
                            </div>
                        </td>
                    </tr>
                `;

            case 'deliberation':
                return `
                    <tr>
                        ${programCell}
                        <td class="applicant-name">${user.full_name}</td>
                        <td class="applicant-email">${user.email}</td>
                        <td class="text-center">${formatDate(up.deliberation_started_at)}</td>
                        <td class="text-center">
                            <div class="btn-group-actions">
                                <button class="btn btn-sm btn-success btn-action"
                                        onclick="deliberationManager.showDecisionModal(${user.id}, ${up.program_id}, '${user.full_name}', 'accept')">
                                    <i class="bi bi-check-lg"></i> Aceptar
                                </button>
                                <button class="btn btn-sm btn-danger btn-action"
                                        onclick="deliberationManager.showDecisionModal(${user.id}, ${up.program_id}, '${user.full_name}', 'reject')">
                                    <i class="bi bi-x-lg"></i> Rechazar
                                </button>
                                ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(user.id) : ''}
                            </div>
                        </td>
                    </tr>
                `;

            case 'accepted': {
                const forceResetBtn = window.canForceReset
                    ? `<button class="btn btn-sm btn-outline-secondary btn-action"
                               title="Reiniciar estado a En Proceso (solo admin)"
                               onclick="deliberationManager.forceResetApplicant(${user.id}, ${up.program_id}, '${user.full_name}')">
                           <i class="bi bi-arrow-counterclockwise"></i>
                       </button>`
                    : '';
                return `
                    <tr>
                        ${programCell}
                        <td class="applicant-name">${user.full_name}</td>
                        <td class="applicant-email">${user.email}</td>
                        <td class="text-center">${formatDate(up.decision_at)}</td>
                        ${this._notesCell(up.decision_notes)}
                        <td class="text-center">${forceResetBtn}${window.siiapStudentRecordBtn ? ' ' + window.siiapStudentRecordBtn(user.id) : ''}</td>
                    </tr>
                `;
            }

            case 'rejected': {
                const rejectionBadge = up.rejection_type === 'partial'
                    ? SIIAP.statusBadge('deliberation', 'Correcciones', 'sm')
                    : SIIAP.statusBadge('rejected', 'Definitivo', 'sm');
                const resetBtn = up.rejection_type === 'partial'
                    ? `<button class="btn btn-sm btn-outline-primary btn-action"
                               onclick="deliberationManager.resetApplicant(${user.id}, ${up.program_id})">
                           <i class="bi bi-arrow-counterclockwise"></i> Reiniciar
                       </button>`
                    : '';

                // Parsear correction_required: puede ser JSON {archive_id, archive_name, notes} o texto plano
                let correctionDisplay = up.correction_required || up.decision_notes || '-';
                if (up.correction_required) {
                    try {
                        const corr = JSON.parse(up.correction_required);
                        const parts = [];
                        if (corr.archive_name) parts.push(`<strong>Documento:</strong> ${corr.archive_name}`);
                        if (corr.notes) parts.push(corr.notes);
                        correctionDisplay = parts.join('<br>') || '-';
                    } catch (e) {
                        // No es JSON, usar el texto tal cual
                    }
                }

                return `
                    <tr>
                        ${programCell}
                        <td class="applicant-name">${user.full_name}</td>
                        <td class="applicant-email">${user.email}</td>
                        <td class="text-center">${rejectionBadge}</td>
                        <td class="text-center">${formatDate(up.decision_at)}</td>
                        ${this._notesCell(correctionDisplay)}
                        <td class="text-center">${resetBtn}${window.siiapStudentRecordBtn ? ' ' + window.siiapStudentRecordBtn(user.id) : ''}</td>
                    </tr>
                `;
            }

            default:
                return '';
        }
    }

    async loadProgramArchivesForRejection() {
        // Usa el program_id del aspirante en el modal (no el del selector global,
        // que puede ser '' en modo "Todos").
        const programId = document.getElementById('decisionProgramId')?.value
            || this.currentProgramId;
        if (!programId) return;

        const select = document.getElementById('rejectionArchiveSelect');
        if (!select) return;

        select.innerHTML = '<option value="">Cargando documentos...</option>';
        select.disabled = true;

        try {
            const response = await fetch(`/api/v1/deliberation/program/${programId}/admission-archives`);
            const result = await response.json();

            if (result.error || !result.data) {
                select.innerHTML = '<option value="">Error al cargar documentos</option>';
                return;
            }

            select.innerHTML = '<option value="">— Sin documento específico —</option>';
            result.data.forEach(archive => {
                const opt = document.createElement('option');
                opt.value = archive.id;
                opt.dataset.name = archive.name;
                opt.textContent = archive.name;
                select.appendChild(opt);
            });
            select.disabled = false;
        } catch (error) {
            console.error('Error loading archives:', error);
            select.innerHTML = '<option value="">Error al cargar documentos</option>';
        }
    }

    showStartDeliberation(userId, programId, applicantName) {
        document.getElementById('startDelibUserId').value = userId;
        document.getElementById('startDelibProgramId').value = programId;
        document.getElementById('startDelibApplicantName').textContent = applicantName;
        this.startDeliberationModal.show();
    }

    async startDeliberation() {
        const userId = document.getElementById('startDelibUserId').value;
        const programId = document.getElementById('startDelibProgramId').value;

        try {
            const response = await fetch(`/api/v1/deliberation/user/${userId}/program/${programId}/start`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                }
            });

            const result = await response.json();

            this.startDeliberationModal.hide();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadApplicants('interview_completed');
            }
        } catch (error) {
            console.error('Error starting deliberation:', error);
            showFlash('danger', 'Error al iniciar la deliberación');
        }
    }

    showDecisionModal(userId, programId, applicantName, action) {
        document.getElementById('decisionUserId').value = userId;
        document.getElementById('decisionProgramId').value = programId;
        document.getElementById('decisionAction').value = action;
        document.getElementById('decisionApplicantName').textContent = applicantName;
        document.getElementById('decisionNotes').value = '';

        // Show/hide rejection section
        const rejectionSection = document.getElementById('rejectionSection');
        const correctionSection = document.getElementById('correctionSection');
        const modalTitle = document.getElementById('decisionModalTitle');
        const confirmBtn = document.getElementById('confirmDecisionBtn');

        const acceptSection = document.getElementById('acceptSection');
        const dictamenSection = document.getElementById('dictamenSection');
        const isConditionalCheck = document.getElementById('acceptIsConditional');
        const dictamenFileInput = document.getElementById('dictamenFile');

        if (action === 'reject') {
            rejectionSection.classList.remove('d-none');
            if (acceptSection) acceptSection.classList.add('d-none');
            correctionSection.classList.add('d-none');
            document.getElementById('rejectionType').value = 'full';
            // Reset archive select
            const archiveSelect = document.getElementById('rejectionArchiveSelect');
            if (archiveSelect) {
                archiveSelect.innerHTML = '<option value="">— Sin documento específico —</option>';
                archiveSelect.disabled = true;
            }
            document.getElementById('correctionRequired').value = '';
            modalTitle.textContent = 'Rechazar aspirante';
            confirmBtn.className = 'btn btn-danger';
            confirmBtn.textContent = 'Confirmar rechazo';
        } else {
            rejectionSection.classList.add('d-none');
            if (acceptSection) acceptSection.classList.remove('d-none');
            if (isConditionalCheck) isConditionalCheck.checked = false;
            if (dictamenSection) dictamenSection.classList.add('d-none');
            if (dictamenFileInput) dictamenFileInput.value = '';
            modalTitle.textContent = 'Aceptar aspirante';
            confirmBtn.className = 'btn btn-success';
            confirmBtn.textContent = 'Confirmar aceptación';
        }

        // Toggle dictamen section when checkbox changes
        if (isConditionalCheck && !isConditionalCheck.dataset.bound) {
            isConditionalCheck.addEventListener('change', (e) => {
                if (dictamenSection) {
                    dictamenSection.classList.toggle('d-none', !e.target.checked);
                }
            });
            isConditionalCheck.dataset.bound = '1';
        }

        this.decisionModal.show();
    }

    async submitDecision() {
        const userId = document.getElementById('decisionUserId').value;
        const programId = document.getElementById('decisionProgramId').value;
        const action = document.getElementById('decisionAction').value;
        const notes = document.getElementById('decisionNotes').value;

        if (!userId || !programId || !/^\d+$/.test(userId) || !/^\d+$/.test(programId)) {
            showFlash('danger', 'Aspirante o programa inválido — recarga la página e intenta de nuevo.');
            return;
        }

        let endpoint, body;

        if (action === 'accept') {
            endpoint = `/api/v1/deliberation/user/${userId}/program/${programId}/accept`;
            const isConditional = !!document.getElementById('acceptIsConditional')?.checked;
            const dictamenFile = document.getElementById('dictamenFile')?.files?.[0];

            if (isConditional && !dictamenFile) {
                showFlash('warning', 'Debes adjuntar el Dictamen de Aceptación para aceptación condicionada.');
                return;
            }

            if (isConditional) {
                const fd = new FormData();
                if (notes) fd.append('notes', notes);
                fd.append('is_conditional', 'true');
                fd.append('dictamen_file', dictamenFile);
                try {
                    const response = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' },
                        body: fd,
                    });
                    const result = await response.json();
                    this.decisionModal.hide();
                    if (result.flash) result.flash.forEach(f => showFlash(f.level, f.message));
                    if (!result.error) {
                        this.loadStats();
                        this.loadApplicants('deliberation');
                    }
                } catch (error) {
                    console.error('Error submitting decision:', error);
                    showFlash('danger', 'Error al procesar la decisión');
                }
                return;
            }
            body = { notes, is_conditional: false };
        } else {
            const rejectionType = document.getElementById('rejectionType').value;
            const correctionText = document.getElementById('correctionRequired').value;
            endpoint = `/api/v1/deliberation/user/${userId}/program/${programId}/reject`;

            // Si es parcial, incluir el documento específico seleccionado (si se eligió uno)
            let correctionRequired = correctionText;
            if (rejectionType === 'partial') {
                const archiveSelect = document.getElementById('rejectionArchiveSelect');
                const archiveId = archiveSelect?.value;
                const archiveName = archiveSelect?.options[archiveSelect.selectedIndex]?.dataset?.name;
                if (archiveId && archiveName) {
                    correctionRequired = JSON.stringify({
                        archive_id: parseInt(archiveId),
                        archive_name: archiveName,
                        notes: correctionText
                    });
                }
            }

            body = { rejection_type: rejectionType, notes, correction_required: correctionRequired };
        }

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify(body)
            });

            const result = await response.json();

            this.decisionModal.hide();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadApplicants('deliberation');
            }
        } catch (error) {
            console.error('Error submitting decision:', error);
            showFlash('danger', 'Error al procesar la decisión');
        }
    }

    resetApplicant(userId, programId) {
        this.showConfirm(
            'Reiniciar estado',
            '¿Deseas reiniciar el estado de este aspirante? Podrá volver a enviar documentos.',
            () => this._doResetApplicant(userId, programId),
            'btn-primary'
        );
    }

    async _doResetApplicant(userId, programId) {
        try {
            const response = await fetch(`/api/v1/deliberation/user/${userId}/program/${programId}/reset`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ reason: 'Correcciones completadas' })
            });

            const result = await response.json();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadApplicants('rejected');
            }
        } catch (error) {
            console.error('Error resetting applicant:', error);
            showFlash('danger', 'Error al reiniciar el estado');
        }
    }

    forceResetApplicant(userId, programId, applicantName) {
        this.showConfirm(
            'Reinicio administrativo',
            `¿Reiniciar el estado de "${applicantName}" a "En Proceso"? Se registrará en el historial.`,
            () => this._doForceResetApplicant(userId, programId, applicantName),
            'btn-warning'
        );
    }

    async _doForceResetApplicant(userId, programId, applicantName) {
        try {
            const response = await fetch(`/api/v1/deliberation/user/${userId}/program/${programId}/force-reset`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ reason: 'Reinicio administrativo desde panel de deliberación' })
            });
            const result = await response.json();
            if (result.flash) result.flash.forEach(f => showFlash(f.level, f.message));
            if (!result.error) {
                this.loadStats();
                this.loadApplicants('accepted');
            }
        } catch (error) {
            console.error('Error force resetting applicant:', error);
            showFlash('danger', 'Error al reiniciar el estado');
        }
    }

    showConfirm(title, message, onConfirm, btnClass = 'btn-primary') {
        document.getElementById('confirmModalTitle').textContent = title;
        document.getElementById('confirmModalMessage').textContent = message;
        const btn = document.getElementById('confirmModalBtn');
        btn.className = `btn ${btnClass}`;
        const modal = new bootstrap.Modal(document.getElementById('confirmModal'));
        btn.onclick = () => {
            modal.hide();
            onConfirm();
        };
        modal.show();
    }
}

// Initialize when DOM is ready
let deliberationManager;
document.addEventListener('DOMContentLoaded', () => {
    deliberationManager = new DeliberationManager();
});
