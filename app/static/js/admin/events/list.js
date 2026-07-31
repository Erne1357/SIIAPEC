/* Admin events list page (KPIs + filtros + tabla + wizard crear). */
(() => {
    const C = window.EventsCommon;
    if (!C) {
        console.error('EventsCommon not loaded');
        return;
    }
    const ctx = window.EVENTS_LIST || { detailUrlBase: '/admin/events' };

    let currentEvents = [];
    let programs = [];
    const wizardState = { purpose: null, stage: 1 };

    const modalInstance = (id) => {
        const node = document.getElementById(id);
        return node ? bootstrap.Modal.getOrCreateInstance(node) : null;
    };
    const el = (id) => document.getElementById(id);

    // =================================================================
    // KPIs
    // =================================================================
    async function loadKpis() {
        try {
            const { data } = await C.apiRequest(`${C.API}/events/admin-stats`);
            el('kpiToday').textContent = data.today ?? 0;
            el('kpiUpcoming').textContent = data.upcoming_7d ?? 0;
            el('kpiActive').textContent = data.active ?? 0;
            el('kpiPendingChanges').textContent = data.pending_change_requests ?? 0;
            el('kpiFreeSlots').textContent = data.free_slots ?? 0;
        } catch (err) {
            console.error('Error loading KPIs:', err);
        }
    }

    function applyKpiFilter(action) {
        // Marca visualmente y para lector de pantalla qué KPI está filtrando.
        document.querySelectorAll('[data-kpi-action]').forEach(btn => {
            btn.setAttribute('aria-pressed', String(btn.dataset.kpiAction === action));
        });

        // Reset filters
        el('filterAcademicPeriod').value = '';
        el('filterProgram').value = '';
        el('filterType').value = '';
        el('filterSearch').value = '';

        if (action === 'today' || action === 'upcoming') {
            el('filterStatus').value = '';
            // Best-effort: status published / ongoing — but we don't filter by date server-side.
            // Just reload list to show active events.
        } else if (action === 'active') {
            el('filterStatus').value = '';
        } else if (action === 'pending-changes' || action === 'free-slots') {
            el('filterStatus').value = '';
        }
        loadEvents();
    }

    // =================================================================
    // FILTERS / DROPDOWNS
    // =================================================================
    async function loadPrograms() {
        try {
            const { data } = await C.apiRequest(`${C.API}/programs`);
            programs = data.data || data.items || [];
            const selects = [el('eventProgram'), el('filterProgram')];
            selects.forEach(s => {
                if (!s) return;
                const placeholder = s.id === 'filterProgram' ? 'Todos los programas' : 'Todos los programas';
                s.innerHTML = `<option value="">${placeholder}</option>` +
                    programs.map(p => `<option value="${p.id}">${C.escapeHtml(p.name)}</option>`).join('');
            });
        } catch (err) {
            console.error('Error loading programs:', err);
        }
    }

    async function loadAcademicPeriods() {
        try {
            const { data } = await C.apiRequest(`${C.API}/academic-periods`);
            const periods = data.data || data.items || [];
            const selects = [el('eventAcademicPeriod'), el('filterAcademicPeriod')];
            selects.forEach(s => {
                if (!s) return;
                const placeholder = s.id === 'filterAcademicPeriod'
                    ? 'Todos los periodos'
                    : 'Sin periodo (atemporal)';
                s.innerHTML = `<option value="">${placeholder}</option>` +
                    periods.map(p => `<option value="${p.id}">${C.escapeHtml(p.code || p.name || `Periodo ${p.id}`)}</option>`).join('');
            });
        } catch (err) {
            console.error('Error loading periods:', err);
        }
    }

    function buildFilterQuery() {
        const params = new URLSearchParams();
        const v = (id) => el(id)?.value;
        if (v('filterAcademicPeriod')) params.set('academic_period_id', v('filterAcademicPeriod'));
        if (v('filterProgram')) params.set('program_id', v('filterProgram'));
        if (v('filterType')) params.set('type', v('filterType'));
        if (v('filterStatus')) params.set('status', v('filterStatus'));
        if (v('filterSearch')) params.set('search', v('filterSearch'));
        return params.toString();
    }

    // =================================================================
    // EVENTS TABLE
    // =================================================================
    function eventsTbody() {
        return document.querySelector('#eventsTable tbody');
    }

    function setEventsBusy(busy, message) {
        const tbody = eventsTbody();
        if (tbody) tbody.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (message) window.SIIAP?.announce?.(message);
    }

    async function loadEvents() {
        setEventsBusy(true, 'Cargando eventos…');
        try {
            const qs = buildFilterQuery();
            const url = qs ? `${C.API}/events?${qs}` : `${C.API}/events`;
            const { data } = await C.apiRequest(url);
            currentEvents = data.items || [];
            renderEventsTable();
        } catch (err) {
            C.flash(`Error cargando eventos: ${err.message}`, 'danger');
            const tbody = eventsTbody();
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5">
                            <div class="empty-state empty-state--inline empty-state--error">
                                <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                                <h3 class="empty-state__title">No se pudieron cargar los eventos</h3>
                                <p class="empty-state__description">Revisa tu conexión y vuelve a intentarlo.</p>
                                <div class="empty-state__actions">
                                    <button type="button" id="retryEventsBtn" class="btn btn-outline-primary">
                                        <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>
                                        <span class="btn-label">Reintentar</span>
                                    </button>
                                </div>
                            </div>
                        </td>
                    </tr>`;
                document.getElementById('retryEventsBtn')?.addEventListener('click', loadEvents);
            }
            setEventsBusy(false, 'No se pudieron cargar los eventos.');
        }
    }

    function smartWhen(ev) {
        if (ev.capacity_type === 'single') {
            if (ev.windows_count === 0) return '<span class="text-muted">Sin agenda</span>';
            return `<span class="text-muted small">${ev.windows_count} plazo(s)</span>`;
        }
        if (ev.event_date) return C.formatDateTime(ev.event_date);
        return '<span class="text-muted">Sin fecha</span>';
    }

    function occupancy(ev) {
        if (ev.capacity_type === 'single') {
            const total = ev.slots_total || 0;
            const booked = ev.slots_booked || 0;
            const free = total - booked;
            return `<span title="Ocupados / Totales">${booked} / ${total}</span>
                <small class="text-muted ms-1">(${free} libres)</small>`;
        }
        if (ev.capacity_type === 'multiple') {
            const cap = ev.max_capacity ? `/${ev.max_capacity}` : '';
            return `<span>${ev.registrations_count || 0}${cap}</span>`;
        }
        return `<span>${ev.registrations_count || 0}</span> <small class="text-muted">sin límite</small>`;
    }

    function renderEventsTable() {
        const tbody = eventsTbody();
        if (!tbody) return;
        if (currentEvents.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5">
                        <div class="empty-state empty-state--inline">
                            <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
                            <h3 class="empty-state__title">Sin eventos que mostrar</h3>
                            <p class="empty-state__description">Ningún evento coincide con los filtros actuales. Límpialos o crea un evento nuevo.</p>
                            <div class="empty-state__actions">
                                <button type="button" class="btn btn-outline-primary" id="emptyClearFiltersBtn">
                                    <span class="btn-label">Limpiar filtros</span>
                                </button>
                            </div>
                        </div>
                    </td>
                </tr>`;
            document.getElementById('emptyClearFiltersBtn')?.addEventListener('click', clearFilters);
            setEventsBusy(false, 'Ningún evento coincide con los filtros aplicados.');
            return;
        }
        tbody.innerHTML = currentEvents.map(ev => {
            const typeBadge = `<span class="badge ${C.TYPE_BADGE_CLASS[ev.type] || 'bg-secondary'}">${C.TYPE_LABEL[ev.type] || ev.type}</span>`;
            const statusBadge = ev.status && ev.status !== 'published'
                ? `<span class="badge ${C.STATUS_BADGE_CLASS[ev.status] || 'bg-secondary'} ms-1">${C.STATUS_LABEL[ev.status] || ev.status}</span>`
                : '';
            const visBadge = ev.visibility === 'private'
                ? '<span class="badge bg-dark ms-1"><i class="bi bi-lock-fill me-1" aria-hidden="true"></i>Privado</span>'
                : '';
            const detailUrl = `${ctx.detailUrlBase}/${ev.id}`;
            return `
                <tr class="event-row" data-event-id="${ev.id}" data-event-status="${ev.status}">
                    <td>
                        <div class="d-flex align-items-center gap-2">
                            <i class="bi ${C.TYPE_ICON[ev.type] || 'bi-calendar-event'} text-muted"></i>
                            <div class="min-width-0">
                                <div class="fw-semibold text-truncate">${C.escapeHtml(ev.title)}</div>
                                <div class="small">${typeBadge}${statusBadge}${visBadge}</div>
                            </div>
                        </div>
                    </td>
                    <td>
                        ${ev.program_name ? C.escapeHtml(ev.program_name) : '<span class="text-muted">Todos</span>'}
                        ${ev.academic_period_code ? `<div class="text-muted small">${C.escapeHtml(ev.academic_period_code)}</div>` : ''}
                    </td>
                    <td>${smartWhen(ev)}</td>
                    <td class="text-center">${occupancy(ev)}</td>
                    <td class="text-end" data-no-row-click>
                        <a href="${detailUrl}" class="btn btn-sm btn-outline-primary me-1"
                           aria-label="Abrir el evento ${C.escapeHtml(ev.title)}">
                            <i class="bi bi-arrow-right" aria-hidden="true"></i> Abrir
                        </a>
                        <div class="dropdown d-inline-block">
                            <button class="btn btn-sm btn-outline-secondary dropdown-toggle tap-target" type="button"
                                data-bs-toggle="dropdown" aria-expanded="false"
                                aria-label="Más acciones para ${C.escapeHtml(ev.title)}">
                                <i class="bi bi-three-dots" aria-hidden="true"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end">
                                ${ev.status === 'published' || ev.status === 'ongoing' ? `
                                    <li>
                                        <button class="dropdown-item btn-conclude-event" data-event-id="${ev.id}">
                                            <i class="bi bi-check2-circle me-2"></i>Concluir
                                        </button>
                                    </li>
                                ` : ''}
                                ${ev.status !== 'archived' ? `
                                    <li>
                                        <button class="dropdown-item btn-archive-event" data-event-id="${ev.id}">
                                            <i class="bi bi-archive me-2"></i>Archivar
                                        </button>
                                    </li>
                                ` : `
                                    <li>
                                        <button class="dropdown-item btn-unarchive-event" data-event-id="${ev.id}">
                                            <i class="bi bi-arrow-counterclockwise me-2"></i>Desarchivar
                                        </button>
                                    </li>
                                `}
                                <li><hr class="dropdown-divider"></li>
                                <li>
                                    <button class="dropdown-item text-danger btn-delete-event" data-event-id="${ev.id}">
                                        <i class="bi bi-trash me-2"></i>Eliminar
                                    </button>
                                </li>
                            </ul>
                        </div>
                    </td>
                </tr>`;
        }).join('');
        setEventsBusy(false, `${currentEvents.length} eventos cargados.`);
    }

    function clearFilters() {
        ['filterAcademicPeriod', 'filterProgram', 'filterType', 'filterStatus', 'filterSearch'].forEach(id => {
            if (el(id)) el(id).value = '';
        });
        document.querySelectorAll('[data-kpi-action]').forEach(btn => {
            btn.setAttribute('aria-pressed', 'false');
        });
        loadEvents();
    }

    // =================================================================
    // ROW ACTIONS
    // =================================================================
    async function handleConcludeEvent(eventId) {
        const ev = currentEvents.find(e => e.id === eventId);
        const title = ev ? ev.title : `evento #${eventId}`;
        const ok = await siiapConfirm({
            type: 'warning', title: 'Concluir evento',
            message: `¿Concluir "${title}"?\n\nSe marcará como completado, se cancelarán las invitaciones pendientes y se eliminarán sus imágenes. No se puede revertir.`,
            confirmLabel: 'Sí, concluir'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/conclude`, { method: 'POST' });
            C.flash('Evento concluido', 'success');
            await Promise.all([loadEvents(), loadKpis()]);
        } catch (err) {
            C.flash(`Error al concluir: ${err.message}`, 'danger');
        }
    }

    async function handleArchiveEvent(eventId) {
        const ev = currentEvents.find(e => e.id === eventId);
        const title = ev ? ev.title : `evento #${eventId}`;
        const ok = await siiapConfirm({
            type: 'warning', title: 'Archivar evento',
            message: `¿Archivar "${title}"?\n\nSe ocultará del listado, se notificará a los registrados y se cancelarán invitaciones pendientes.`,
            confirmLabel: 'Sí, archivar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/archive`, { method: 'POST' });
            C.flash('Evento archivado', 'success');
            await Promise.all([loadEvents(), loadKpis()]);
        } catch (err) {
            C.flash(`Error al archivar: ${err.message}`, 'danger');
        }
    }

    async function handleUnarchiveEvent(eventId) {
        const ok = await siiapConfirm({
            type: 'info', title: 'Reactivar evento',
            message: '¿Volver a publicar este evento?', confirmLabel: 'Sí, reactivar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/unarchive`, {
                method: 'POST', body: JSON.stringify({ new_status: 'published' })
            });
            C.flash('Evento reactivado', 'success');
            await Promise.all([loadEvents(), loadKpis()]);
        } catch (err) {
            C.flash(`Error al reactivar: ${err.message}`, 'danger');
        }
    }

    function openDeleteEventModal(eventId) {
        const ev = currentEvents.find(e => e.id === eventId);
        if (!ev) return;
        el('deleteEventId').value = eventId;
        el('deleteEventTitle').textContent = ev.title;
        const warning = el('deleteEventWarning');
        if (ev.slots_booked > 0) {
            el('deleteEventAppointmentsCount').textContent = ev.slots_booked;
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
        modalInstance('confirmDeleteEventModal')?.show();
    }

    async function handleDeleteEvent() {
        const eventId = parseInt(el('deleteEventId').value);
        if (!eventId) return;
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}`, { method: 'DELETE' });
            if (data.requires_force) {
                const confirmed = await siiapConfirm({
                    type: 'danger', title: 'Eliminar evento con citas',
                    message: data.message + '\n\n¿Eliminar de todas formas?',
                    confirmLabel: 'Sí, eliminar'
                });
                if (!confirmed) return;
                await C.apiRequest(`${C.API}/events/${eventId}?force=true`, { method: 'DELETE' });
            }
            C.flash('Evento eliminado', 'success');
            modalInstance('confirmDeleteEventModal')?.hide();
            await Promise.all([loadEvents(), loadKpis()]);
        } catch (err) {
            C.flash(`Error eliminando evento: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // WIZARD CREATE
    // =================================================================
    function showWizardStage(n) {
        document.getElementById('wizardStage1').classList.toggle('active', n === 1);
        document.getElementById('wizardStage2').classList.toggle('active', n === 2);

        // Stepper: refleja el paso actual y el ya completado. El paso completado
        // sustituye su número por la paloma, igual que hace el macro stepper()
        // en Jinja: si no, el mismo estado se ve distinto según quién lo pinte.
        document.querySelectorAll('#wizardStepper .stepper__step').forEach(step => {
            const index = parseInt(step.dataset.step, 10);
            const done = index < n;
            step.classList.toggle('stepper__step--active', index === n);
            step.classList.toggle('stepper__step--completed', done);
            if (index === n) step.setAttribute('aria-current', 'step');
            else step.removeAttribute('aria-current');

            const number = step.querySelector('.stepper__number');
            if (number) {
                number.innerHTML = done
                    ? '<i class="bi bi-check-lg" aria-hidden="true"></i>'
                    : String(index);
            }
        });

        document.getElementById('wizardBtnBack').classList.toggle('d-none', n === 1);
        document.getElementById('wizardBtnSubmit').classList.toggle('d-none', n === 1);
        wizardState.stage = n;
        window.SIIAP?.announce?.(n === 1
            ? 'Paso 1 de 2: selecciona el propósito del evento.'
            : 'Paso 2 de 2: configura el evento.');
    }

    function selectPurpose(purpose) {
        wizardState.purpose = purpose;
        document.querySelectorAll('.wizard-purpose-card').forEach(c => {
            const isSelected = c.dataset.purpose === purpose;
            c.classList.toggle('selected', isSelected);
            c.setAttribute('aria-pressed', String(isSelected));
        });

        const capTypeSelect = el('eventCapacityType');
        const fieldDates = el('fieldEventDates');
        const fieldEndDates = el('fieldEventEndDates');
        const fieldMaxCap = el('fieldMaxCapacity');
        const fieldCapType = el('fieldCapacityType');
        const typeSelect = el('eventType');

        if (purpose === 'single') {
            capTypeSelect.value = 'single';
            Array.from(typeSelect.options).forEach(opt => {
                opt.hidden = !['interview', 'defense'].includes(opt.value);
            });
            typeSelect.value = 'interview';
            fieldDates.classList.add('d-none');
            fieldEndDates.classList.add('d-none');
            fieldMaxCap.classList.add('d-none');
            fieldCapType.classList.add('d-none');
        } else if (purpose === 'multiple') {
            capTypeSelect.value = 'multiple';
            Array.from(typeSelect.options).forEach(opt => {
                opt.hidden = ['interview', 'defense'].includes(opt.value);
            });
            typeSelect.value = 'workshop';
            fieldDates.classList.remove('d-none');
            fieldEndDates.classList.remove('d-none');
            fieldMaxCap.classList.remove('d-none');
            el('eventMaxCapacity').required = true;
            fieldCapType.classList.remove('d-none');
            Array.from(capTypeSelect.options).forEach(opt => {
                opt.hidden = opt.value === 'single';
            });
        } else {
            Array.from(typeSelect.options).forEach(opt => { opt.hidden = false; });
            typeSelect.value = 'other';
            fieldDates.classList.remove('d-none');
            fieldEndDates.classList.remove('d-none');
            fieldMaxCap.classList.add('d-none');
            el('eventMaxCapacity').required = false;
            fieldCapType.classList.remove('d-none');
            Array.from(capTypeSelect.options).forEach(opt => { opt.hidden = false; });
        }

        showWizardStage(2);
    }

    function setupWizard() {
        document.querySelectorAll('.wizard-purpose-card').forEach(card => {
            card.addEventListener('click', () => selectPurpose(card.dataset.purpose));
        });
        el('wizardBtnBack')?.addEventListener('click', () => showWizardStage(1));
        el('eventCapacityType')?.addEventListener('change', (e) => {
            if (!['multiple', 'other'].includes(wizardState.purpose)) return;
            const fieldMaxCap = el('fieldMaxCapacity');
            const maxInput = el('eventMaxCapacity');
            if (e.target.value === 'multiple') {
                fieldMaxCap.classList.remove('d-none');
                maxInput.required = true;
            } else {
                fieldMaxCap.classList.add('d-none');
                maxInput.required = false;
                maxInput.value = '';
            }
        });
        const modalEl = el('createEventModal');
        modalEl?.addEventListener('hidden.bs.modal', resetWizard);
    }

    function resetWizard() {
        wizardState.purpose = null;
        wizardState.stage = 1;
        document.querySelectorAll('.wizard-purpose-card').forEach(c => {
            c.classList.remove('selected');
            c.setAttribute('aria-pressed', 'false');
        });
        const typeSelect = el('eventType');
        if (typeSelect) {
            Array.from(typeSelect.options).forEach(opt => { opt.hidden = false; });
            typeSelect.value = 'interview';
        }
        el('fieldEventDates')?.classList.add('d-none');
        el('fieldEventEndDates')?.classList.add('d-none');
        el('fieldMaxCapacity')?.classList.add('d-none');
        el('fieldCapacityType')?.classList.add('d-none');
        if (el('eventMaxCapacity')) {
            el('eventMaxCapacity').required = false;
            el('eventMaxCapacity').value = '';
        }
        showWizardStage(1);
        el('createEventForm')?.reset();
    }

    async function handleCreateEvent(e) {
        e.preventDefault();
        if (!wizardState.purpose) {
            C.flash('Selecciona el tipo de evento', 'warning');
            showWizardStage(1);
            return;
        }
        const capacityType = el('eventCapacityType').value;
        const maxCapacity = el('eventMaxCapacity').value;
        if (capacityType === 'multiple' && (!maxCapacity || parseInt(maxCapacity) < 1)) {
            C.flash('Capacidad máxima inválida', 'warning');
            return;
        }
        const eventDate = el('eventDateCreate')?.value || null;
        const eventEndDate = el('eventEndDateCreate')?.value || null;
        const programId = el('eventProgram').value;
        const periodId = el('eventAcademicPeriod')?.value;

        const payload = {
            title: el('eventTitle').value.trim(),
            program_id: programId ? parseInt(programId) : null,
            academic_period_id: periodId ? parseInt(periodId) : null,
            type: el('eventType').value,
            location: el('eventLocation').value,
            description: el('eventDescription').value,
            capacity_type: capacityType,
            max_capacity: capacityType === 'multiple' ? parseInt(maxCapacity) : null,
            event_date: eventDate || null,
            event_end_date: eventEndDate || null,
            requires_registration: el('eventRequiresRegistration').checked,
            allows_attendance_tracking: el('eventAllowsAttendance').checked,
            visible_to_students: el('eventVisibleToStudents').checked,
            visibility: document.querySelector('input[name="eventVisibility"]:checked')?.value || 'public',
            reminders_enabled: el('eventRemindersEnabled').checked,
            status: el('eventStatus').value
        };

        if (!payload.title) {
            C.flash('El título es requerido', 'warning');
            return;
        }

        try {
            const { data } = await C.apiRequest(`${C.API}/events`, {
                method: 'POST', body: JSON.stringify(payload)
            });
            C.flash('Evento creado exitosamente', 'success');
            modalInstance('createEventModal')?.hide();
            const newId = data.event_id || data.id;
            if (newId) {
                window.location.href = `${ctx.detailUrlBase}/${newId}`;
            } else {
                resetWizard();
                await Promise.all([loadEvents(), loadKpis()]);
            }
        } catch (err) {
            C.flash(`Error creando evento: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // EVENT LISTENERS
    // =================================================================
    function setupListeners() {
        // KPI click filter
        document.querySelectorAll('[data-kpi-action]').forEach(btn => {
            btn.addEventListener('click', () => applyKpiFilter(btn.dataset.kpiAction));
        });

        // Filters
        ['filterAcademicPeriod', 'filterProgram', 'filterType', 'filterStatus'].forEach(id => {
            el(id)?.addEventListener('change', loadEvents);
        });
        let searchTimer = null;
        el('filterSearch')?.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(loadEvents, 350);
        });
        el('btnClearFilters')?.addEventListener('click', clearFilters);

        // Row interactions
        document.querySelector('#eventsTable')?.addEventListener('click', (e) => {
            // dropdown actions
            const concludeBtn = e.target.closest('.btn-conclude-event');
            const archiveBtn = e.target.closest('.btn-archive-event');
            const unarchiveBtn = e.target.closest('.btn-unarchive-event');
            const deleteBtn = e.target.closest('.btn-delete-event');
            if (concludeBtn) { e.stopPropagation(); handleConcludeEvent(parseInt(concludeBtn.dataset.eventId)); return; }
            if (archiveBtn) { e.stopPropagation(); handleArchiveEvent(parseInt(archiveBtn.dataset.eventId)); return; }
            if (unarchiveBtn) { e.stopPropagation(); handleUnarchiveEvent(parseInt(unarchiveBtn.dataset.eventId)); return; }
            if (deleteBtn) { e.stopPropagation(); openDeleteEventModal(parseInt(deleteBtn.dataset.eventId)); return; }

            // Row click navigates to detail
            if (e.target.closest('[data-no-row-click]')) return;
            const row = e.target.closest('tr.event-row');
            if (row) {
                const id = parseInt(row.dataset.eventId);
                window.location.href = `${ctx.detailUrlBase}/${id}`;
            }
        });

        // Confirm delete
        el('confirmDeleteEventBtn')?.addEventListener('click', handleDeleteEvent);

        // Wizard
        setupWizard();
        el('createEventForm')?.addEventListener('submit', handleCreateEvent);

        // Realtime
        let refreshTimer = null;
        const refresh = () => {
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => { loadEvents(); loadKpis(); }, 700);
        };
        window.addEventListener('siiap:appointment:changed', refresh);
        window.addEventListener('siiap:event:changed', refresh);
    }

    // =================================================================
    // INIT
    // =================================================================
    async function init() {
        await Promise.all([loadPrograms(), loadAcademicPeriods()]);
        await Promise.all([loadEvents(), loadKpis()]);
        setupListeners();
    }

    document.addEventListener('DOMContentLoaded', init);
})();
