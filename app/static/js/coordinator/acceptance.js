/**
 * Acceptance & Enrollment Document Management for Coordinators
 * Fase 4 - Aceptacion e Inscripcion
 * Fase 7 - Diferimiento de Inscripcion
 */
class AcceptanceManager {
    constructor() {
        this.programSelector = document.getElementById('programSelector');
        this.statsContainer = document.getElementById('statsContainer');
        this.currentProgramId = this.programSelector?.value || '';

        // Modals
        this.uploadDocModal = new bootstrap.Modal(document.getElementById('uploadDocModal'));
        this.reviewReceiptModal = new bootstrap.Modal(document.getElementById('reviewReceiptModal'));
        this.deferApplicantModal = new bootstrap.Modal(document.getElementById('deferApplicantModal'));
        this.reviewDeferralModal = new bootstrap.Modal(document.getElementById('reviewDeferralModal'));
        this.reactivateModal = new bootstrap.Modal(document.getElementById('reactivateModal'));

        this.init();
    }

    init() {
        this.bindEvents();
        // En modo "Todos" igualmente carga (fan-out a programas accesibles)
        this.loadStats();
        this.loadTab('pending_docs');
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

    /** Escapa un valor para usarlo dentro de un atributo HTML. */
    _escAttr(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /**
     * Celda «Programa» del modo «Todos los programas». La columna se acota por
     * CSS (.program-col) para que un nombre largo no empuje el resto de la
     * tabla fuera del ancho real del portátil; el nombre completo va en el
     * title y sigue siendo accesible.
     */
    _programCell(name) {
        const value = name || '';
        return this._isAllMode()
            ? `<td class="program-col text-muted small" title="${this._escAttr(value)}">${value}</td>`
            : '';
    }

    async _fanFetch(urlBuilder) {
        const ids = this._targetProgramIds();
        const tasks = ids.map(async (pid) => {
            try {
                const res = await fetch(urlBuilder(pid));
                const json = await res.json();
                if (!res.ok || json.error) return { pid, data: null, error: json.error };
                return { pid, data: json.data, error: null };
            } catch (e) {
                return { pid, data: null, error: { message: e.message } };
            }
        });
        return Promise.all(tasks);
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

    /** Estado de error en tabla. */
    _errorRow(colspan, detail) {
        return `
            <tr>
                <td colspan="${colspan}">
                    <div class="empty-state empty-state--inline empty-state--error">
                        <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                        <h3 class="empty-state__title">No se pudieron cargar los datos</h3>
                        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
                        <p class="empty-state__error-detail">${detail || ''}</p>
                    </div>
                </td>
            </tr>`;
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

    _announce(message) {
        if (window.SIIAP && SIIAP.announce) SIIAP.announce(message);
    }

    bindEvents() {
        if (this.programSelector) {
            this.programSelector.addEventListener('change', () => {
                this.currentProgramId = this.programSelector.value;
                this.loadStats();
                this.loadCurrentTab();
            });
        }

        // Tab changes
        document.querySelectorAll('#acceptanceTabs button[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => {
                const tabId = e.target.getAttribute('data-bs-target');
                if (tabId === '#deferred-pane') {
                    this.loadDeferredTab();
                } else {
                    this.loadTab(e.target.dataset.tab);
                }
            });
        });

        // Upload doc confirm
        document.getElementById('confirmUploadDocBtn')?.addEventListener('click', () => {
            this.submitUploadDoc();
        });

        // Review receipt confirm
        document.getElementById('confirmReviewReceiptBtn')?.addEventListener('click', () => {
            this.submitReviewReceipt();
        });

        // Assign control number confirm
        document.getElementById('confirmAssignCtrlBtn')?.addEventListener('click', () => {
            this.submitAssignControlNumber();
        });

        // Show/hide notes required indicator when selecting reject
        document.querySelectorAll('input[name="reviewReceiptStatus"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                this._setNotesRequired('notesRequired', 'reviewReceiptNotes', e.target.value === 'rejected');
            });
        });

        // Defer applicant confirm
        document.getElementById('confirmDeferBtn')?.addEventListener('click', () => {
            this.submitDefer();
        });

        // Review deferral confirm
        document.getElementById('confirmReviewDeferralBtn')?.addEventListener('click', () => {
            this.submitReviewDeferral();
        });

        // Reactivate confirm
        document.getElementById('confirmReactivateBtn')?.addEventListener('click', () => {
            this.submitReactivate();
        });

        // Show/hide notes required when rejecting deferral
        document.querySelectorAll('input[name="reviewDeferralStatus"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                this._setNotesRequired('deferralNotesRequired', 'reviewDeferralNotes', e.target.value === 'rejected');
            });
        });
    }

    /**
     * Muestra/oculta la marca de campo requerido y sincroniza aria-required en
     * el control: el asterisco por sí solo no comunica obligatoriedad.
     */
    _setNotesRequired(markId, textareaId, isRequired) {
        document.getElementById(markId)?.classList.toggle('d-none', !isRequired);
        const textarea = document.getElementById(textareaId);
        if (textarea) textarea.setAttribute('aria-required', isRequired ? 'true' : 'false');
    }

    loadCurrentTab() {
        const activeTab = document.querySelector('#acceptanceTabs button.active');
        if (!activeTab) return;
        const tabId = activeTab.getAttribute('data-bs-target');
        if (tabId === '#deferred-pane') {
            this.loadDeferredTab();
        } else {
            this.loadTab(activeTab.dataset.tab);
        }
    }

    async loadStats() {
        try {
            const results = await this._fanFetch(pid => `/api/v1/acceptance/program/${pid}/stats`);
            const stats = { total_accepted: 0, pending_docs: 0, receipt_submitted: 0, completed: 0 };
            results.forEach(r => {
                if (!r.data) return;
                stats.total_accepted    += r.data.total_accepted    || 0;
                stats.pending_docs      += r.data.pending_docs      || 0;
                stats.receipt_submitted += r.data.receipt_submitted || 0;
                stats.completed         += r.data.completed         || 0;
            });

            document.getElementById('pendingDocsCount').textContent = stats.pending_docs;
            document.getElementById('receiptCount').textContent = stats.receipt_submitted;
            document.getElementById('completedCount').textContent = stats.completed;

            // Tarjetas de indicadores (componente compartido .stat-card).
            // La tarjeta sin modificador ES el tono neutro; el tono nunca es el
            // único portador de significado: cada una lleva icono + etiqueta.
            if (this.statsContainer) {
                const cards = [
                    { tone: '',        icon: 'people-fill',            value: stats.total_accepted || 0,    label: 'Total aceptados' },
                    { tone: 'warning', icon: 'hourglass-split',        value: stats.pending_docs || 0,      label: 'Pendientes' },
                    { tone: 'info',    icon: 'file-earmark-arrow-up',  value: stats.receipt_submitted || 0, label: 'Boleta recibida' },
                    { tone: 'success', icon: 'check-all',              value: stats.completed || 0,         label: 'Completados' },
                ];
                this.statsContainer.innerHTML = cards.map(c => `
                    <div class="stat-card${c.tone ? ' stat-card--' + c.tone : ''}">
                        <i class="bi bi-${c.icon} stat-card__icon" aria-hidden="true"></i>
                        <p class="stat-card__value">${c.value}</p>
                        <p class="stat-card__label">${c.label}</p>
                    </div>
                `).join('');
            }
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    async loadTab(tabName) {
        try {
            const results = await this._fanFetch(pid => `/api/v1/acceptance/program/${pid}/applicants`);
            const allApplicants = [];
            results.forEach(r => {
                if (!r.data) return;
                r.data.forEach(a => { a.__program_name = this._programName(r.pid); });
                allApplicants.push(...r.data);
            });

            if (tabName === 'pending_docs') {
                this.renderPendingDocsTab(allApplicants.filter(a => this.isPendingDocs(a)));
            } else if (tabName === 'receipt_submitted') {
                this.renderReceiptTab(allApplicants.filter(a => this.isReceiptSubmitted(a)));
            } else if (tabName === 'completed') {
                this.renderCompletedTab(allApplicants.filter(a => this.isCompleted(a)));
            }
        } catch (error) {
            console.error('Error loading tab:', error);
        }
    }

    isPendingDocs(applicant) {
        return !this.isReceiptSubmitted(applicant) && !this.isCompleted(applicant);
    }

    isReceiptSubmitted(applicant) {
        const docs = applicant.acceptance_docs;
        return docs.enrollment_receipt?.status === 'uploaded';
    }

    isCompleted(applicant) {
        const docs = applicant.acceptance_docs;
        return docs.enrollment_receipt?.status === 'approved';
    }

    renderPendingDocsTab(applicants) {
        const tbody = document.querySelector('#pendingDocsTable tbody');
        if (!tbody) return;
        this._toggleProgramHeader('pendingDocsTable');

        const colspan = this._isAllMode() ? 6 : 5;
        if (!applicants.length) {
            tbody.innerHTML = this._emptyRow(
                colspan, 'check2-circle', 'Sin aspirantes pendientes',
                'Todos los aspirantes aceptados ya tienen su carta y su tira de materias.'
            );
            this._announce('Sin aspirantes pendientes de documentos.');
            return;
        }

        tbody.innerHTML = applicants.map(a => {
            const user = a.user;
            const up = a.user_program;
            const docs = a.acceptance_docs;
            const letterStatus = this.renderDocBadge(docs.acceptance_letter);
            const scheduleStatus = this.renderDocBadge(docs.course_schedule);
            const safeName = user.full_name.replace(/'/g, "\\'");
            const programCell = this._programCell(a.__program_name);

            return `
                <tr>
                    ${programCell}
                    <td class="applicant-name">${user.full_name}</td>
                    <td class="applicant-email">${user.email}</td>
                    <td class="text-center">${letterStatus}</td>
                    <td class="text-center">${scheduleStatus}</td>
                    <td class="text-center">
                        <div class="btn-group-actions">
                            ${!docs.acceptance_letter?.file_path ? `
                            <button class="btn btn-sm btn-outline-primary btn-action"
                                    onclick="acceptanceManager.showUploadModal(${user.id}, ${up.program_id}, '${safeName}', 'acceptance_letter')">
                                <i class="bi bi-upload"></i> Carta
                            </button>` : `
                            <button class="btn btn-sm btn-outline-secondary btn-action"
                                    onclick="acceptanceManager.showUploadModal(${user.id}, ${up.program_id}, '${safeName}', 'acceptance_letter')">
                                <i class="bi bi-arrow-repeat"></i> Carta
                            </button>`}
                            ${!docs.course_schedule?.file_path ? `
                            <button class="btn btn-sm btn-outline-primary btn-action"
                                    onclick="acceptanceManager.showUploadModal(${user.id}, ${up.program_id}, '${safeName}', 'course_schedule')">
                                <i class="bi bi-upload"></i> Tira
                            </button>` : `
                            <button class="btn btn-sm btn-outline-secondary btn-action"
                                    onclick="acceptanceManager.showUploadModal(${user.id}, ${up.program_id}, '${safeName}', 'course_schedule')">
                                <i class="bi bi-arrow-repeat"></i> Tira
                            </button>`}
                            <button class="btn btn-sm btn-outline-warning btn-action"
                                    onclick="acceptanceManager.showDeferModal(${user.id}, ${up.program_id}, '${safeName}')">
                                <i class="bi bi-arrow-clockwise"></i> Diferir
                            </button>
                            ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(user.id) : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        this._announce(`${applicants.length} aspirante(s) pendiente(s) de documentos.`);
    }

    /**
     * Inserta o quita la columna "Programa" en el thead de la tabla indicada
     * según el modo actual ("Todos" vs programa específico).
     */
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

    renderReceiptTab(applicants) {
        const tbody = document.querySelector('#receiptTable tbody');
        if (!tbody) return;
        this._toggleProgramHeader('receiptTable');

        const colspan = this._isAllMode() ? 7 : 6;
        if (!applicants.length) {
            tbody.innerHTML = this._emptyRow(
                colspan, 'inbox', 'Sin boletas por revisar',
                'Aqui aparecerán las boletas de servicios escolares en cuanto los aspirantes las suban.'
            );
            this._announce('Sin boletas pendientes de revisión.');
            return;
        }

        tbody.innerHTML = applicants.map(a => {
            const user = a.user;
            const docs = a.acceptance_docs;
            const receiptDoc = docs.enrollment_receipt;
            const programCell = this._programCell(a.__program_name);

            return `
                <tr>
                    ${programCell}
                    <td class="applicant-name">${user.full_name}</td>
                    <td class="applicant-email">${user.email}</td>
                    <td class="text-center">${this.renderDocBadge(docs.acceptance_letter)}</td>
                    <td class="text-center">${this.renderDocBadge(docs.course_schedule)}</td>
                    <td class="text-center">${SIIAP.statusBadge('review', 'Subida', 'sm')}</td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-primary btn-action"
                                onclick="acceptanceManager.showReviewModal(${receiptDoc.id}, '${user.full_name}', '${receiptDoc.file_path}')">
                            <i class="bi bi-eye"></i> Revisar
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
        this._announce(`${applicants.length} boleta(s) por revisar.`);
    }

    renderCompletedTab(applicants) {
        const tbody = document.querySelector('#completedTable tbody');
        if (!tbody) return;
        this._toggleProgramHeader('completedTable');

        const colspan = this._isAllMode() ? 7 : 6;
        if (!applicants.length) {
            tbody.innerHTML = this._emptyRow(
                colspan, 'inbox', 'Sin procesos completados',
                'Ningún aspirante ha completado todavía el proceso de inscripción.'
            );
            this._announce('Sin procesos de inscripción completados.');
            return;
        }

        tbody.innerHTML = applicants.map(a => {
            const user = a.user;
            const up = a.user_program;
            const programCell = this._programCell(a.__program_name);

            let controlNumberCell;
            if (user.control_number) {
                controlNumberCell = `<span class="badge bg-primary font-monospace">${user.control_number}</span>`;
            } else {
                controlNumberCell = `
                    <button class="btn btn-sm btn-outline-success btn-action"
                            onclick="acceptanceManager.showAssignControlNumberModal(${user.id}, ${up.program_id}, '${user.full_name.replace(/'/g, "\\'")}')">
                        <i class="bi bi-hash"></i> Asignar
                    </button>`;
            }

            return `
                <tr>
                    ${programCell}
                    <td class="applicant-name">${user.full_name}</td>
                    <td class="applicant-email">${user.email}</td>
                    <td class="text-center">${SIIAP.statusBadge('approved', 'Disponible', 'sm')}</td>
                    <td class="text-center">${SIIAP.statusBadge('approved', 'Disponible', 'sm')}</td>
                    <td class="text-center">${SIIAP.statusBadge('approved', 'Aprobada', 'sm')}</td>
                    <td class="text-center">${controlNumberCell}</td>
                </tr>
            `;
        }).join('');
        this._announce(`${applicants.length} proceso(s) completado(s).`);
    }

    showAssignControlNumberModal(userId, programId, applicantName) {
        document.getElementById('assignCtrlUserId').value = userId;
        document.getElementById('assignCtrlProgramId').value = programId;
        document.getElementById('assignCtrlApplicantName').textContent = applicantName;
        document.getElementById('assignCtrlNumber').value = '';

        const modal = new bootstrap.Modal(document.getElementById('assignControlNumberModal'));
        modal.show();
    }

    async submitAssignControlNumber() {
        const userId = document.getElementById('assignCtrlUserId').value;
        const programId = document.getElementById('assignCtrlProgramId').value;
        const controlNumber = document.getElementById('assignCtrlNumber').value.trim();

        if (!controlNumber) {
            showFlash('warning', 'Ingresa el número de control');
            return;
        }

        const btn = document.getElementById('confirmAssignCtrlBtn');
        btn.disabled = true;

        try {
            const response = await fetch(
                `/api/v1/acceptance/user/${userId}/program/${programId}/assign-control-number`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: JSON.stringify({ control_number: controlNumber })
                }
            );

            const result = await response.json();
            bootstrap.Modal.getInstance(document.getElementById('assignControlNumberModal'))?.hide();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadCurrentTab();
            }
        } catch (error) {
            console.error('Error assigning control number:', error);
            showFlash('danger', 'Error al asignar número de control');
        } finally {
            btn.disabled = false;
        }
    }

    /** Chip de estado documental — usa el componente compartido .status-badge. */
    renderDocBadge(doc) {
        if (!doc || doc.status === 'pending' || !doc.file_path) {
            return SIIAP.statusBadge('pending', 'Pendiente', 'sm');
        }
        if (doc.status === 'approved') {
            return SIIAP.statusBadge('approved', 'Aprobada', 'sm');
        }
        if (doc.status === 'uploaded') {
            return SIIAP.statusBadge('review', 'Subida', 'sm');
        }
        if (doc.status === 'rejected') {
            return SIIAP.statusBadge('rejected', 'Rechazada', 'sm');
        }
        return SIIAP.statusBadge('pending', 'Sin registro', 'sm');
    }

    showUploadModal(userId, programId, applicantName, documentType) {
        const typeLabels = {
            'acceptance_letter': 'Carta de aceptación',
            'course_schedule': 'Tira de materias',
        };

        document.getElementById('uploadDocUserId').value = userId;
        document.getElementById('uploadDocProgramId').value = programId;
        document.getElementById('uploadDocType').value = documentType;
        document.getElementById('uploadDocApplicantName').textContent = applicantName;
        document.getElementById('uploadDocTypeLabel').textContent = typeLabels[documentType] || documentType;
        document.getElementById('uploadDocModalTitle').textContent = `Subir ${(typeLabels[documentType] || documentType).toLowerCase()}`;
        document.getElementById('uploadDocFile').value = '';

        this.uploadDocModal.show();
    }

    async submitUploadDoc() {
        const userId = document.getElementById('uploadDocUserId').value;
        const programId = document.getElementById('uploadDocProgramId').value;
        const documentType = document.getElementById('uploadDocType').value;
        const fileInput = document.getElementById('uploadDocFile');

        if (!fileInput.files[0]) {
            showFlash('warning', 'Selecciona un archivo');
            return;
        }

        const spinner = document.getElementById('uploadDocSpinner');
        const btn = document.getElementById('confirmUploadDocBtn');
        spinner.classList.remove('d-none');
        btn.disabled = true;

        const formData = new FormData();
        formData.append('document_type', documentType);
        formData.append('file', fileInput.files[0]);

        try {
            const response = await fetch(
                `/api/v1/acceptance/user/${userId}/program/${programId}/upload-doc`,
                {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: formData
                }
            );

            const result = await response.json();
            this.uploadDocModal.hide();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadCurrentTab();
            }
        } catch (error) {
            console.error('Error uploading doc:', error);
            showFlash('danger', 'Error al subir el documento');
        } finally {
            spinner.classList.add('d-none');
            btn.disabled = false;
        }
    }

    showReviewModal(docId, applicantName, filePath) {
        document.getElementById('reviewReceiptDocId').value = docId;
        document.getElementById('reviewReceiptApplicantName').textContent = applicantName;
        document.getElementById('reviewReceiptNotes').value = '';
        this._setNotesRequired('notesRequired', 'reviewReceiptNotes', false);

        // Reset radios
        document.querySelectorAll('input[name="reviewReceiptStatus"]').forEach(r => r.checked = false);

        // Construir URL de descarga del archivo
        if (filePath) {
            // filePath tiene formato: user_id/acceptance/filename.pdf
            const parts = filePath.split('/');
            const userId = parts[0];
            const phase = parts[1];
            const filename = parts.slice(2).join('/');
            const downloadUrl = `/files/doc/${userId}/${phase}/${filename}`;
            const viewBtn = document.getElementById('viewReceiptBtn');
            viewBtn.href = downloadUrl;
            viewBtn.classList.remove('disabled');
            viewBtn.setAttribute('aria-disabled', 'false');
        } else {
            const viewBtn = document.getElementById('viewReceiptBtn');
            viewBtn.href = '#';
            viewBtn.classList.add('disabled');
            viewBtn.setAttribute('aria-disabled', 'true');
        }

        this.reviewReceiptModal.show();
    }

    async submitReviewReceipt() {
        const docId = document.getElementById('reviewReceiptDocId').value;
        const status = document.querySelector('input[name="reviewReceiptStatus"]:checked')?.value;
        const notes = document.getElementById('reviewReceiptNotes').value.trim();

        if (!docId || docId === 'undefined' || docId === 'null' || !/^\d+$/.test(docId)) {
            showFlash('danger', 'Documento inválido — recarga la página e intenta de nuevo.');
            return;
        }

        if (!status) {
            showFlash('warning', 'Selecciona una decisión (Aprobar o Rechazar)');
            return;
        }

        if (status === 'rejected' && !notes) {
            showFlash('warning', 'Debes indicar el motivo del rechazo');
            return;
        }

        try {
            const response = await fetch(`/api/v1/acceptance/document/${docId}/review`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ status, notes })
            });

            const result = await response.json();
            this.reviewReceiptModal.hide();

            if (result.flash) {
                result.flash.forEach(f => showFlash(f.level, f.message));
            }

            if (!result.error) {
                this.loadStats();
                this.loadCurrentTab();
            }
        } catch (error) {
            console.error('Error reviewing receipt:', error);
            showFlash('danger', 'Error al revisar la boleta');
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // FASE 7: Diferimiento de Inscripción
    // ─────────────────────────────────────────────────────────────────────

    async loadDeferredTab() {
        const deferredTbody = document.querySelector('#deferredTable tbody');
        const requestsTbody = document.querySelector('#pendingRequestsTable tbody');
        const deferredCountBadge = document.getElementById('deferredCount');

        if (deferredTbody) deferredTbody.innerHTML = this._loadingRow(6, 'Cargando aspirantes diferidos…');

        try {
            const results = await this._fanFetch(pid => `/api/v1/acceptance/program/${pid}/deferred`);
            const deferred = [];
            const pending_requests = [];
            results.forEach(r => {
                if (!r.data) return;
                const programName = this._programName(r.pid);
                (r.data.deferred || []).forEach(d => { d.__program_name = programName; deferred.push(d); });
                (r.data.pending_requests || []).forEach(p => { p.__program_name = programName; pending_requests.push(p); });
            });

            if (deferredCountBadge) deferredCountBadge.textContent = deferred.length;

            // Sección solicitudes pendientes del aspirante
            const pendingSection = document.getElementById('pendingRequestsSection');
            const pendingCount = document.getElementById('pendingRequestsCount');
            if (pendingSection) pendingSection.classList.toggle('d-none', !pending_requests.length);
            if (pendingCount) pendingCount.textContent = pending_requests.length;

            if (requestsTbody) {
                this._toggleProgramHeader('pendingRequestsTable');
                const pCol = this._isAllMode() ? 7 : 6;
                if (!pending_requests.length) {
                    requestsTbody.innerHTML = this._emptyRow(
                        pCol, 'bell', 'Sin solicitudes pendientes',
                        'Ningún aspirante ha solicitado diferir su inscripción.'
                    );
                } else {
                    requestsTbody.innerHTML = pending_requests.map(p => {
                        const safeName = p.user.full_name.replace(/'/g, "\\'");
                        const safeReason = (p.deferral.reason || '').replace(/'/g, "\\'");
                        const programCell = this._programCell(p.__program_name);
                        return `
                            <tr>
                                ${programCell}
                                <td>${p.user.full_name}</td>
                                <td>${p.user.email}</td>
                                <td class="text-center">#${p.deferral.deferral_number}</td>
                                <td>${p.deferral.deferred_to_period_name || '<em class="text-muted">Por asignar</em>'}</td>
                                <td class="reason-cell fst-italic text-muted"
                                    title="${this._escAttr(p.deferral.reason || 'Sin especificar')}">${p.deferral.reason || 'Sin especificar'}</td>
                                <td class="text-center">
                                    <button class="btn btn-sm btn-primary"
                                            onclick="acceptanceManager.showReviewDeferralModal(${p.deferral.id}, '${safeName}', ${p.deferral.deferral_number}, '${p.deferral.deferred_to_period_name || ''}', '${safeReason}')">
                                        <i class="bi bi-eye me-1"></i>Revisar
                                    </button>
                                </td>
                            </tr>`;
                    }).join('');
                }
            }

            if (deferredTbody) {
                this._toggleProgramHeader('deferredTable');
                const dCol = this._isAllMode() ? 7 : 6;
                if (!deferred.length) {
                    deferredTbody.innerHTML = this._emptyRow(
                        dCol, 'calendar-check', 'Sin inscripciones diferidas',
                        'Ningún aspirante tiene la inscripción diferida a otro periodo.'
                    );
                } else {
                    deferredTbody.innerHTML = deferred.map(d => {
                        const safeName = d.user.full_name.replace(/'/g, "\\'");
                        const deferral = d.deferral;
                        const canReactivate = deferral && deferral.deferred_to_period_id;
                        const deferralsLeft = d.can_defer_again
                            ? SIIAP.statusBadge('deliberation', `${d.deferrals_used}/2 usados`, 'sm')
                            : SIIAP.statusBadge('rejected', 'Máximo alcanzado', 'sm');
                        const reactivateBtn = canReactivate
                            ? `<button class="btn btn-sm btn-success" onclick="acceptanceManager.showReactivateModal(${d.user_program.user_id}, ${d.user_program.program_id}, '${safeName}', '${deferral.deferred_to_period_name || ''}')">
                                   <i class="bi bi-person-check-fill me-1"></i>Reactivar
                               </button>`
                            : `<span class="text-muted small">Sin periodo destino</span>`;
                        const programCell = this._programCell(d.__program_name);
                        return `
                            <tr>
                                ${programCell}
                                <td>${d.user.full_name}</td>
                                <td>${d.user.email}</td>
                                <td class="text-center">${deferralsLeft}</td>
                                <td>${deferral ? (deferral.original_period_name || '-') : '-'}</td>
                                <td>${deferral ? (deferral.deferred_to_period_name || '<em class="text-muted">Por asignar</em>') : '-'}</td>
                                <td class="text-center">${reactivateBtn}</td>
                            </tr>`;
                    }).join('');
                }
            }
        } catch (error) {
            console.error('Error loading deferred tab:', error);
            if (deferredTbody) deferredTbody.innerHTML = this._errorRow(6, error.message);
            this._announce('No se pudieron cargar los aspirantes diferidos.');
        }
    }

    showDeferModal(userId, programId, applicantName) {
        document.getElementById('deferUserId').value = userId;
        document.getElementById('deferProgramId').value = programId;
        document.getElementById('deferApplicantName').textContent = applicantName;
        document.getElementById('deferReason').value = '';
        this.deferApplicantModal.show();
    }

    async submitDefer() {
        const userId = document.getElementById('deferUserId').value;
        const programId = document.getElementById('deferProgramId').value;
        const reason = document.getElementById('deferReason').value.trim();

        const btn = document.getElementById('confirmDeferBtn');
        const spinner = document.getElementById('deferSpinner');
        btn.disabled = true;
        spinner.classList.remove('d-none');

        try {
            const response = await fetch(
                `/api/v1/acceptance/user/${userId}/program/${programId}/defer`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: JSON.stringify({ reason: reason || null })
                }
            );
            const result = await response.json();
            this.deferApplicantModal.hide();
            if (result.flash) result.flash.forEach(f => showFlash(f.level, f.message));
            if (!result.error) { this.loadStats(); this.loadCurrentTab(); }
        } catch (error) {
            console.error('Error deferring applicant:', error);
            showFlash('danger', 'Error al diferir la inscripción');
        } finally {
            btn.disabled = false;
            spinner.classList.add('d-none');
        }
    }

    showReviewDeferralModal(deferralId, applicantName, deferralNumber, periodName, reason) {
        document.getElementById('reviewDeferralId').value = deferralId;
        document.getElementById('reviewDeferralApplicantName').textContent = applicantName;
        document.getElementById('reviewDeferralNumber').textContent = `#${deferralNumber}`;
        document.getElementById('reviewDeferralPeriod').textContent = periodName || 'Por asignar';
        document.getElementById('reviewDeferralReason').textContent = reason || 'Sin especificar';
        document.getElementById('reviewDeferralNotes').value = '';
        this._setNotesRequired('deferralNotesRequired', 'reviewDeferralNotes', false);
        document.querySelectorAll('input[name="reviewDeferralStatus"]').forEach(r => r.checked = false);
        this.reviewDeferralModal.show();
    }

    async submitReviewDeferral() {
        const deferralId = document.getElementById('reviewDeferralId').value;
        const decision = document.querySelector('input[name="reviewDeferralStatus"]:checked')?.value;
        const notes = document.getElementById('reviewDeferralNotes').value.trim();

        if (!decision) { showFlash('warning', 'Selecciona una decisión'); return; }
        if (decision === 'rejected' && !notes) { showFlash('warning', 'Debes indicar el motivo del rechazo'); return; }

        const btn = document.getElementById('confirmReviewDeferralBtn');
        const spinner = document.getElementById('reviewDeferralSpinner');
        btn.disabled = true;
        spinner.classList.remove('d-none');

        const endpoint = decision === 'approved'
            ? `/api/v1/acceptance/deferral/${deferralId}/approve`
            : `/api/v1/acceptance/deferral/${deferralId}/reject`;

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({ notes: notes || null })
            });
            const result = await response.json();
            this.reviewDeferralModal.hide();
            if (result.flash) result.flash.forEach(f => showFlash(f.level, f.message));
            if (!result.error) this.loadDeferredTab();
        } catch (error) {
            console.error('Error reviewing deferral:', error);
            showFlash('danger', 'Error al procesar la solicitud');
        } finally {
            btn.disabled = false;
            spinner.classList.add('d-none');
        }
    }

    showReactivateModal(userId, programId, applicantName, periodName) {
        document.getElementById('reactivateUserId').value = userId;
        document.getElementById('reactivateProgramId').value = programId;
        document.getElementById('reactivateApplicantName').textContent = applicantName;
        document.getElementById('reactivatePeriodName').textContent = periodName || 'Siguiente periodo';
        this.reactivateModal.show();
    }

    async submitReactivate() {
        const userId = document.getElementById('reactivateUserId').value;
        const programId = document.getElementById('reactivateProgramId').value;

        const btn = document.getElementById('confirmReactivateBtn');
        const spinner = document.getElementById('reactivateSpinner');
        btn.disabled = true;
        spinner.classList.remove('d-none');

        try {
            const response = await fetch(
                `/api/v1/acceptance/user/${userId}/program/${programId}/reactivate`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    }
                }
            );
            const result = await response.json();
            this.reactivateModal.hide();
            if (result.flash) result.flash.forEach(f => showFlash(f.level, f.message));
            if (!result.error) { this.loadStats(); this.loadDeferredTab(); }
        } catch (error) {
            console.error('Error reactivating applicant:', error);
            showFlash('danger', 'Error al reactivar al aspirante');
        } finally {
            btn.disabled = false;
            spinner.classList.add('d-none');
        }
    }

}

let acceptanceManager;
document.addEventListener('DOMContentLoaded', () => {
    acceptanceManager = new AcceptanceManager();

    // ── Tiempo real: actualizar cuando hay cambios en aceptación ──
    window.addEventListener('siiap:acceptance:updated', (e) => {
        if (!acceptanceManager) return;
        const data = e.detail;
        // En modo "Todos" reaccionamos a cualquier programa accesible
        if (acceptanceManager.currentProgramId &&
            data?.program_id &&
            String(data.program_id) !== String(acceptanceManager.currentProgramId)) return;

        acceptanceManager.loadStats();
        const activeTab = document.querySelector('.nav-link.active[data-tab]');
        if (activeTab) {
            acceptanceManager.loadTab(activeTab.dataset.tab);
        }
    });
});
