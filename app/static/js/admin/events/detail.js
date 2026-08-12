/* Admin events detail page (orchestrator). */
(() => {
    const C = window.EventsCommon;
    if (!C) {
        console.error('EventsCommon not loaded');
        return;
    }
    const ctx = window.EVENT_DETAIL || {};
    const eventId = ctx.eventId;

    // ---------- State ----------
    let currentEvent = null;
    let currentWindows = [];
    let currentSlots = [];
    let currentChangeRequests = [];
    let currentRegistrations = [];
    // ¿Quien mira administra este evento? Lo declara la respuesta de
    // /attendance/event/<id>/registrations. Por defecto true: si un endpoint
    // antiguo no lo manda, la página se comporta como antes.
    let registrationsCanManage = true;
    let currentInvitations = [];
    let eligibleStudents = [];
    let programs = [];
    let attendanceFilter = '';
    let slotsFilter = 'all';

    // Track loaded panes to avoid duplicate fetches
    const loaded = {
        windows: false, slots: false, changeRequests: false,
        registrations: false, invitations: false, attendance: false
    };

    // Modals (lazy init)
    const modal = (id) => {
        const elNode = document.getElementById(id);
        return elNode ? bootstrap.Modal.getOrCreateInstance(elNode) : null;
    };

    function el(id) { return document.getElementById(id); }

    /**
     * Marca una región dinámica como ocupada/lista y lo anuncia por la región
     * viva única (SIIAP.announce). `target` puede ser un <tbody> o un contenedor.
     */
    function setRegionBusy(target, busy, message) {
        if (target) target.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (message) window.SIIAP?.announce?.(message);
    }

    /** Fila de estado vacío dentro de una tabla (componente .empty-state). */
    function emptyRow(colspan, icon, title, description) {
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

    /** Fila de error de carga con botón de reintento. */
    function errorRow(colspan, title, description, retryId) {
        return `
            <tr>
                <td colspan="${colspan}">
                    <div class="empty-state empty-state--inline empty-state--error">
                        <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                        <h3 class="empty-state__title">${title}</h3>
                        <p class="empty-state__description">${description}</p>
                        <div class="empty-state__actions">
                            <button type="button" id="${retryId}" class="btn btn-outline-primary">
                                <i class="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>
                                <span class="btn-label">Reintentar</span>
                            </button>
                        </div>
                    </div>
                </td>
            </tr>`;
    }

    // =================================================================
    // HEADER
    // =================================================================
    function setBreadcrumb(title) {
        const node = el('breadcrumbEventTitle');
        if (node) node.textContent = title || 'Evento';
        document.title = `${title || 'Evento'} - SIIAP`;
    }

    function renderHeader(ev) {
        const iconNode = el('eventDetailIcon');
        if (iconNode) iconNode.innerHTML = `<i class="bi ${C.TYPE_ICON[ev.type] || 'bi-calendar-event'}"></i>`;

        const titleNode = el('eventDetailTitle');
        if (titleNode) titleNode.textContent = ev.title || '';

        const typeBadge = el('eventDetailTypeBadge');
        if (typeBadge) {
            typeBadge.className = `badge ${C.TYPE_BADGE_CLASS[ev.type] || 'bg-secondary'}`;
            typeBadge.textContent = C.TYPE_LABEL[ev.type] || ev.type;
        }

        const statusBadge = el('eventDetailStatusBadge');
        if (statusBadge) {
            statusBadge.className = `badge ${C.STATUS_BADGE_CLASS[ev.status] || 'bg-secondary'}`;
            statusBadge.textContent = C.STATUS_LABEL[ev.status] || ev.status;
            statusBadge.classList.remove('d-none');
        }

        const visBadge = el('eventDetailVisibilityBadge');
        if (visBadge) visBadge.classList.toggle('d-none', ev.visibility !== 'private');

        const subtitle = el('eventDetailSubtitle');
        if (subtitle) {
            const parts = [];
            parts.push(ev.program_name
                ? `<i class="bi bi-mortarboard me-1"></i>${C.escapeHtml(ev.program_name)}`
                : '<i class="bi bi-globe me-1"></i>Todos los programas');
            parts.push(ev.academic_period_code
                ? `<i class="bi bi-calendar3 me-1"></i>${C.escapeHtml(ev.academic_period_code)}`
                : '<i class="bi bi-infinity me-1"></i>Atemporal');
            parts.push(`<i class="bi bi-people-fill me-1"></i>${C.CAPACITY_LABEL[ev.capacity_type] || ev.capacity_type}`);
            subtitle.innerHTML = parts.join(' &middot; ');
        }

        const header = el('eventDetailHeader');
        if (header) header.className = `event-detail-header card mb-3 event-detail-border-${ev.type || 'other'}`;

        const togglePrivacyLabel = el('togglePrivacyLabel');
        if (togglePrivacyLabel) {
            togglePrivacyLabel.textContent = ev.visibility === 'private' ? 'Hacer público' : 'Hacer privado';
        }

        const btnArchive = el('btnArchiveEvent');
        const btnUnarchive = el('btnUnarchiveEvent');
        if (btnArchive && btnUnarchive) {
            const isArchived = ev.status === 'archived';
            btnArchive.classList.toggle('d-none', isArchived);
            btnUnarchive.classList.toggle('d-none', !isArchived);
        }
    }

    function renderStatChips(ev) {
        const node = el('eventStatChips');
        if (!node) return;
        const chips = [];

        if (ev.capacity_type === 'single') {
            chips.push(`<span class="event-stat-chip"><i class="bi bi-calendar2-check" aria-hidden="true"></i> Horarios ocupados: <strong>${ev.slots_booked || 0}/${ev.slots_total || 0}</strong></span>`);
            if (ev.windows_count) {
                chips.push(`<span class="event-stat-chip"><i class="bi bi-clock-history" aria-hidden="true"></i> ${ev.windows_count} plazo(s)</span>`);
            }
        } else {
            const cap = ev.max_capacity ? `/${ev.max_capacity}` : '';
            chips.push(`<span class="event-stat-chip"><i class="bi bi-people" aria-hidden="true"></i> Registrados: <strong>${ev.registrations_count || 0}${cap}</strong></span>`);
            if (ev.invitations_pending) {
                chips.push(`<span class="event-stat-chip event-stat-chip--warning"><i class="bi bi-envelope" aria-hidden="true"></i> ${ev.invitations_pending} invitaciones pendientes</span>`);
            }
            if (ev.event_date) {
                chips.push(`<span class="event-stat-chip"><i class="bi bi-calendar-event" aria-hidden="true"></i> ${C.formatDateTime(ev.event_date)}</span>`);
            }
        }

        if (ev.location) {
            chips.push(`<span class="event-stat-chip"><i class="bi bi-geo-alt" aria-hidden="true"></i> ${C.escapeHtml(ev.location)}</span>`);
        }

        node.innerHTML = chips.join('');
    }

    function applyTabsByCapacity(capacityType) {
        const isSingle = capacityType === 'single';
        document.querySelectorAll('#eventDetailTabs .single-only').forEach(li => li.classList.toggle('d-none', !isSingle));
        document.querySelectorAll('#eventDetailTabs .multi-only').forEach(li => li.classList.toggle('d-none', isSingle));
    }

    function updateTabBadges(ev) {
        const att = el('attendeesBadge');
        if (att) att.textContent = ev.registrations_count || 0;
        const inv = el('invitationsBadge');
        if (inv) inv.textContent = ev.invitations_pending || 0;
    }

    function renderSummary(ev) {
        const set = (id, val) => { const node = el(id); if (node) node.innerHTML = val || '&mdash;'; };
        set('summaryProgram', ev.program_name ? C.escapeHtml(ev.program_name) : '<span class="text-muted">Todos los programas</span>');
        set('summaryPeriod', ev.academic_period_code ? C.escapeHtml(ev.academic_period_code) : '<span class="text-muted">Sin periodo</span>');
        let cap = C.CAPACITY_LABEL[ev.capacity_type] || ev.capacity_type;
        if (ev.capacity_type === 'multiple' && ev.max_capacity) cap += ` &mdash; máx. ${ev.max_capacity}`;
        set('summaryCapacity', cap);
        set('summaryLocation', ev.location ? C.escapeHtml(ev.location) : '<span class="text-muted">Sin lugar</span>');

        let datesHtml = '<span class="text-muted">No definidas</span>';
        if (ev.event_date) {
            datesHtml = C.formatDateTime(ev.event_date);
            if (ev.event_end_date) datesHtml += ` &rarr; ${C.formatDateTime(ev.event_end_date)}`;
        }
        set('summaryDates', datesHtml);

        const flags = [];
        if (ev.requires_registration) flags.push('Requiere registro');
        if (ev.allows_attendance_tracking) flags.push('Control de asistencia');
        if (ev.visible_to_students) flags.push('Visible a estudiantes');
        if (ev.reminders_enabled) flags.push('Recordatorios activos');
        set('summaryFlags', flags.length
            ? flags.map(f => `<span class="badge bg-light text-dark border me-1">${f}</span>`).join('')
            : '<span class="text-muted">Sin opciones activadas</span>');

        const desc = el('summaryDescription');
        if (desc) {
            if (ev.description) {
                desc.textContent = ev.description;
                desc.classList.remove('text-muted', 'fst-italic');
            } else {
                desc.innerHTML = '<span class="text-muted fst-italic">Sin descripción</span>';
            }
        }
    }

    // =================================================================
    // EVENT DETAIL LOAD
    // =================================================================
    async function loadEventDetails() {
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}`);
            currentEvent = data;
            setBreadcrumb(data.title);
            renderHeader(data);
            renderStatChips(data);
            applyTabsByCapacity(data.capacity_type);
            updateTabBadges(data);
            renderSummary(data);
            return data;
        } catch (err) {
            C.flash(err.message || 'No se pudo cargar el evento', 'error');
            setBreadcrumb('Evento no encontrado');
        }
    }

    async function refreshEventOnly() {
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}`);
            currentEvent = data;
            renderHeader(data);
            renderStatChips(data);
            updateTabBadges(data);
            renderSummary(data);
        } catch (err) {
            console.error(err);
        }
    }

    // =================================================================
    // PROGRAMS (for assign program filter)
    // =================================================================
    async function loadPrograms() {
        try {
            const { data } = await C.apiRequest(`${C.API}/programs`);
            programs = data.data || data.items || [];
            const filter = el('assignProgramFilter');
            if (filter && programs.length) {
                filter.innerHTML = '<option value="">Todos los programas</option>' +
                    programs.map(p => `<option value="${p.id}">${C.escapeHtml(p.name)}</option>`).join('');
            }
        } catch (err) {
            console.error('Error loading programs:', err);
        }
    }

    // =================================================================
    // WINDOWS
    // =================================================================
    async function loadWindows() {
        const tbody = document.querySelector('#windowsTable tbody');
        setRegionBusy(tbody, true, 'Cargando plazos de horarios…');
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}/windows-list`);
            currentWindows = data.windows || [];
            renderWindowsTable();
            loaded.windows = true;
        } catch (err) {
            C.flash(`Error cargando plazos: ${err.message}`, 'danger');
            if (tbody) {
                tbody.innerHTML = errorRow(8, 'No se pudieron cargar los plazos',
                    'Revisa tu conexión y vuelve a intentarlo.', 'retryWindowsBtn');
                document.getElementById('retryWindowsBtn')?.addEventListener('click', loadWindows);
            }
            setRegionBusy(tbody, false, 'No se pudieron cargar los plazos de horarios.');
        }
    }

    function renderWindowsTable() {
        const tbody = document.querySelector('#windowsTable tbody');
        if (!tbody) return;
        if (currentWindows.length === 0) {
            tbody.innerHTML = emptyRow(8, 'calendar-plus', 'Todavía no hay plazos',
                'Crea un plazo para generar los horarios de atención del evento.');
            setRegionBusy(tbody, false, 'No hay plazos creados para este evento.');
            return;
        }
        tbody.innerHTML = currentWindows.map(w => {
            const date = C.formatDate(w.date);
            const startTime = (w.start_time || '').substring(0, 5);
            const endTime = (w.end_time || '').substring(0, 5);
            const when = `${date}, ${startTime} a ${endTime}`;
            return `
                <tr data-window-id="${w.id}">
                    <th scope="row" class="fw-normal">${date}</th>
                    <td>${startTime} - ${endTime}</td>
                    <td>${w.slot_minutes} min</td>
                    <td class="text-center">
                        ${w.slots_generated
                            ? '<span class="badge bg-success"><i class="bi bi-check" aria-hidden="true"></i> Sí</span>'
                            : '<span class="badge bg-secondary"><i class="bi bi-x" aria-hidden="true"></i> No</span>'}
                    </td>
                    <td class="text-center">${w.slots_total}</td>
                    <td class="text-center"><span class="badge bg-success">${w.slots_free}</span></td>
                    <td class="text-center">
                        <span class="badge bg-${w.slots_booked > 0 ? 'warning text-dark' : 'secondary'}">${w.slots_booked}</span>
                    </td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">
                            ${!w.slots_generated ? `
                                <button type="button" class="btn btn-outline-success btn-generate-window-slots tap-target"
                                    data-window-id="${w.id}"
                                    aria-label="Generar los horarios del plazo del ${when}"
                                    title="Generar los horarios de este plazo">
                                    <i class="bi bi-gear" aria-hidden="true"></i>
                                </button>
                            ` : ''}
                            <button type="button" class="btn btn-outline-danger btn-delete-window tap-target"
                                data-window-id="${w.id}"
                                data-date="${date}"
                                data-time="${startTime} - ${endTime}"
                                data-slots-booked="${w.slots_booked}"
                                aria-label="Eliminar el plazo del ${when}"
                                title="Eliminar plazo">
                                <i class="bi bi-trash" aria-hidden="true"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false, `${currentWindows.length} plazos cargados.`);
    }

    async function handleAddWindow(e) {
        e.preventDefault();
        const payload = {
            date: el('windowDate').value,
            start_time: el('windowStartTime').value,
            end_time: el('windowEndTime').value,
            slot_minutes: parseInt(el('windowSlotMinutes').value)
        };
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/windows`, {
                method: 'POST', body: JSON.stringify(payload)
            });
            C.flash('Plazo creado exitosamente', 'success');
            modal('addWindowModal')?.hide();
            el('addWindowForm').reset();
            await loadWindows();
            if (loaded.slots) await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error creando plazo: ${err.message}`, 'danger');
        }
    }

    async function handleGenerateWindowSlots(windowId) {
        try {
            const { data } = await C.apiRequest(
                `${C.API}/events/windows/${windowId}/generate-slots`, { method: 'POST' });
            C.flash(data.created > 0
                ? `Se generaron ${data.created} horarios nuevos`
                : 'No se generaron horarios nuevos: ya existen.', data.created > 0 ? 'success' : 'info');
            await loadWindows();
            if (loaded.slots) await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error generando horarios: ${err.message}`, 'danger');
        }
    }

    async function handleGenerateAllSlots() {
        if (!currentEvent || !currentWindows.length) {
            C.flash('Primero agrega plazos de horarios', 'warning');
            return;
        }
        let totalCreated = 0;
        for (const w of currentWindows) {
            try {
                const { data } = await C.apiRequest(
                    `${C.API}/events/windows/${w.id}/generate-slots`, { method: 'POST' });
                totalCreated += data.created || 0;
            } catch (err) {
                console.error(`Error generando slots ventana ${w.id}:`, err);
            }
        }
        C.flash(totalCreated > 0
            ? `Se generaron ${totalCreated} nuevos horarios`
            : 'No se generaron nuevos horarios.', totalCreated > 0 ? 'success' : 'info');
        await loadWindows();
        if (loaded.slots) await loadSlots();
        await refreshEventOnly();
    }

    function openDeleteWindowModal(windowId, date, time, slotsBooked) {
        el('deleteWindowId').value = windowId;
        el('deleteWindowDate').textContent = date;
        el('deleteWindowTime').textContent = time;
        const warning = el('deleteWindowWarning');
        const count = el('deleteWindowSlotsCount');
        if (slotsBooked > 0) {
            count.textContent = slotsBooked;
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
        modal('confirmDeleteWindowModal')?.show();
    }

    async function handleDeleteWindow() {
        const windowId = parseInt(el('deleteWindowId').value);
        if (!windowId) return;
        try {
            const { data } = await C.apiRequest(`${C.API}/events/windows/${windowId}`, { method: 'DELETE' });
            if (data.requires_force) {
                const confirmed = await siiapConfirm({
                    type: 'danger', title: 'Eliminar plazo',
                    message: data.message + '\n\n¿Eliminar de todas formas?',
                    confirmLabel: 'Sí, eliminar'
                });
                if (!confirmed) return;
                await C.apiRequest(`${C.API}/events/windows/${windowId}?force=true`, { method: 'DELETE' });
            }
            C.flash('Plazo eliminado exitosamente', 'success');
            modal('confirmDeleteWindowModal')?.hide();
            await loadWindows();
            if (loaded.slots) await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error eliminando plazo: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // SLOTS
    // =================================================================
    async function loadSlots() {
        setRegionBusy(document.querySelector('#slotsTable tbody'), true, 'Cargando horarios…');
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}/slots`);
            const items = data.items || [];
            const enriched = await Promise.all(items.map(async (slot) => {
                if (slot.status === 'booked') {
                    try {
                        const { data: a } = await C.apiRequest(`${C.API}/appointments/by-slot/${slot.id}`);
                        if (a.appointment) {
                            return {
                                ...slot,
                                appointment_id: a.appointment.id,
                                student_name: a.appointment.student?.full_name || 'Sin asignar'
                            };
                        }
                    } catch (e) { /* ignore */ }
                }
                return slot;
            }));
            currentSlots = enriched;
            renderSlotsTable();
            updateSlotCounts();
            loaded.slots = true;
        } catch (err) {
            C.flash(`Error cargando horarios: ${err.message}`, 'danger');
            const tbody = document.querySelector('#slotsTable tbody');
            if (tbody) {
                tbody.innerHTML = errorRow(5, 'No se pudieron cargar los horarios',
                    'Revisa tu conexión y vuelve a intentarlo.', 'retrySlotsBtn');
                document.getElementById('retrySlotsBtn')?.addEventListener('click', loadSlots);
            }
            setRegionBusy(tbody, false, 'No se pudieron cargar los horarios.');
        }
    }

    function filteredSlots() {
        if (slotsFilter === 'free') return currentSlots.filter(s => s.status === 'free');
        if (slotsFilter === 'booked') return currentSlots.filter(s => s.status === 'booked');
        return currentSlots;
    }

    function renderSlotsTable() {
        const tbody = document.querySelector('#slotsTable tbody');
        if (!tbody) return;
        const list = filteredSlots();
        if (list.length === 0) {
            tbody.innerHTML = emptyRow(5, 'calendar2-x', 'Sin horarios que mostrar',
                'Cambia el filtro o genera los horarios desde la pestaña «Horarios».');
            setRegionBusy(tbody, false, 'No hay horarios para el filtro seleccionado.');
            return;
        }
        tbody.innerHTML = list.map(slot => {
            const startTime = new Date(slot.starts_at);
            const endTime = new Date(slot.ends_at);
            const dateStr = startTime.toLocaleDateString('es-MX');
            const timeStr = `${startTime.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })} - ${endTime.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}`;
            const slotInfo = `${dateStr} ${timeStr}`;
            const safeInfo = C.escapeHtml(slotInfo);
            const statusChip = slot.status === 'free'
                ? '<span class="badge bg-success"><i class="bi bi-unlock me-1" aria-hidden="true"></i>Libre</span>'
                : slot.status === 'booked'
                    ? '<span class="badge bg-primary"><i class="bi bi-person-check me-1" aria-hidden="true"></i>Ocupado</span>'
                    : `<span class="badge bg-secondary">${C.escapeHtml(slot.status)}</span>`;
            return `
                <tr data-slot-id="${slot.id}" class="slot-${slot.status}">
                    <th scope="row" class="fw-normal">${dateStr}</th>
                    <td>${timeStr}</td>
                    <td class="text-center">${statusChip}</td>
                    <td>${C.escapeHtml(slot.student_name || '—')}</td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">
                            ${slot.status === 'free' ? `
                                <button type="button" class="btn btn-outline-primary btn-assign-slot tap-target"
                                    data-slot-id="${slot.id}" data-slot-info="${safeInfo}"
                                    aria-label="Asignar un estudiante al horario del ${safeInfo}"
                                    title="Asignar estudiante">
                                    <i class="bi bi-person-plus" aria-hidden="true"></i>
                                </button>
                            ` : slot.status === 'booked' && slot.appointment_id ? `
                                <button type="button" class="btn btn-outline-danger btn-cancel-appointment tap-target"
                                    data-appointment-id="${slot.appointment_id}"
                                    data-slot-info="${safeInfo}"
                                    data-student-name="${C.escapeHtml(slot.student_name || 'Sin asignar')}"
                                    aria-label="Cancelar la cita del ${safeInfo}"
                                    title="Cancelar cita">
                                    <i class="bi bi-x-lg" aria-hidden="true"></i>
                                </button>
                            ` : ''}
                            <button type="button" class="btn btn-outline-secondary btn-delete-slot tap-target"
                                data-slot-id="${slot.id}"
                                data-slot-info="${safeInfo}"
                                data-student-name="${C.escapeHtml(slot.student_name || '')}"
                                data-is-booked="${slot.status === 'booked'}"
                                aria-label="Eliminar el horario del ${safeInfo}"
                                title="Eliminar horario">
                                <i class="bi bi-trash" aria-hidden="true"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false, `${list.length} horarios mostrados.`);
    }

    function updateSlotCounts() {
        const free = currentSlots.filter(s => s.status === 'free').length;
        const booked = currentSlots.filter(s => s.status === 'booked').length;
        el('freeSlots').textContent = free;
        el('bookedSlots').textContent = booked;
        el('totalSlots').textContent = currentSlots.length;
    }

    function openAssignModal(slotId, slotInfo) {
        el('assignEventId').value = eventId;
        el('assignSlotId').value = slotId;
        el('assignStudentId').value = '';
        el('assignSlotInfo').textContent = slotInfo;
        el('assignStudentSearch').value = '';
        el('assignNotes').value = '';
        el('btnConfirmAssign').disabled = true;

        const filter = el('assignProgramFilter');
        if (filter) {
            if (currentEvent?.program_id) {
                filter.value = currentEvent.program_id;
                filter.disabled = true;
            } else {
                filter.value = '';
                filter.disabled = false;
            }
        }

        renderAssignStudentsList();
        modal('assignSlotModal')?.show();
    }

    function renderAssignStudentsList() {
        const list = el('assignStudentsList');
        if (!list) return;
        const search = (el('assignStudentSearch').value || '').toLowerCase().trim();
        const programFilter = el('assignProgramFilter').value;
        let filtered = eligibleStudents;
        if (programFilter) filtered = filtered.filter(s => String(s.program_id) === String(programFilter));
        if (search) {
            filtered = filtered.filter(s => {
                const blob = `${s.full_name || ''} ${s.email || ''} ${s.program_name || ''}`.toLowerCase();
                return blob.includes(search);
            });
        }
        if (filtered.length === 0) {
            const msg = eligibleStudents.length === 0
                ? 'No hay estudiantes elegibles registrados.'
                : 'Ningún estudiante coincide con los filtros actuales.';
            list.innerHTML = `<p class="list-group-item text-center text-muted py-3 mb-0">${msg}</p>`;
            return;
        }
        const selectedId = el('assignStudentId').value;
        list.innerHTML = filtered.map(s => {
            const isSelected = String(selectedId) === String(s.id);
            return `
            <button type="button" class="list-group-item list-group-item-action assign-student-item ${isSelected ? 'active' : ''}"
                data-student-id="${s.id}" aria-pressed="${isSelected}">
                <div class="d-flex align-items-center gap-2">
                    <img src="${s.avatar_url || '/static/assets/images/default.jpg'}"
                        class="rounded-circle avatar-xs" alt="">
                    <div class="flex-grow-1 text-start">
                        <span class="fw-semibold small d-block">${C.escapeHtml(s.full_name)}</span>
                        <span class="text-muted small">${C.escapeHtml(s.email || '')} ${s.program_name ? '&middot; ' + C.escapeHtml(s.program_name) : ''}</span>
                    </div>
                    <i class="bi bi-check-circle-fill assign-student-item__check" aria-hidden="true"></i>
                </div>
            </button>`;
        }).join('');
    }

    async function loadEligibleStudents(programId) {
        try {
            if (programId) {
                const { data } = await C.apiRequest(`${C.API}/interviews/eligible-students/${programId}`);
                eligibleStudents = (data.eligible_students || []).map(s => ({ ...s, program_id: programId }));
            } else {
                const { data } = await C.apiRequest(`${C.API}/interviews/eligible-students`);
                const groups = data.programs || [];
                eligibleStudents = [];
                groups.forEach(g => {
                    (g.eligible_students || []).forEach(s => {
                        eligibleStudents.push({ ...s, program_id: g.program_id, program_name: g.program_name });
                    });
                });
            }
        } catch (err) {
            console.error('Error loading eligible students:', err);
            eligibleStudents = [];
        }
    }

    async function handleAssignSlot(e) {
        e.preventDefault();
        const payload = {
            event_id: el('assignEventId').value,
            slot_id: el('assignSlotId').value,
            // Identificador público del aspirante (UUID): cadena, no entero.
            applicant_id: el('assignStudentId').value || null,
            notes: el('assignNotes').value
        };
        if (!payload.applicant_id) {
            C.flash('Selecciona un estudiante', 'warning');
            return;
        }
        try {
            await C.apiRequest(`${C.API}/appointments`, { method: 'POST', body: JSON.stringify(payload) });
            C.flash('Cita asignada exitosamente', 'success');
            modal('assignSlotModal')?.hide();
            el('assignSlotForm').reset();
            await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error asignando cita: ${err.message}`, 'danger');
        }
    }

    function openCancelAppointmentModal(appointmentId, slotInfo, studentName) {
        el('cancelAppointmentId').value = appointmentId;
        el('cancelSlotInfo').textContent = slotInfo;
        el('cancelStudentName').textContent = studentName;
        el('cancelReason').value = '';
        modal('confirmCancelAppointmentModal')?.show();
    }

    async function handleCancelAppointment(e) {
        e.preventDefault();
        const appointmentId = el('cancelAppointmentId').value;
        const reason = el('cancelReason').value.trim();
        try {
            await C.apiRequest(`${C.API}/appointments/${appointmentId}/cancel`, {
                method: 'POST',
                body: JSON.stringify({ reason: reason || 'Cancelada por coordinador' })
            });
            C.flash('Cita cancelada exitosamente', 'success');
            modal('confirmCancelAppointmentModal')?.hide();
            el('cancelAppointmentForm').reset();
            await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error cancelando cita: ${err.message}`, 'danger');
        }
    }

    function openDeleteSlotModal(slotId, slotInfo, studentName, isBooked) {
        el('deleteSlotId').value = slotId;
        el('deleteSlotInfo').textContent = slotInfo;
        const studentDiv = el('deleteSlotStudent');
        const warning = el('deleteSlotWarning');
        if (isBooked && studentName) {
            el('deleteSlotStudentName').textContent = studentName;
            studentDiv.classList.remove('d-none');
            warning.classList.remove('d-none');
        } else {
            studentDiv.classList.add('d-none');
            warning.classList.add('d-none');
        }
        modal('confirmDeleteSlotModal')?.show();
    }

    async function handleDeleteSlot() {
        const slotId = parseInt(el('deleteSlotId').value);
        if (!slotId) return;
        try {
            const { data } = await C.apiRequest(`${C.API}/events/slots/${slotId}`, { method: 'DELETE' });
            if (data.requires_force) {
                const confirmed = await siiapConfirm({
                    type: 'danger', title: 'Eliminar horario',
                    message: data.message + '\n\n¿Eliminar de todas formas?',
                    confirmLabel: 'Sí, eliminar'
                });
                if (!confirmed) return;
                await C.apiRequest(`${C.API}/events/slots/${slotId}?force=true`, { method: 'DELETE' });
            }
            C.flash('Horario eliminado exitosamente', 'success');
            modal('confirmDeleteSlotModal')?.hide();
            await loadWindows();
            await loadSlots();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error eliminando horario: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // CHANGE REQUESTS
    // =================================================================
    async function loadChangeRequests() {
        const tbody = document.querySelector('#changeRequestsTable tbody');
        if (!tbody) return;
        setRegionBusy(tbody, true, 'Cargando solicitudes de cambio…');
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">Cargando&hellip;</td></tr>';
        try {
            const { data } = await C.apiRequest(`${C.API}/appointments/change-requests/by-event/${eventId}`);
            currentChangeRequests = data.change_requests || [];
            renderChangeRequestsTable();
            const badge = el('changeRequestsBadge');
            if (badge) {
                if (currentChangeRequests.length > 0) {
                    badge.textContent = currentChangeRequests.length;
                    badge.classList.remove('d-none');
                } else {
                    badge.classList.add('d-none');
                }
            }
            loaded.changeRequests = true;
        } catch (err) {
            tbody.innerHTML = errorRow(6, 'No se pudieron cargar las solicitudes',
                'Revisa tu conexión y vuelve a intentarlo.', 'retryChangeRequestsBtn');
            document.getElementById('retryChangeRequestsBtn')?.addEventListener('click', loadChangeRequests);
            setRegionBusy(tbody, false, 'No se pudieron cargar las solicitudes de cambio.');
        }
    }

    function renderChangeRequestsTable() {
        const tbody = document.querySelector('#changeRequestsTable tbody');
        if (!tbody) return;
        if (currentChangeRequests.length === 0) {
            tbody.innerHTML = emptyRow(6, 'inbox', 'Sin solicitudes pendientes',
                'Cuando un estudiante pida cambiar su horario, aparecerá aquí.');
            setRegionBusy(tbody, false, 'No hay solicitudes de cambio pendientes.');
            return;
        }
        tbody.innerHTML = currentChangeRequests.map(req => {
            const start = new Date(req.current_slot.starts_at);
            const end = new Date(req.current_slot.ends_at);
            const slotStr = `${start.toLocaleDateString('es-MX')} ${start.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })} - ${end.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}`;
            const createdDate = new Date(req.created_at).toLocaleDateString('es-MX');
            const reasonAttr = (req.reason || '').replace(/"/g, '&quot;');
            const sugAttr = (req.suggestions || '').replace(/"/g, '&quot;');
            return `
                <tr data-req-id="${req.id}">
                    <th scope="row" class="fw-normal">
                        <span class="fw-semibold small d-block">${C.escapeHtml(req.student.full_name)}</span>
                        <span class="text-muted small">${C.escapeHtml(req.student.email)}</span>
                    </th>
                    <td><small>${slotStr}</small></td>
                    <td><small>${C.escapeHtml(req.reason || '—')}</small></td>
                    <td><small>${C.escapeHtml(req.suggestions || '—')}</small></td>
                    <td><small>${createdDate}</small></td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">
                            <button type="button" class="btn btn-outline-success btn-accept-change tap-target"
                                data-req-id="${req.id}"
                                data-student-name="${C.escapeHtml(req.student.full_name)}"
                                data-current-slot="${C.escapeHtml(slotStr)}"
                                data-reason="${reasonAttr}"
                                data-suggestions="${sugAttr}"
                                aria-label="Aprobar el cambio de horario de ${C.escapeHtml(req.student.full_name)}"
                                title="Aprobar cambio">
                                <i class="bi bi-check-lg" aria-hidden="true"></i>
                            </button>
                            <button type="button" class="btn btn-outline-danger btn-reject-change tap-target"
                                data-req-id="${req.id}"
                                aria-label="Rechazar el cambio de horario de ${C.escapeHtml(req.student.full_name)}"
                                title="Rechazar cambio">
                                <i class="bi bi-x-lg" aria-hidden="true"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false, `${currentChangeRequests.length} solicitudes de cambio cargadas.`);
    }

    async function openAcceptChangeModal(reqId, studentName, currentSlot, reason, suggestions) {
        el('acceptChangeReqId').value = reqId;
        el('acceptChangeStudentName').textContent = studentName;
        el('acceptChangeCurrentSlot').textContent = currentSlot;
        el('acceptChangeReason').textContent = reason || '—';
        el('acceptChangeSuggestions').textContent = suggestions || '—';

        if (!loaded.slots) await loadSlots();
        const slotSelect = el('acceptChangeNewSlot');
        const freeSlots = currentSlots.filter(s => s.status === 'free');
        if (freeSlots.length === 0) {
            slotSelect.innerHTML = '<option value="">No hay horarios libres disponibles</option>';
        } else {
            slotSelect.innerHTML = '<option value="">Seleccionar nuevo horario...</option>' +
                freeSlots.map(slot => {
                    const s = new Date(slot.starts_at);
                    const e = new Date(slot.ends_at);
                    const label = `${s.toLocaleDateString('es-MX')} ${s.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })} - ${e.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}`;
                    return `<option value="${slot.id}">${label}</option>`;
                }).join('');
        }
        modal('acceptChangeRequestModal')?.show();
    }

    async function acceptChangeRequest() {
        const reqId = el('acceptChangeReqId').value;
        const newSlotId = el('acceptChangeNewSlot').value;
        if (!newSlotId) {
            C.flash('Selecciona un nuevo horario', 'warning');
            return;
        }
        try {
            await C.apiRequest(`${C.API}/appointments/change-requests/${reqId}/decision`, {
                method: 'PUT',
                body: JSON.stringify({ status: 'accepted', new_slot_id: parseInt(newSlotId) })
            });
            C.flash('Cambio de horario aprobado', 'success');
            modal('acceptChangeRequestModal')?.hide();
            await loadSlots();
            await loadChangeRequests();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error aprobando cambio: ${err.message}`, 'danger');
        }
    }

    async function rejectChangeRequest(reqId) {
        const ok = await siiapConfirm({
            type: 'danger', title: 'Rechazar solicitud',
            message: '¿Rechazar esta solicitud de cambio?',
            confirmLabel: 'Sí, rechazar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/appointments/change-requests/${reqId}/decision`, {
                method: 'PUT', body: JSON.stringify({ status: 'rejected' })
            });
            C.flash('Solicitud rechazada', 'success');
            await loadChangeRequests();
        } catch (err) {
            C.flash(`Error rechazando solicitud: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // REGISTRATIONS / ATTENDANCE
    // =================================================================
    async function loadRegistrations() {
        setRegionBusy(document.querySelector('#registrationsTable tbody'), true, 'Cargando registros…');
        setRegionBusy(document.querySelector('#quickRegistrationsTable tbody'), true);
        try {
            const { data } = await C.apiRequest(`${C.API}/attendance/event/${eventId}/registrations`);
            currentRegistrations = data.registrations || [];
            // `can_manage` viene del propio endpoint (attendance_api.py) y es el
            // MISMO booleano con el que decidió si mandar `notes`. Sin él la
            // consola no distingue «esta persona se auto-registró» de «no
            // tienes derecho a saber cómo llegó», ni «puedes pasar lista» de
            // «cada clic acabará en 403».
            registrationsCanManage = data.can_manage !== false;
            updateRegistrationStats();
            renderAttendanceTable();
            renderQuickRegistrations();
            loaded.registrations = true;
            loaded.attendance = true;
        } catch (err) {
            C.flash(`Error cargando registros: ${err.message}`, 'danger');
            const tbody = document.querySelector('#registrationsTable tbody');
            if (tbody) {
                tbody.innerHTML = errorRow(6, 'No se pudieron cargar los registros',
                    'Revisa tu conexión y vuelve a intentarlo.', 'retryRegistrationsBtn');
                document.getElementById('retryRegistrationsBtn')?.addEventListener('click', loadRegistrations);
            }
            setRegionBusy(tbody, false, 'No se pudieron cargar los registros del evento.');
            setRegionBusy(document.querySelector('#quickRegistrationsTable tbody'), false);
        }
    }

    function updateRegistrationStats() {
        const total = currentRegistrations.length;
        const attended = currentRegistrations.filter(r => r.status === 'attended').length;
        const noShow = currentRegistrations.filter(r => r.status === 'no_show').length;
        const registered = currentRegistrations.filter(r => r.status === 'registered').length;
        if (el('statsTotal')) el('statsTotal').textContent = total;
        if (el('statsAttended')) el('statsAttended').textContent = attended;
        if (el('statsNoShow')) el('statsNoShow').textContent = noShow;
        if (el('statsRegistered')) el('statsRegistered').textContent = registered;
        if (el('attendeesBadge')) el('attendeesBadge').textContent = total;
    }

    function renderAttendanceTable() {
        const tbody = document.querySelector('#registrationsTable tbody');
        if (!tbody) return;
        let filtered = currentRegistrations;
        if (attendanceFilter) filtered = filtered.filter(r => r.status === attendanceFilter);
        if (filtered.length === 0) {
            tbody.innerHTML = emptyRow(6, 'people', 'Sin registros que mostrar',
                'Cambia el filtro de asistencia o invita a más personas al evento.');
            setRegionBusy(tbody, false, 'No hay registros para el filtro seleccionado.');
            return;
        }
        tbody.innerHTML = filtered.map(reg => {
            const registeredDate = C.formatDateTime(reg.registered_at);
            const attendedDate = reg.attended_at ? C.formatDateTime(reg.attended_at) : '—';
            const name = C.escapeHtml(reg.full_name);
            return `
                <tr data-registration-id="${reg.id}" data-user-id="${reg.user_id}">
                    <th scope="row" class="fw-semibold">${name}</th>
                    <td>${C.escapeHtml(reg.email)}</td>
                    <td>${registeredDate}</td>
                    <td class="text-center">${attendanceBadge(reg.status)}</td>
                    <td>${attendedDate}</td>
                    <td class="text-end">
                        ${!registrationsCanManage ? `
                            <span class="status-badge status-badge--sm"
                                  title="No administras este evento, así que no puedes pasar lista.">
                                <i class="bi bi-lock-fill" aria-hidden="true"></i>
                                <span>Solo lectura</span>
                            </span>
                            <span class="visually-hidden">
                                No administras este evento, así que no puedes cambiar su asistencia.
                            </span>
                        ` : reg.status === 'registered' ? `
                            <div class="btn-group btn-group-sm">
                                <button type="button" class="btn btn-outline-success btn-mark-attended tap-target"
                                    data-user-id="${reg.user_id}"
                                    aria-label="Marcar que ${name} sí asistió" title="Marcar asistencia">
                                    <i class="bi bi-check-lg" aria-hidden="true"></i>
                                </button>
                                <button type="button" class="btn btn-outline-warning btn-mark-no-show tap-target"
                                    data-user-id="${reg.user_id}"
                                    aria-label="Marcar que ${name} no asistió" title="Marcar como ausente">
                                    <i class="bi bi-x-lg" aria-hidden="true"></i>
                                </button>
                            </div>
                        ` : `
                            <button type="button" class="btn btn-outline-secondary btn-sm btn-undo-attendance tap-target"
                                data-user-id="${reg.user_id}"
                                aria-label="Restablecer el estado de asistencia de ${name}" title="Restablecer estado">
                                <i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i>
                            </button>
                        `}
                    </td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false, `${filtered.length} registros mostrados.`);
    }

    /* Estado de asistencia: chip del sistema (nunca solo color). */
    function attendanceBadge(status) {
        if (status === 'registered') return window.SIIAP.statusBadge('pending', 'Pendiente', 'sm');
        if (status === 'attended') return window.SIIAP.statusBadge('approved', 'Asistió', 'sm');
        if (status === 'no_show') return window.SIIAP.statusBadge('rejected', 'No asistió', 'sm');
        return window.SIIAP.statusBadge('pending', window.SIIAP.statusLabel(status), 'sm');
    }

    function renderQuickRegistrations() {
        const tbody = document.querySelector('#quickRegistrationsTable tbody');
        if (!tbody) return;
        if (currentRegistrations.length === 0) {
            tbody.innerHTML = emptyRow(4, 'person-plus', 'Todavía no hay personas registradas',
                'Usa «Invitar estudiantes» para convocar a los asistentes.');
            setRegionBusy(tbody, false);
            return;
        }
        tbody.innerHTML = currentRegistrations.map(reg => {
            // El origen se deduce de la nota del organizador, y esa nota NO
            // cruza entre programas: para quien no administra el evento llega
            // en null. Antes eso caía en el `else` y afirmaba «Auto-registro»
            // de todo el mundo, incluidos los invitados. Ausencia de permiso no
            // es ausencia de invitación: se dice que no está disponible.
            const originBadge = !registrationsCanManage
                ? `<span class="text-muted small">
                        <i class="bi bi-shield-lock me-1" aria-hidden="true"></i>No disponible
                   </span>`
                : (reg.notes && reg.notes.includes('invitación'))
                    ? '<span class="badge bg-info">Invitación</span>'
                    : '<span class="badge bg-secondary">Auto-registro</span>';
            return `
                <tr>
                    <th scope="row" class="fw-normal">${C.escapeHtml(reg.full_name)}</th>
                    <td>${C.escapeHtml(reg.email)}</td>
                    <td>${originBadge}</td>
                    <td>${attendanceBadge(reg.status)}</td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false);
    }

    async function markAttendance(userId, attended) {
        try {
            await C.apiRequest(`${C.API}/attendance/event/${eventId}/mark-attendance`, {
                method: 'POST', body: JSON.stringify({ user_id: userId, attended: attended })
            });
            C.flash(attended ? 'Asistencia registrada' : 'Marcado como ausente', 'success');
            await loadRegistrations();
        } catch (err) {
            C.flash(`Error al marcar asistencia: ${err.message}`, 'danger');
        }
    }

    async function undoAttendance(userId) {
        try {
            await C.apiRequest(`${C.API}/attendance/event/${eventId}/mark-attendance`, {
                method: 'POST', body: JSON.stringify({ user_id: userId, reset: true })
            });
            C.flash('Estado actualizado a registrado', 'success');
            await loadRegistrations();
        } catch (err) {
            C.flash(`Error al actualizar: ${err.message}`, 'danger');
        }
    }

    function exportAttendance() {
        if (currentRegistrations.length === 0) {
            C.flash('No hay registros para exportar', 'warning');
            return;
        }
        let csv = 'Estudiante,Email,Fecha Registro,Estado,Fecha Asistencia\n';
        currentRegistrations.forEach(reg => {
            const registeredDate = C.formatDateTime(reg.registered_at);
            const attendedDate = reg.attended_at ? C.formatDateTime(reg.attended_at) : '';
            csv += `"${reg.full_name}","${reg.email}","${registeredDate}","${reg.status}","${attendedDate}"\n`;
        });
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', `asistencia_${currentEvent?.title || 'evento'}_${Date.now()}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        C.flash('Archivo exportado exitosamente', 'success');
    }

    // =================================================================
    // INVITATIONS
    // =================================================================
    async function loadInvitations() {
        const tbody = document.querySelector('#invitationsTable tbody');
        setRegionBusy(tbody, true, 'Cargando invitaciones…');
        try {
            const { data } = await C.apiRequest(`${C.API}/invitations/event/${eventId}/list`);
            currentInvitations = data.invitations || [];
            renderInvitationsTable();
            if (el('invitationsBadge')) el('invitationsBadge').textContent = currentInvitations.length;
            loaded.invitations = true;
        } catch (err) {
            console.error('Error loading invitations:', err);
            if (tbody) {
                tbody.innerHTML = errorRow(5, 'No se pudieron cargar las invitaciones',
                    'Revisa tu conexión y vuelve a intentarlo.', 'retryInvitationsBtn');
                document.getElementById('retryInvitationsBtn')?.addEventListener('click', loadInvitations);
            }
            setRegionBusy(tbody, false, 'No se pudieron cargar las invitaciones.');
        }
    }

    /* Estado de la invitación: chip del sistema con icono, no solo color. */
    function invitationBadge(status) {
        if (status === 'pending') return window.SIIAP.statusBadge('pending', 'Pendiente', 'sm');
        if (status === 'accepted') return window.SIIAP.statusBadge('approved', 'Aceptada', 'sm');
        if (status === 'rejected') return window.SIIAP.statusBadge('rejected', 'Rechazada', 'sm');
        return window.SIIAP.statusBadge('pending', window.SIIAP.statusLabel(status), 'sm');
    }

    function renderInvitationsTable() {
        const tbody = document.querySelector('#invitationsTable tbody');
        if (!tbody) return;
        if (currentInvitations.length === 0) {
            tbody.innerHTML = emptyRow(5, 'envelope', 'Sin invitaciones enviadas',
                'Invita estudiantes desde la pestaña «Asistentes».');
            setRegionBusy(tbody, false, 'No hay invitaciones enviadas para este evento.');
            return;
        }
        tbody.innerHTML = currentInvitations.map(inv => {
            const invitedDate = C.formatDateTime(inv.invited_at);
            const name = C.escapeHtml(inv.full_name);
            return `
                <tr data-invitation-id="${inv.id}">
                    <th scope="row" class="fw-normal">${name}</th>
                    <td>${C.escapeHtml(inv.inviter_name || '')}</td>
                    <td>${invitedDate}</td>
                    <td>${invitationBadge(inv.status)}</td>
                    <td class="text-end">
                        ${inv.status === 'pending' ? `
                            <button type="button" class="btn btn-sm btn-outline-danger btn-cancel-invitation tap-target"
                                data-invitation-id="${inv.id}"
                                aria-label="Cancelar la invitación de ${name}" title="Cancelar invitación">
                                <i class="bi bi-x-lg" aria-hidden="true"></i>
                            </button>
                        ` : ''}
                    </td>
                </tr>`;
        }).join('');
        setRegionBusy(tbody, false, `${currentInvitations.length} invitaciones cargadas.`);
    }

    let invitePool = [];
    const inviteSelected = new Set();

    async function loadInvitePool(scope) {
        const grid = el('inviteUsersGrid');
        if (grid) {
            setRegionBusy(grid, true, 'Cargando usuarios que puedes invitar…');
            grid.innerHTML = '<p class="text-center text-muted py-4 w-100 mb-0">Cargando usuarios…</p>';
        }

        const registeredIds = currentRegistrations.map(r => r.user_id);
        const invitedIds = currentInvitations.filter(i => i.status === 'pending').map(i => i.user_id);
        const excludedIds = new Set([...registeredIds, ...invitedIds]);

        try {
            let users = [];
            if (scope === 'event_program' && currentEvent.program_id) {
                const { data } = await C.apiRequest(`${C.API}/coordinator/students?program_id=${currentEvent.program_id}`);
                users = (data.students || []).map(s => ({ ...s, role_name: 'student' }));
            } else if (scope === 'all_students') {
                const { data } = await C.apiRequest(`${C.API}/coordinator/students`);
                users = (data.students || []).map(s => ({ ...s, role_name: 'student' }));
            } else if (scope === 'all_users' || (scope === 'event_program' && !currentEvent.program_id)) {
                const { data } = await C.apiRequest(`${C.API}/admin/users?per_page=500&active=true`);
                const raw = data?.data?.users || data?.users || data?.items || [];
                users = raw.map(u => ({
                    id: u.id,
                    full_name: u.full_name || [u.first_name, u.last_name, u.mother_last_name].filter(Boolean).join(' '),
                    email: u.email,
                    avatar_url: u.avatar_url,
                    program_name: u.program_name || '',
                    program_id: u.program_id || null,
                    role_name: u.role_name || u.role || ''
                }));
            }
            invitePool = users.filter(u => !excludedIds.has(u.id));
            renderInvitePool();
        } catch (err) {
            C.flash(`Error cargando usuarios: ${err.message}`, 'danger');
            invitePool = [];
            renderInvitePool();
        }
    }

    function getFilteredInvitePool() {
        const search = (el('inviteSearch').value || '').toLowerCase().trim();
        const programFilter = el('inviteProgramFilter').value;
        return invitePool.filter(u => {
            if (programFilter && String(u.program_id) !== String(programFilter)) return false;
            if (search) {
                const blob = `${u.full_name || ''} ${u.email || ''} ${u.program_name || ''} ${u.role_name || ''}`.toLowerCase();
                if (!blob.includes(search)) return false;
            }
            return true;
        });
    }

    function roleBadge(roleName) {
        const m = {
            student: 'Estudiante',
            applicant: 'Aspirante',
            program_admin: 'Coordinador',
            postgraduate_admin: 'Posgrado',
            social_service: 'Servicio social'
        };
        const cls = roleName === 'student' ? 'bg-info text-dark'
            : roleName === 'applicant' ? 'bg-warning text-dark'
            : roleName === 'program_admin' || roleName === 'postgraduate_admin' ? 'bg-primary'
            : 'bg-secondary';
        const label = m[roleName] || roleName || 'Usuario';
        return `<span class="badge ${cls}">${C.escapeHtml(label)}</span>`;
    }

    function renderInvitePool() {
        const grid = el('inviteUsersGrid');
        if (!grid) return;
        const list = getFilteredInvitePool();
        el('inviteVisibleCount').textContent = list.length;

        if (invitePool.length === 0) {
            grid.innerHTML = `
                <div class="empty-state empty-state--compact w-100">
                    <div class="empty-state__icon"><i class="bi bi-person-x" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">No hay usuarios disponibles</h3>
                    <p class="empty-state__description">Todas las personas de este alcance ya están registradas o invitadas.</p>
                </div>`;
            updateInviteSelectedUI();
            setRegionBusy(grid, false, 'No hay usuarios disponibles para invitar.');
            return;
        }
        if (list.length === 0) {
            grid.innerHTML = `
                <div class="empty-state empty-state--compact w-100">
                    <div class="empty-state__icon"><i class="bi bi-search" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">Sin coincidencias</h3>
                    <p class="empty-state__description">Ajusta la búsqueda o el filtro de programa.</p>
                </div>`;
            updateInviteSelectedUI();
            setRegionBusy(grid, false, 'Ningún usuario coincide con la búsqueda.');
            return;
        }

        grid.innerHTML = list.map(u => {
            const isSelected = inviteSelected.has(u.id);
            const avatar = u.avatar_url || '/static/assets/images/default.jpg';
            return `
                <label class="invite-user-card ${isSelected ? 'selected' : ''}" data-user-id="${u.id}">
                    <input type="checkbox" class="form-check-input invite-check" data-user-id="${u.id}" ${isSelected ? 'checked' : ''}>
                    <img src="${avatar}" class="invite-avatar" alt="" loading="lazy">
                    <div class="invite-info">
                        <div class="invite-name">${C.escapeHtml(u.full_name || 'Sin nombre')}</div>
                        <div class="invite-email">${C.escapeHtml(u.email || '')}</div>
                        <div class="invite-meta">
                            ${roleBadge(u.role_name)}
                            ${u.program_name ? `<span class="text-muted small ms-1">${C.escapeHtml(u.program_name)}</span>` : ''}
                        </div>
                    </div>
                </label>`;
        }).join('');
        updateInviteSelectedUI();
        setRegionBusy(grid, false, `${list.length} usuarios disponibles para invitar.`);
    }

    function updateInviteSelectedUI() {
        const count = inviteSelected.size;
        el('inviteSelectedCount').textContent = count;
        el('inviteSendCount').textContent = count;
        el('btnSendInvitations').disabled = count === 0;
    }

    function refreshInviteProgramFilterOptions() {
        const filter = el('inviteProgramFilter');
        if (!filter) return;
        const seen = new Map();
        invitePool.forEach(u => {
            if (u.program_id && u.program_name && !seen.has(u.program_id)) {
                seen.set(u.program_id, u.program_name);
            }
        });
        const opts = ['<option value="">Cualquier programa</option>'];
        for (const [pid, pname] of seen.entries()) {
            opts.push(`<option value="${pid}">${C.escapeHtml(pname)}</option>`);
        }
        filter.innerHTML = opts.join('');
    }

    async function openInviteModal() {
        if (!currentEvent) return;
        el('inviteEventId').value = eventId;
        el('inviteSearch').value = '';
        el('inviteMessage').value = '';
        inviteSelected.clear();

        const allowRow = el('inviteAllowExternalRow');
        if (allowRow) {
            allowRow.classList.toggle('d-none', !currentEvent.program_id);
            el('inviteAllowExternal').checked = false;
        }

        const scopeSel = el('inviteScopeSelect');
        const defaultScope = currentEvent.program_id ? 'event_program' : 'all_users';
        if (scopeSel) scopeSel.value = defaultScope;

        modal('inviteStudentsModal')?.show();
        await loadInvitePool(defaultScope);
        refreshInviteProgramFilterOptions();
    }

    async function sendInvitations() {
        const message = el('inviteMessage').value;
        const ids = Array.from(inviteSelected);
        if (ids.length === 0) {
            C.flash('Selecciona al menos un usuario', 'warning');
            return;
        }
        const allowExternal = el('inviteAllowExternal')?.checked
            || el('inviteScopeSelect')?.value !== 'event_program';
        try {
            const { data } = await C.apiRequest(`${C.API}/invitations/event/${eventId}/invite`, {
                method: 'POST',
                body: JSON.stringify({ user_ids: ids, notes: message, allow_external: allowExternal })
            });
            C.flash(`${data.invited} invitaciones enviadas`, 'success');
            if (data.already_invited > 0) C.flash(`${data.already_invited} ya tenían invitación`, 'info');
            if (data.already_registered > 0) C.flash(`${data.already_registered} ya estaban registrados`, 'info');
            if (data.details?.wrong_program?.length > 0) {
                C.flash(`${data.details.wrong_program.length} no pertenecen al programa (marca la opción para incluirlos)`, 'warning');
            }
            modal('inviteStudentsModal')?.hide();
            inviteSelected.clear();
            await loadInvitations();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error enviando invitaciones: ${err.message}`, 'danger');
        }
    }

    async function cancelInvitation(invitationId) {
        const ok = await siiapConfirm({
            type: 'warning', title: 'Cancelar invitación',
            message: '¿Cancelar esta invitación?', confirmLabel: 'Sí, cancelar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/invitations/${invitationId}`, { method: 'DELETE' });
            C.flash('Invitación cancelada', 'success');
            await loadInvitations();
            await refreshEventOnly();
        } catch (err) {
            C.flash(`Error cancelando invitación: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // EDIT EVENT INFO
    // =================================================================
    let academicPeriods = [];

    async function loadAcademicPeriods() {
        try {
            const { data } = await C.apiRequest(`${C.API}/academic-periods`);
            academicPeriods = data.data || data.items || [];
        } catch (err) {
            console.error('Error loading academic periods:', err);
        }
    }

    function populateEditFormSelects() {
        const programSel = el('editProgram');
        if (programSel) {
            programSel.innerHTML = '<option value="">Todos los programas</option>' +
                programs.map(p => `<option value="${p.id}">${C.escapeHtml(p.name)}</option>`).join('');
        }
        const periodSel = el('editAcademicPeriod');
        if (periodSel) {
            periodSel.innerHTML = '<option value="">Sin periodo (atemporal)</option>' +
                academicPeriods.map(p => `<option value="${p.id}">${C.escapeHtml(p.code || p.name || `Periodo ${p.id}`)}</option>`).join('');
        }
    }

    function populateEditFormFromEvent(ev) {
        if (!ev) return;
        el('editEventId').value = ev.id;
        el('editTitle').value = ev.title || '';
        el('editType').value = ev.type || 'interview';
        el('editDescription').value = ev.description || '';
        el('editProgram').value = ev.program_id || '';
        el('editAcademicPeriod').value = ev.academic_period_id || '';
        el('editVisibility').value = ev.visibility || 'public';
        el('editStatus').value = ev.status || 'published';
        el('editLocation').value = ev.location || '';

        const isMulti = ev.capacity_type !== 'single';
        el('editDatesRow').classList.toggle('d-none', !isMulti);
        el('editDatesRow2').classList.toggle('d-none', !isMulti);
        el('editMaxCapacityRow').classList.toggle('d-none', ev.capacity_type !== 'multiple');

        if (ev.event_date) {
            try { el('editEventDate').value = new Date(ev.event_date).toISOString().slice(0, 16); }
            catch (e) { el('editEventDate').value = ''; }
        } else {
            el('editEventDate').value = '';
        }
        if (ev.event_end_date) {
            try { el('editEventEndDate').value = new Date(ev.event_end_date).toISOString().slice(0, 16); }
            catch (e) { el('editEventEndDate').value = ''; }
        } else {
            el('editEventEndDate').value = '';
        }
        el('editMaxCapacity').value = ev.max_capacity || '';

        el('editRequiresRegistration').checked = !!ev.requires_registration;
        el('editAllowsAttendance').checked = !!ev.allows_attendance_tracking;
        el('editVisibleToStudents').checked = !!ev.visible_to_students;
        el('editRemindersEnabled').checked = !!ev.reminders_enabled;
    }

    function openEditEventInfoModal() {
        if (!currentEvent) return;
        populateEditFormSelects();
        populateEditFormFromEvent(currentEvent);
        modal('editEventInfoModal')?.show();
    }

    async function handleEditEvent(e) {
        e.preventDefault();
        const evId = el('editEventId').value;
        const isSingle = currentEvent?.capacity_type === 'single';

        const payload = {
            title: el('editTitle').value.trim(),
            type: el('editType').value,
            description: el('editDescription').value,
            program_id: el('editProgram').value ? parseInt(el('editProgram').value) : null,
            academic_period_id: el('editAcademicPeriod').value ? parseInt(el('editAcademicPeriod').value) : null,
            location: el('editLocation').value,
            visibility: el('editVisibility').value,
            status: el('editStatus').value,
            requires_registration: el('editRequiresRegistration').checked,
            allows_attendance_tracking: el('editAllowsAttendance').checked,
            visible_to_students: el('editVisibleToStudents').checked,
            reminders_enabled: el('editRemindersEnabled').checked
        };

        if (!isSingle) {
            payload.event_date = el('editEventDate').value || null;
            payload.event_end_date = el('editEventEndDate').value || null;
            if (currentEvent.capacity_type === 'multiple') {
                const cap = parseInt(el('editMaxCapacity').value);
                if (!cap || cap < 1) {
                    C.flash('Capacidad máxima inválida', 'warning');
                    return;
                }
                payload.max_capacity = cap;
            }
        }

        if (!payload.title) {
            C.flash('El título es requerido', 'warning');
            return;
        }

        try {
            await C.apiRequest(`${C.API}/events/${evId}`, {
                method: 'PUT', body: JSON.stringify(payload)
            });
            C.flash('Evento actualizado exitosamente', 'success');
            modal('editEventInfoModal')?.hide();
            await loadEventDetails();
        } catch (err) {
            C.flash(`Error actualizando evento: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // CONTENT VISUAL (cover / gallery / hosts)
    // =================================================================
    let currentEventImages = { cover: null, gallery: [], eventId: null };
    let currentEventHosts = [];
    let contentLoaded = false;

    function buildEventImageUrl(imgEventId, image, kind) {
        if (!image || !image.path) return '';
        const filename = image.path.split('/').pop();
        return `/files/event/${imgEventId}/${kind}/${filename}`;
    }

    async function loadEventMedia() {
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}/images`);
            currentEventImages = { cover: data.cover || null, gallery: data.gallery || [], eventId: eventId };
            renderCoverPreview();
            renderGalleryGrid();
            renderSummaryCover();
        } catch (err) {
            console.error('Error loading event media:', err);
        }
    }

    async function loadEventHosts() {
        try {
            const { data } = await C.apiRequest(`${C.API}/events/${eventId}/hosts`);
            currentEventHosts = data.hosts || [];
            renderHostsList();
            renderSummaryHosts();
        } catch (err) {
            console.error('Error loading hosts:', err);
        }
    }

    function renderCoverPreview() {
        const preview = el('coverPreview');
        const delBtn = el('coverDeleteBtn');
        const coverIdEl = el('coverImageId');
        if (!preview) return;
        if (currentEventImages.cover) {
            const url = buildEventImageUrl(currentEventImages.eventId, currentEventImages.cover, 'cover');
            preview.style.backgroundImage = `url('${url}')`;
            preview.classList.remove('empty');
            preview.innerHTML = '';
            delBtn?.classList.remove('d-none');
            if (coverIdEl) coverIdEl.value = currentEventImages.cover.id;
        } else {
            preview.style.backgroundImage = '';
            preview.classList.add('empty');
            preview.innerHTML = `
                <div class="text-center">
                    <i class="bi bi-image icon-3xl d-block mb-2" aria-hidden="true"></i>
                    <span class="text-muted small">Sin portada</span>
                </div>`;
            delBtn?.classList.add('d-none');
            if (coverIdEl) coverIdEl.value = '';
        }
    }

    function renderSummaryCover() {
        const preview = el('summaryCoverPreview');
        if (!preview) return;
        if (currentEventImages.cover) {
            const url = buildEventImageUrl(currentEventImages.eventId, currentEventImages.cover, 'cover');
            preview.style.backgroundImage = `url('${url}')`;
            preview.classList.remove('empty');
            preview.innerHTML = '';
        } else {
            preview.style.backgroundImage = '';
            preview.classList.add('empty');
            preview.innerHTML = `
                <div class="text-center">
                    <i class="bi bi-image icon-3xl d-block mb-2" aria-hidden="true"></i>
                    <span class="text-muted small">Sin portada</span>
                </div>`;
        }
    }

    function renderGalleryGrid() {
        const grid = el('galleryGrid');
        const empty = el('galleryEmpty');
        if (!grid) return;
        const images = currentEventImages.gallery;
        if (images.length === 0) {
            grid.innerHTML = '';
            empty?.classList.remove('d-none');
            return;
        }
        empty?.classList.add('d-none');
        grid.innerHTML = images.map(img => {
            const url = buildEventImageUrl(currentEventImages.eventId, img, 'gallery');
            return `
                <div class="event-gallery-item">
                    <img src="${url}" alt="${C.escapeHtml(img.caption || '')}">
                    <button type="button" class="btn btn-danger btn-sm btn-remove btn-delete-gallery-image tap-target"
                        data-image-id="${img.id}"
                        aria-label="Eliminar la imagen ${C.escapeHtml(img.caption || 'de la galería')}"
                        title="Eliminar imagen">
                        <i class="bi bi-x-lg" aria-hidden="true"></i>
                    </button>
                </div>`;
        }).join('');
    }

    function renderHostsList() {
        const list = el('hostsList');
        const empty = el('hostsEmpty');
        if (!list) return;
        if (currentEventHosts.length === 0) {
            list.innerHTML = '';
            if (empty) {
                list.appendChild(empty);
                empty.classList.remove('d-none');
            }
            return;
        }
        empty?.classList.add('d-none');
        list.innerHTML = currentEventHosts.map((host, idx) => {
            const isInternal = !!host.user_id;
            const typeBadge = isInternal
                ? '<span class="badge bg-info text-dark">Interno</span>'
                : '<span class="badge bg-secondary">Externo</span>';
            const avatarSrc = host.avatar_url || host.photo_url || '/static/assets/images/default.jpg';
            const name = host.full_name || host.name || host.external_name || 'Sin nombre';
            const safeName = C.escapeHtml(name);
            return `
                <div class="event-host-card" data-host-idx="${idx}">
                    <img src="${avatarSrc}" alt="" class="avatar">
                    <div class="host-info">
                        <p class="fw-semibold mb-0">${safeName}</p>
                        <p class="text-muted small mb-0">${C.escapeHtml(host.role_label || '')} ${typeBadge}</p>
                    </div>
                    <div class="host-actions d-flex gap-1">
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-move-host-up tap-target"
                            data-idx="${idx}" aria-label="Subir a ${safeName} en el orden de ponentes"
                            title="Subir" ${idx === 0 ? 'disabled' : ''}>
                            <i class="bi bi-arrow-up" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-move-host-down tap-target"
                            data-idx="${idx}" aria-label="Bajar a ${safeName} en el orden de ponentes"
                            title="Bajar" ${idx === currentEventHosts.length - 1 ? 'disabled' : ''}>
                            <i class="bi bi-arrow-down" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-primary btn-edit-host tap-target"
                            data-idx="${idx}" aria-label="Editar al ponente ${safeName}" title="Editar">
                            <i class="bi bi-pencil" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-danger btn-remove-host tap-target"
                            data-idx="${idx}" aria-label="Quitar al ponente ${safeName}" title="Eliminar">
                            <i class="bi bi-trash" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>`;
        }).join('');
    }

    function renderSummaryHosts() {
        const node = el('summaryHosts');
        if (!node) return;
        if (currentEventHosts.length === 0) {
            node.innerHTML = `
                <div class="empty-state empty-state--compact">
                    <div class="empty-state__icon"><i class="bi bi-person-x" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">Sin ponentes registrados</h3>
                    <p class="empty-state__description">Agrégalos desde «Contenido visual».</p>
                </div>`;
            return;
        }
        node.innerHTML = currentEventHosts.map(host => {
            const isInternal = !!host.user_id;
            const avatarSrc = host.avatar_url || host.photo_url || '/static/assets/images/default.jpg';
            const name = host.full_name || host.name || host.external_name || 'Sin nombre';
            const typeBadge = isInternal
                ? '<span class="badge bg-info text-dark">Interno</span>'
                : '<span class="badge bg-secondary">Externo</span>';
            return `
                <div class="d-flex align-items-center gap-2 mb-2">
                    <img src="${avatarSrc}" class="rounded-circle avatar-xs" alt="">
                    <div class="flex-grow-1 min-width-0">
                        <p class="fw-semibold small text-truncate mb-0">${C.escapeHtml(name)}</p>
                        <p class="text-muted small text-truncate mb-0">${C.escapeHtml(host.role_label || '')} ${typeBadge}</p>
                    </div>
                </div>`;
        }).join('');
    }

    function setupCoverDropzone() {
        const dropzone = el('coverDropzone');
        const selectBtn = el('coverSelectBtn');
        const fileInput = el('coverFileInput');
        const delBtn = el('coverDeleteBtn');
        if (!dropzone) return;

        selectBtn?.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
        dropzone.addEventListener('click', () => fileInput.click());
        fileInput?.addEventListener('change', () => {
            if (fileInput.files.length > 0) uploadCover(fileInput.files[0]);
        });
        dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) uploadCover(file);
        });
        delBtn?.addEventListener('click', deleteCover);
    }

    async function uploadCover(file) {
        if (file.size > 5 * 1024 * 1024) {
            C.flash('La imagen supera el límite de 5 MB', 'warning');
            return;
        }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await fetch(`${C.API}/events/${eventId}/cover`, {
                method: 'POST',
                headers: { 'X-CSRFToken': C.getCsrfToken() },
                credentials: 'same-origin',
                body: fd
            });
            const data = await res.json();
            if (!res.ok) {
                C.flash(data.error?.message || 'Error al subir la portada', 'danger');
                return;
            }
            C.flash('Portada actualizada', 'success');
            await loadEventMedia();
        } catch (err) {
            C.flash(`Error al subir portada: ${err.message}`, 'danger');
        }
    }

    async function deleteCover() {
        const imageId = el('coverImageId')?.value;
        if (!imageId) return;
        const ok = await siiapConfirm({
            type: 'danger', title: 'Eliminar portada',
            message: '¿Eliminar la portada de este evento?', confirmLabel: 'Sí, eliminar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/images/${imageId}`, { method: 'DELETE' });
            C.flash('Portada eliminada', 'success');
            currentEventImages.cover = null;
            renderCoverPreview();
            renderSummaryCover();
        } catch (err) {
            C.flash(`Error al eliminar portada: ${err.message}`, 'danger');
        }
    }

    function setupGalleryDropzone() {
        const dropzone = el('galleryDropzone');
        const selectBtn = el('gallerySelectBtn');
        const fileInput = el('galleryFileInput');
        if (!dropzone) return;

        selectBtn?.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
        dropzone.addEventListener('click', () => fileInput.click());
        fileInput?.addEventListener('change', () => {
            if (fileInput.files.length > 0) uploadGalleryImages(Array.from(fileInput.files));
        });
        dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            uploadGalleryImages(Array.from(e.dataTransfer.files));
        });

        el('galleryGrid')?.addEventListener('click', async (e) => {
            const btn = e.target.closest('.btn-delete-gallery-image');
            if (!btn) return;
            const imageId = parseInt(btn.dataset.imageId);
            const ok = await siiapConfirm({
                type: 'danger', title: 'Eliminar imagen',
                message: '¿Eliminar esta imagen de la galería?', confirmLabel: 'Sí, eliminar'
            });
            if (!ok) return;
            try {
                await C.apiRequest(`${C.API}/events/images/${imageId}`, { method: 'DELETE' });
                C.flash('Imagen eliminada', 'success');
                await loadEventMedia();
            } catch (err) {
                C.flash(`Error al eliminar imagen: ${err.message}`, 'danger');
            }
        });
    }

    async function uploadGalleryImages(files) {
        let uploaded = 0;
        for (const file of files) {
            if (file.size > 5 * 1024 * 1024) {
                C.flash(`"${file.name}" supera el límite de 5 MB y fue omitida`, 'warning');
                continue;
            }
            const fd = new FormData();
            fd.append('file', file);
            try {
                const res = await fetch(`${C.API}/events/${eventId}/images`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': C.getCsrfToken() },
                    credentials: 'same-origin',
                    body: fd
                });
                if (res.ok) uploaded++;
                else {
                    const d = await res.json();
                    C.flash(d.error?.message || `Error subiendo "${file.name}"`, 'danger');
                }
            } catch (err) {
                C.flash(`Error al subir "${file.name}": ${err.message}`, 'danger');
            }
        }
        if (uploaded > 0) {
            C.flash(`${uploaded} imagen(es) subida(s)`, 'success');
            await loadEventMedia();
        }
    }

    function setupHostsPanel() {
        el('addHostBtn')?.addEventListener('click', () => openHostEditor(-1));
        el('saveHostsBtn')?.addEventListener('click', saveHosts);
        el('hostsList')?.addEventListener('click', (e) => {
            const up = e.target.closest('.btn-move-host-up');
            const down = e.target.closest('.btn-move-host-down');
            const edit = e.target.closest('.btn-edit-host');
            const del = e.target.closest('.btn-remove-host');
            if (up)   { const i = parseInt(up.dataset.idx);   moveHost(i, i - 1); }
            if (down) { const i = parseInt(down.dataset.idx); moveHost(i, i + 1); }
            if (edit) openHostEditor(parseInt(edit.dataset.idx));
            if (del)  removeHost(parseInt(del.dataset.idx));
        });
    }

    function moveHost(fromIdx, toIdx) {
        if (toIdx < 0 || toIdx >= currentEventHosts.length) return;
        const arr = [...currentEventHosts];
        [arr[fromIdx], arr[toIdx]] = [arr[toIdx], arr[fromIdx]];
        currentEventHosts = arr;
        renderHostsList();
    }

    function removeHost(idx) {
        currentEventHosts = currentEventHosts.filter((_, i) => i !== idx);
        renderHostsList();
    }

    function openHostEditor(idx) {
        const isNew = idx === -1;
        el('hostEditorModalTitle').textContent = isNew ? 'Agregar ponente' : 'Editar ponente';
        el('hostEditIndex').value = idx;

        el('hostTypeInternal').checked = true;
        el('hostUserSearch').value = '';
        el('hostUserId').value = '';
        el('hostSelectedUserCard').classList.add('d-none');
        el('hostExternalName').value = '';
        el('hostExternalBio').value = '';
        el('hostExternalPhoto').value = '';
        el('hostExternalPhotoPath').value = '';
        el('hostRoleLabel').value = '';
        el('hostInternalPanel').classList.remove('d-none');
        el('hostExternalPanel').classList.add('d-none');

        if (!isNew) {
            const host = currentEventHosts[idx];
            if (host.user_id) {
                el('hostTypeInternal').checked = true;
                el('hostUserId').value = host.user_id;
                showSelectedUser({
                    id: host.user_id,
                    full_name: host.full_name || host.name || '',
                    email: host.email || '',
                    role: host.role_display || host.role || '',
                    avatar_url: host.avatar_url || host.photo_url || ''
                });
            } else {
                el('hostTypeExternal').checked = true;
                el('hostInternalPanel').classList.add('d-none');
                el('hostExternalPanel').classList.remove('d-none');
                el('hostExternalName').value = host.external_name || host.name || '';
                el('hostExternalBio').value = host.external_bio || host.bio || '';
                el('hostExternalPhotoPath').value = host.external_photo_path || '';
            }
            el('hostRoleLabel').value = host.role_label || '';
        }
        modal('hostEditorModal')?.show();
    }

    /* Abre/cierra el desplegable de resultados manteniendo aria-expanded. */
    function toggleHostDropdown(open) {
        const dropdown = el('hostUserDropdown');
        const search = el('hostUserSearch');
        if (dropdown) dropdown.classList.toggle('d-none', !open);
        if (search) search.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function showSelectedUser(user) {
        el('hostUserId').value = user.id;
        el('hostSelectedName').textContent = user.full_name || '';
        el('hostSelectedEmail').textContent = user.email || '';
        el('hostSelectedRole').textContent = user.role || '';
        el('hostSelectedAvatar').src = user.avatar_url || '/static/assets/images/default.jpg';
        el('hostSelectedUserCard').classList.remove('d-none');
        el('hostUserSearch').value = '';
        toggleHostDropdown(false);
    }

    function setupHostEditor() {
        document.querySelectorAll('input[name="hostTypeRadio"]').forEach(radio => {
            radio.addEventListener('change', () => {
                const isInternal = el('hostTypeInternal').checked;
                el('hostInternalPanel').classList.toggle('d-none', !isInternal);
                el('hostExternalPanel').classList.toggle('d-none', isInternal);
            });
        });

        let searchTimer = null;
        el('hostUserSearch')?.addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            const q = e.target.value.trim();
            if (q.length < 2) {
                toggleHostDropdown(false);
                return;
            }
            searchTimer = setTimeout(() => searchUsers(q), 300);
        });

        el('hostClearUserBtn')?.addEventListener('click', () => {
            el('hostUserId').value = '';
            el('hostSelectedUserCard').classList.add('d-none');
        });

        el('hostEditorSaveBtn')?.addEventListener('click', saveHostFromModal);
    }

    async function searchUsers(query) {
        try {
            const { data } = await C.apiRequest(`${C.API}/admin/users?search=${encodeURIComponent(query)}&per_page=10&active=true`);
            const users = data?.data?.users || data?.users || data?.items || [];
            renderUserDropdown(users);
        } catch (err) {
            console.error('Error searching users:', err);
            renderUserDropdown([]);
        }
    }

    function renderUserDropdown(users) {
        const dropdown = el('hostUserDropdown');
        if (!dropdown) return;
        if (users.length === 0) {
            dropdown.innerHTML = '<p class="list-group-item text-muted small mb-0">Sin resultados para esa búsqueda</p>';
            toggleHostDropdown(true);
            window.SIIAP?.announce?.('Sin resultados para esa búsqueda de usuarios.');
            return;
        }
        const fullNameOf = (u) => u.full_name || u.name ||
            [u.first_name, u.last_name, u.mother_last_name].filter(Boolean).join(' ');
        dropdown.innerHTML = users.map(u => {
            const fullName = fullNameOf(u);
            const role = u.role_name || u.role || '';
            return `
                <button type="button" class="list-group-item list-group-item-action py-2" role="option"
                    data-user-id="${u.id}"
                    data-full-name="${C.escapeHtml(fullName)}"
                    data-email="${C.escapeHtml(u.email || '')}"
                    data-role="${C.escapeHtml(role)}"
                    data-avatar="${C.escapeHtml(u.avatar_url || '')}">
                    <div class="d-flex align-items-center gap-2">
                        <img src="${u.avatar_url || '/static/assets/images/default.jpg'}" alt=""
                            class="rounded-circle avatar-xs">
                        <div>
                            <div class="fw-semibold small">${C.escapeHtml(fullName)}</div>
                            <div class="text-muted small">${C.escapeHtml(u.email || '')} &bull; ${C.escapeHtml(role)}</div>
                        </div>
                    </div>
                </button>`;
        }).join('');
        dropdown.querySelectorAll('button[data-user-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                showSelectedUser({
                    // Identificador público del usuario (UUID): cadena.
                    id: btn.dataset.userId,
                    full_name: btn.dataset.fullName,
                    email: btn.dataset.email,
                    role: btn.dataset.role,
                    avatar_url: btn.dataset.avatar
                });
            });
        });
        toggleHostDropdown(true);
        window.SIIAP?.announce?.(`${users.length} usuarios encontrados.`);
    }

    async function saveHostFromModal() {
        const isInternal = el('hostTypeInternal').checked;
        const roleLabel = el('hostRoleLabel').value.trim();
        const editIdx = parseInt(el('hostEditIndex').value);
        if (!roleLabel) {
            C.flash('El rol o etiqueta es requerido', 'warning');
            return;
        }
        let hostObj = { role_label: roleLabel };
        if (isInternal) {
            // El ponente interno se nombra con el UUID público del usuario;
            // `replace_hosts` lo resuelve al id interno en el servidor.
            const userId = el('hostUserId').value.trim();
            if (!userId) {
                C.flash('Selecciona un usuario del sistema', 'warning');
                return;
            }
            hostObj.user_id = userId;
            hostObj.full_name = el('hostSelectedName').textContent;
            hostObj.email = el('hostSelectedEmail').textContent;
            hostObj.avatar_url = el('hostSelectedAvatar').src;
        } else {
            const externalName = el('hostExternalName').value.trim();
            const externalBio = el('hostExternalBio').value.trim();
            const photoFile = el('hostExternalPhoto').files[0];
            const existingPath = el('hostExternalPhotoPath').value;
            if (!externalName) {
                C.flash('El nombre del ponente externo es requerido', 'warning');
                return;
            }
            let photoPath = existingPath;
            if (photoFile) {
                try {
                    const fd = new FormData();
                    fd.append('file', photoFile);
                    const res = await fetch(`${C.API}/events/${eventId}/hosts/photo`, {
                        method: 'POST',
                        headers: { 'X-CSRFToken': C.getCsrfToken() },
                        credentials: 'same-origin',
                        body: fd
                    });
                    const d = await res.json();
                    if (res.ok && d.path) photoPath = d.path;
                } catch (err) {
                    C.flash('Error al subir foto del ponente', 'warning');
                }
            }
            hostObj.external_name = externalName;
            hostObj.external_bio = externalBio;
            hostObj.external_photo_path = photoPath || null;
        }
        if (editIdx === -1) currentEventHosts.push(hostObj);
        else currentEventHosts[editIdx] = hostObj;
        renderHostsList();
        modal('hostEditorModal')?.hide();
    }

    async function saveHosts() {
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/hosts`, {
                method: 'PUT', body: JSON.stringify({ hosts: currentEventHosts })
            });
            C.flash('Ponentes guardados exitosamente', 'success');
            renderSummaryHosts();
        } catch (err) {
            C.flash(`Error al guardar ponentes: ${err.message}`, 'danger');
        }
    }

    function openContentVisualModal() {
        modal('contentVisualModal')?.show();
        if (!contentLoaded) {
            loadEventMedia();
            loadEventHosts();
            contentLoaded = true;
        }
    }

    // =================================================================
    // HEADER ACTIONS (conclude/archive/unarchive/delete/toggle privacy)
    // =================================================================
    async function handleConcludeEvent() {
        const ok = await siiapConfirm({
            type: 'warning', title: 'Concluir evento',
            message: `¿Concluir "${currentEvent.title}"?\n\nSe marcará como completado, se cancelarán invitaciones pendientes y se eliminarán imágenes del servidor. No se puede revertir.`,
            confirmLabel: 'Sí, concluir'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/conclude`, { method: 'POST' });
            C.flash('Evento concluido exitosamente', 'success');
            await loadEventDetails();
        } catch (err) {
            C.flash(`Error al concluir: ${err.message}`, 'danger');
        }
    }

    async function handleArchiveEvent() {
        const ok = await siiapConfirm({
            type: 'warning', title: 'Archivar evento',
            message: `¿Archivar "${currentEvent.title}"?\n\nSe ocultará del listado público, se notificará a registrados, se cancelarán invitaciones pendientes y se eliminarán imágenes.`,
            confirmLabel: 'Sí, archivar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}/archive`, { method: 'POST' });
            C.flash('Evento archivado', 'success');
            await loadEventDetails();
        } catch (err) {
            C.flash(`Error al archivar: ${err.message}`, 'danger');
        }
    }

    async function handleUnarchiveEvent() {
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
            await loadEventDetails();
        } catch (err) {
            C.flash(`Error al reactivar: ${err.message}`, 'danger');
        }
    }

    function openDeleteEventModal() {
        if (!currentEvent) return;
        el('deleteEventId').value = currentEvent.id;
        el('deleteEventTitle').textContent = currentEvent.title;
        const warning = el('deleteEventWarning');
        if (currentEvent.slots_booked > 0) {
            el('deleteEventAppointmentsCount').textContent = currentEvent.slots_booked;
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
        modal('confirmDeleteEventModal')?.show();
    }

    async function handleDeleteEvent() {
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
            C.flash('Evento eliminado exitosamente', 'success');
            modal('confirmDeleteEventModal')?.hide();
            window.location.href = ctx.listUrl;
        } catch (err) {
            C.flash(`Error eliminando evento: ${err.message}`, 'danger');
        }
    }

    async function handleTogglePrivacy() {
        if (!currentEvent) return;
        const next = currentEvent.visibility === 'private' ? 'public' : 'private';
        const ok = await siiapConfirm({
            type: 'warning',
            title: next === 'private' ? 'Hacer privado' : 'Hacer público',
            message: next === 'private'
                ? 'Solo los invitados verán este evento. ¿Continuar?'
                : 'Todos los estudiantes verán este evento. ¿Continuar?',
            confirmLabel: 'Sí, cambiar'
        });
        if (!ok) return;
        try {
            await C.apiRequest(`${C.API}/events/${eventId}`, {
                method: 'PUT', body: JSON.stringify({ visibility: next })
            });
            C.flash('Visibilidad actualizada', 'success');
            await loadEventDetails();
        } catch (err) {
            C.flash(`Error actualizando visibilidad: ${err.message}`, 'danger');
        }
    }

    // =================================================================
    // EVENT LISTENERS
    // =================================================================
    function setupEventListeners() {
        // Forms
        el('addWindowForm')?.addEventListener('submit', handleAddWindow);
        el('assignSlotForm')?.addEventListener('submit', handleAssignSlot);
        el('cancelAppointmentForm')?.addEventListener('submit', handleCancelAppointment);

        // Header buttons
        el('btnTogglePrivacy')?.addEventListener('click', handleTogglePrivacy);
        el('btnConcludeEvent')?.addEventListener('click', handleConcludeEvent);
        el('btnArchiveEvent')?.addEventListener('click', handleArchiveEvent);
        el('btnUnarchiveEvent')?.addEventListener('click', handleUnarchiveEvent);
        el('btnDeleteEvent')?.addEventListener('click', openDeleteEventModal);

        el('btnEditInfo')?.addEventListener('click', openEditEventInfoModal);
        el('btnEditContent')?.addEventListener('click', openContentVisualModal);

        // Edit info form
        el('editEventForm')?.addEventListener('submit', handleEditEvent);
        el('btnRevertEdit')?.addEventListener('click', () => populateEditFormFromEvent(currentEvent));

        // Content visual setup (drop zones + hosts panel + editor)
        setupCoverDropzone();
        setupGalleryDropzone();
        setupHostsPanel();
        setupHostEditor();

        // Confirm buttons
        el('confirmDeleteEventBtn')?.addEventListener('click', handleDeleteEvent);
        el('confirmDeleteWindowBtn')?.addEventListener('click', handleDeleteWindow);
        el('confirmDeleteSlotBtn')?.addEventListener('click', handleDeleteSlot);
        el('confirmAcceptChangeBtn')?.addEventListener('click', acceptChangeRequest);

        // Windows tab actions
        el('btnGenerateSlotsFromWindows')?.addEventListener('click', handleGenerateAllSlots);
        document.querySelector('#windowsTable')?.addEventListener('click', (e) => {
            const gen = e.target.closest('.btn-generate-window-slots');
            const del = e.target.closest('.btn-delete-window');
            if (gen) handleGenerateWindowSlots(parseInt(gen.dataset.windowId));
            if (del) openDeleteWindowModal(
                parseInt(del.dataset.windowId), del.dataset.date, del.dataset.time,
                parseInt(del.dataset.slotsBooked || 0)
            );
        });

        // Slots tab actions
        el('btnGenerateSlots')?.addEventListener('click', handleGenerateAllSlots);
        el('btnRefreshSlots')?.addEventListener('click', () => { loadSlots(); loadWindows(); });
        document.querySelectorAll('input[name="slotFilter"]').forEach(input => {
            input.addEventListener('change', (e) => {
                slotsFilter = e.target.value;
                renderSlotsTable();
            });
        });
        document.querySelector('#slotsTable')?.addEventListener('click', (e) => {
            const assignBtn = e.target.closest('.btn-assign-slot');
            const cancelBtn = e.target.closest('.btn-cancel-appointment');
            const delBtn = e.target.closest('.btn-delete-slot');
            if (assignBtn) openAssignModal(assignBtn.dataset.slotId, assignBtn.dataset.slotInfo);
            if (cancelBtn) openCancelAppointmentModal(
                cancelBtn.dataset.appointmentId, cancelBtn.dataset.slotInfo, cancelBtn.dataset.studentName
            );
            if (delBtn) openDeleteSlotModal(
                parseInt(delBtn.dataset.slotId), delBtn.dataset.slotInfo,
                delBtn.dataset.studentName, delBtn.dataset.isBooked === 'true'
            );
        });

        // Assign modal interactions
        el('assignStudentSearch')?.addEventListener('input', renderAssignStudentsList);
        el('assignProgramFilter')?.addEventListener('change', renderAssignStudentsList);
        el('assignStudentsList')?.addEventListener('click', (e) => {
            const item = e.target.closest('.assign-student-item');
            if (!item) return;
            el('assignStudentId').value = item.dataset.studentId;
            el('btnConfirmAssign').disabled = false;
            document.querySelectorAll('.assign-student-item').forEach(node => {
                const isSelected = node === item;
                node.classList.toggle('active', isSelected);
                node.setAttribute('aria-pressed', String(isSelected));
            });
        });

        // Change requests
        document.querySelector('#changeRequestsTable')?.addEventListener('click', (e) => {
            const accept = e.target.closest('.btn-accept-change');
            const reject = e.target.closest('.btn-reject-change');
            if (accept) openAcceptChangeModal(
                accept.dataset.reqId, accept.dataset.studentName, accept.dataset.currentSlot,
                accept.dataset.reason, accept.dataset.suggestions
            );
            if (reject) rejectChangeRequest(reject.dataset.reqId);
        });

        // Attendance
        document.querySelectorAll('input[name="attendanceFilter"]').forEach(input => {
            input.addEventListener('change', (e) => {
                attendanceFilter = e.target.value;
                renderAttendanceTable();
            });
        });
        el('btnExportAttendance')?.addEventListener('click', exportAttendance);
        document.querySelector('#registrationsTable')?.addEventListener('click', (e) => {
            const att = e.target.closest('.btn-mark-attended');
            const ns = e.target.closest('.btn-mark-no-show');
            const undo = e.target.closest('.btn-undo-attendance');
            // `data-user-id` lleva el identificador público (UUID): se pasa
            // tal cual, sin parseInt, que daría NaN.
            if (att) markAttendance(att.dataset.userId, true);
            if (ns) markAttendance(ns.dataset.userId, false);
            if (undo) undoAttendance(undo.dataset.userId);
        });

        // Invitations
        document.querySelector('[data-bs-target="#inviteStudentsModal"]')?.addEventListener('click', openInviteModal);
        el('btnSendInvitations')?.addEventListener('click', sendInvitations);
        el('inviteSearch')?.addEventListener('input', renderInvitePool);
        el('inviteProgramFilter')?.addEventListener('change', renderInvitePool);
        el('inviteScopeSelect')?.addEventListener('change', async (e) => {
            inviteSelected.clear();
            await loadInvitePool(e.target.value);
            refreshInviteProgramFilterOptions();
        });
        el('inviteSelectAllBtn')?.addEventListener('click', () => {
            const list = getFilteredInvitePool();
            list.forEach(u => inviteSelected.add(u.id));
            renderInvitePool();
        });
        el('inviteClearSelectionBtn')?.addEventListener('click', () => {
            inviteSelected.clear();
            renderInvitePool();
        });
        el('inviteUsersGrid')?.addEventListener('change', (e) => {
            const cb = e.target.closest('.invite-check');
            if (!cb) return;
            // El conjunto se indexa por el identificador público (UUID en
            // cadena), igual que `inviteSelected.has(u.id)` al pintar. Con
            // parseInt todas las claves colapsaban en NaN.
            const id = cb.dataset.userId;
            if (cb.checked) inviteSelected.add(id);
            else inviteSelected.delete(id);
            const card = cb.closest('.invite-user-card');
            if (card) card.classList.toggle('selected', cb.checked);
            updateInviteSelectedUI();
        });
        document.querySelector('#invitationsTable')?.addEventListener('click', (e) => {
            const cancelBtn = e.target.closest('.btn-cancel-invitation');
            if (cancelBtn) cancelInvitation(parseInt(cancelBtn.dataset.invitationId));
        });

        // Tab lazy-load
        document.getElementById('tab-windows')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.windows) loadWindows();
        });
        document.getElementById('tab-slots')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.slots) loadSlots();
            if (!loaded.windows) loadWindows();
        });
        document.getElementById('tab-change-requests')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.changeRequests) loadChangeRequests();
        });
        document.getElementById('tab-attendees')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.registrations) loadRegistrations();
        });
        document.getElementById('tab-invitations')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.invitations) loadInvitations();
        });
        document.getElementById('tab-attendance')?.addEventListener('shown.bs.tab', () => {
            if (!loaded.attendance) loadRegistrations();
        });

        // Realtime via sockets
        let refreshTimer = null;
        const debouncedRefresh = () => {
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => {
                refreshEventOnly();
                if (loaded.slots) loadSlots();
                if (loaded.windows) loadWindows();
                if (loaded.changeRequests) loadChangeRequests();
                if (loaded.registrations) loadRegistrations();
                if (loaded.invitations) loadInvitations();
            }, 700);
        };
        window.addEventListener('siiap:appointment:changed', debouncedRefresh);
        window.addEventListener('siiap:appointment:change_requested', () => {
            if (loaded.changeRequests) loadChangeRequests();
            else refreshEventOnly();
        });
        window.addEventListener('siiap:event:changed', debouncedRefresh);
    }

    // =================================================================
    // INIT
    // =================================================================
    async function init() {
        if (!eventId) {
            C.flash('Evento inválido', 'error');
            return;
        }
        const ev = await loadEventDetails();
        if (!ev) return;
        await Promise.all([loadPrograms(), loadAcademicPeriods()]);
        if (ev.capacity_type === 'single') {
            await loadEligibleStudents(ev.program_id || null);
        }
        setupEventListeners();
        // Background load for summary (cover + hosts)
        loadEventMedia().catch(() => {});
        loadEventHosts().catch(() => {});
        contentLoaded = true;
    }

    document.addEventListener('DOMContentLoaded', init);
})();
