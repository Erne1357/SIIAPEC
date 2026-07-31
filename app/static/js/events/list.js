// app/static/js/events/list.js
(() => {
    'use strict';

    const API = '/api/v1';

    // ── State ──────────────────────────────────────────────────────────
    let allEvents        = [];
    let myRegistrations  = [];
    let myInvitations    = [];
    // hostsCache: eventId -> Array<host>
    const hostsCache     = new Map();
    // coverCache: eventId -> url string or null
    const coverCache     = new Map();

    // ── DOM ────────────────────────────────────────────────────────────
    const eventsContainer          = document.getElementById('eventsContainer');
    const heroStats                = document.getElementById('heroStats');
    const pendingSection           = document.getElementById('pendingInvitationsSection');
    const pendingList              = document.getElementById('pendingInvitationsList');
    const pendingCount             = document.getElementById('pendingInvitationsCount');
    const btnScrollInvitations     = document.getElementById('btnScrollInvitations');
    const filterType               = document.getElementById('filterType');
    const filterCapacity           = document.getElementById('filterCapacity');
    const filterDate               = document.getElementById('filterDate');
    const filterRegistered         = document.getElementById('filterRegistered');
    const searchEvents             = document.getElementById('searchEvents');
    const btnShowMyRegistrations   = document.getElementById('btnShowMyRegistrations');
    const myRegistrationsBody      = document.getElementById('myRegistrationsBody');
    const resultsCount             = document.getElementById('eventsResultsCount');

    // ── Helpers ────────────────────────────────────────────────────────

    function flash(level, message) {
        window.dispatchEvent(new CustomEvent('flash', { detail: { level, message } }));
    }

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Performs a fetch and returns parsed JSON.
     * Throws on non-ok responses.
     * @param {string} url
     * @param {RequestInit} options
     * @returns {Promise<object>}
     */
    async function apiRequest(url, options = {}) {
        const res = await fetch(url, {
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                ...(options.headers || {}),
            },
            ...options,
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) {
            const msg = data.error?.message || data.error || 'Error en la solicitud';
            throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
    }

    /**
     * Returns a Bootstrap Icons class name for the given event type.
     * @param {string} type
     * @returns {string}
     */
    function getEventIcon(type) {
        const map = {
            interview:    'bi-person-check',
            defense:      'bi-mortarboard',
            workshop:     'bi-tools',
            seminar:      'bi-book',
            conference:   'bi-broadcast',
            info_session: 'bi-info-circle',
        };
        return map[type] || 'bi-calendar-event';
    }

    /* Tipos de portada soportados por components/_event-cover.css. Un tipo
       desconocido cae en la variante 'default'. El JS NO conoce colores. */
    const COVER_TYPES = ['interview', 'defense', 'workshop', 'seminar', 'conference', 'info_session'];

    /**
     * Returns the .event-cover--* modifier for the given event type.
     * @param {string} type
     * @returns {string}
     */
    function eventCoverClass(type) {
        const key = COVER_TYPES.indexOf(type) === -1 ? 'default' : type;
        return 'event-cover--' + key.replace(/_/g, '-');
    }

    /**
     * Returns the Spanish label and the soft surface utility for an event type.
     * The chip is a CATEGORY, not a state: it never uses .status-badge--*.
     * @param {string} type
     * @returns {{ label: string, soft: string }}
     */
    function eventTypeMeta(type) {
        const map = {
            interview:    { label: 'Entrevista',          soft: 'bg-primary-soft' },
            defense:      { label: 'Defensa',             soft: 'bg-danger-soft'  },
            workshop:     { label: 'Taller',              soft: 'bg-success-soft' },
            seminar:      { label: 'Seminario',           soft: 'bg-info-soft'    },
            conference:   { label: 'Conferencia',         soft: 'bg-warning-soft' },
            info_session: { label: 'Sesión Informativa',  soft: 'bg-primary-soft' },
        };
        return map[type] || { label: type || 'Evento', soft: 'bg-primary-soft' };
    }

    /**
     * Builds a machine-readable <time> element for an event date.
     * Uses the shared SIIAP date helpers so the server and the client render
     * exactly the same string.
     * @param {string} iso
     * @param {string} [fallback]
     * @returns {string} HTML
     */
    function timeTag(iso, fallback) {
        const parsed = window.SIIAP?.parseDate ? SIIAP.parseDate(iso) : null;
        if (!parsed) return escapeHtml(fallback || 'Fecha por definir');
        const text = SIIAP.formatDateTime(iso, 'short');
        return `<time datetime="${escapeHtml(parsed.toISOString())}">${escapeHtml(text)}</time>`;
    }

    // ── Data loading ───────────────────────────────────────────────────

    /**
     * Main data fetch: loads events, registrations, and invitations in parallel,
     * then renders all sections.
     */
    async function loadEvents() {
        setEventsBusy(true);
        try {
            const [eventsData, regsData, invsData] = await Promise.all([
                apiRequest(`${API}/events/public`),
                apiRequest(`${API}/attendance/my-registrations`).catch(() => ({ data: { registrations: [] } })),
                apiRequest(`${API}/invitations/my-invitations`).catch(() => ({ data: { invitations: [] } })),
            ]);

            allEvents       = (eventsData.data?.items ?? eventsData.items) || [];
            myRegistrations = regsData.data?.registrations || regsData.registrations || [];
            myInvitations   = invsData.data?.invitations   || invsData.invitations   || [];

            renderHero(allEvents, myInvitations, myRegistrations);
            renderPendingInvitations(myInvitations);
            applyFiltersAndRender();

        } catch (err) {
            console.error('[list.js] Error loading events:', err);
            eventsContainer.innerHTML = `
                <div class="col-12">
                    <div class="empty-state empty-state--error">
                        <div class="empty-state__icon"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                        <h2 class="empty-state__title">No se pudieron cargar los eventos</h2>
                        <p class="empty-state__description">Revisa tu conexión e inténtalo de nuevo.</p>
                        <p class="empty-state__error-detail">${escapeHtml(err.message)}</p>
                        <div class="empty-state__actions">
                            <button type="button" class="btn btn-outline-primary" id="btnRetryEvents">Reintentar</button>
                        </div>
                    </div>
                </div>`;
            announceResults('No se pudieron cargar los eventos.');
        } finally {
            setEventsBusy(false);
        }
    }

    /** Marca el grid como ocupado mientras se recargan los datos. */
    function setEventsBusy(busy, message) {
        if (!eventsContainer) return;
        if (window.SIIAP?.setBusy) {
            SIIAP.setBusy(eventsContainer, busy, message ? { message } : undefined);
        } else {
            eventsContainer.setAttribute('aria-busy', busy ? 'true' : 'false');
        }
    }

    /** Escribe el resultado del filtrado en la región viva de la página. */
    function announceResults(message) {
        if (resultsCount) resultsCount.textContent = message;
    }

    // ── Hero ───────────────────────────────────────────────────────────

    /**
     * Renders the hero section with live counters.
     * @param {Array} events
     * @param {Array} invitations
     * @param {Array} registrations
     */
    function renderHero(events, invitations, registrations) {
        const now   = new Date();
        const in7d  = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
        const upcoming = events.filter(e => {
            if (!e.event_date) return false;
            const d = new Date(e.event_date);
            return d >= now && d <= in7d;
        });
        const pendingInvs = invitations.filter(i => i.status === 'pending');
        const registeredCount = registrations.length;

        heroStats.innerHTML = `
            <span class="hero-stat">
                <i class="bi bi-calendar-week" aria-hidden="true"></i>
                Próximos <strong>${upcoming.length}</strong>
            </span>
            <span class="hero-stat">
                <i class="bi bi-envelope" aria-hidden="true"></i>
                Invitaciones <strong>${pendingInvs.length}</strong>
            </span>
            <span class="hero-stat">
                <i class="bi bi-check2-circle" aria-hidden="true"></i>
                Inscrito en <strong>${registeredCount}</strong>
            </span>`;
    }

    // ── Invitaciones pendientes ────────────────────────────────────────

    /**
     * Renders the pinned pending invitations section.
     * Shows cards for pending and rejected invitations.
     * @param {Array} invitations
     */
    function renderPendingInvitations(invitations) {
        const visible = invitations.filter(i => i.status === 'pending' || i.status === 'rejected');

        if (!visible.length) {
            pendingSection.classList.add('d-none');
            btnScrollInvitations.classList.add('d-none');
            return;
        }

        const pendingOnly = invitations.filter(i => i.status === 'pending');
        pendingSection.classList.remove('d-none');
        pendingCount.textContent = pendingOnly.length;
        btnScrollInvitations.classList.remove('d-none');

        pendingList.innerHTML = visible.map(inv => {
            const isPending  = inv.status === 'pending';
            const isPrivate  = inv.visibility === 'private';
            const title      = inv.event_title || inv.title || 'Evento';

            const statusBadge = isPending
                ? SIIAP.statusBadge('pending', 'Pendiente')
                : SIIAP.statusBadge('rejected', 'Rechazaste');

            const privateBadge = isPrivate
                ? `<span class="badge bg-dark"><i class="bi bi-lock-fill me-1" aria-hidden="true"></i>Privado</span>`
                : '';

            const actionButtons = isPending
                ? `<button type="button" class="btn btn-success btn-sm btn-inv-accept" data-inv-id="${inv.id}">
                        <i class="bi bi-check-lg me-1" aria-hidden="true"></i>Aceptar
                   </button>
                   <button type="button" class="btn btn-outline-danger btn-sm btn-inv-reject" data-inv-id="${inv.id}">
                        <i class="bi bi-x-lg me-1" aria-hidden="true"></i>Rechazar
                   </button>`
                : `<button type="button" class="btn btn-outline-success btn-sm btn-inv-reconsider" data-inv-id="${inv.id}">
                        <i class="bi bi-arrow-counterclockwise me-1" aria-hidden="true"></i>Reconsiderar
                   </button>`;

            return `
                <div class="col-md-6 col-lg-4">
                    <div class="invitation-highlight-card h-100 d-flex flex-column gap-2">
                        <div class="d-flex align-items-center gap-2 flex-wrap">
                            ${statusBadge}${privateBadge}
                        </div>
                        <p class="fw-semibold mb-0">${escapeHtml(title)}</p>
                        ${inv.event_date ? `<p class="card-meta mb-0"><i class="bi bi-calendar3 me-1" aria-hidden="true"></i>${timeTag(inv.event_date)}</p>` : ''}
                        <div class="d-flex gap-2 flex-wrap mt-auto pt-1">
                            ${actionButtons}
                            <a href="/events/${inv.event_id}" class="btn btn-outline-secondary btn-sm">
                                <i class="bi bi-eye me-1" aria-hidden="true"></i>Ver detalle
                            </a>
                        </div>
                    </div>
                </div>`;
        }).join('');
    }

    // ── Filtros ────────────────────────────────────────────────────────

    /**
     * Applies all active filters to allEvents and re-renders the grid.
     */
    function applyFiltersAndRender() {
        const typeVal       = filterType.value;
        const capacityVal   = filterCapacity.value;
        const dateVal       = filterDate.value;
        const onlyRegistered = filterRegistered.checked;
        const query         = searchEvents.value.toLowerCase().trim();

        const now  = new Date();
        const in7d = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
        const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
        const endOfMonth   = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59, 59);

        const filtered = allEvents.filter(ev => {
            if (typeVal && ev.type !== typeVal) return false;

            if (capacityVal === 'available') {
                if (ev.capacity_type === 'multiple' && ev.current_registrations >= ev.max_capacity) return false;
            } else if (capacityVal === 'full') {
                if (!(ev.capacity_type === 'multiple' && ev.current_registrations >= ev.max_capacity)) return false;
            }

            if (dateVal === 'week') {
                if (!ev.event_date) return false;
                const d = new Date(ev.event_date);
                if (d < now || d > in7d) return false;
            } else if (dateVal === 'month') {
                if (!ev.event_date) return false;
                const d = new Date(ev.event_date);
                if (d < startOfMonth || d > endOfMonth) return false;
            }

            if (onlyRegistered) {
                const registered = myRegistrations.some(r => r.event_id === ev.id);
                if (!registered) return false;
            }

            if (query) {
                const haystack = `${ev.title || ''} ${ev.description || ''} ${ev.location || ''}`.toLowerCase();
                if (!haystack.includes(query)) return false;
            }

            return true;
        });

        const filtersActive = Boolean(typeVal || capacityVal || dateVal || onlyRegistered || query);
        renderEvents(filtered, filtersActive);
    }

    /** Restaura los filtros a su estado inicial y vuelve a pintar el grid. */
    function clearFilters() {
        filterType.value       = '';
        filterCapacity.value   = '';
        filterDate.value       = '';
        filterRegistered.checked = false;
        searchEvents.value     = '';
        applyFiltersAndRender();
        searchEvents.focus();
    }

    // ── Render principal ───────────────────────────────────────────────

    /**
     * Renders the main events grid.
     * @param {Array} events
     */
    function renderEvents(events, filtersActive) {
        if (!events.length) {
            const description = filtersActive
                ? 'Ningún evento coincide con los filtros actuales.'
                : 'Vuelve pronto para ver nuevas convocatorias.';
            const actions = filtersActive
                ? `<div class="empty-state__actions">
                       <button type="button" class="btn btn-outline-primary" id="btnClearFilters">Limpiar filtros</button>
                   </div>`
                : '';
            eventsContainer.innerHTML = `
                <div class="col-12">
                    <div class="empty-state">
                        <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
                        <h2 class="empty-state__title">No hay eventos disponibles</h2>
                        <p class="empty-state__description">${description}</p>
                        ${actions}
                    </div>
                </div>`;
            announceResults('Ningún evento coincide con los filtros.');
            return;
        }

        eventsContainer.innerHTML = events.map(ev => renderEventCard(ev)).join('');
        announceResults(events.length === 1
            ? '1 evento encontrado.'
            : `${events.length} eventos encontrados.`);

        // Lanzar lazy-load de covers e inicializar hosts visibles
        initLazyCovers();
        initVisibleHosts();
    }

    /**
     * Renders a single event card HTML string.
     * @param {object} ev - Event data from the API.
     * @returns {string}
     */
    function renderEventCard(ev) {
        const typeMeta    = eventTypeMeta(ev.type);
        const isRegistered = myRegistrations.some(r => r.event_id === ev.id);
        const myReg        = myRegistrations.find(r => r.event_id === ev.id);
        // Status de invitación viene del payload (cubre pending y accepted)
        const invStatus    = ev.my_invitation_status || null;
        const isPendingInvitation = invStatus === 'pending';
        const isAcceptedInvitation = invStatus === 'accepted';
        const hasInvitation = isPendingInvitation || isAcceptedInvitation;
        const isFull        = ev.capacity_type === 'multiple' && ev.current_registrations >= ev.max_capacity;

        // Portada — el color por tipo lo aporta components/_event-cover.css
        const icon          = getEventIcon(ev.type);
        const coverUrl      = buildCoverUrl(ev.id, ev.cover_path);
        const ribbonHtml = isPendingInvitation
            ? '<span class="cover-invitation-ribbon ribbon-pending"><i class="bi bi-envelope-paper-fill me-1" aria-hidden="true"></i>Te invitaron</span>'
            : isAcceptedInvitation
            ? '<span class="cover-invitation-ribbon ribbon-accepted"><i class="bi bi-envelope-check-fill me-1" aria-hidden="true"></i>Invitación aceptada</span>'
            : '';
        const coverHtml     = `
            <div class="event-card-cover event-cover ${eventCoverClass(ev.type)}"
                 data-event-id="${ev.id}" data-cover-url="${escapeHtml(coverUrl)}">
                <i class="bi ${icon} event-cover__icon" aria-hidden="true"></i>
                ${ribbonHtml}
            </div>`;

        // Chips de categoría (no son estados: nunca .status-badge--*)
        const programBadge = ev.program_name
            ? `<span class="badge bg-primary-soft">${escapeHtml(ev.program_name)}</span>`
            : `<span class="badge bg-secondary">Abierto a todos</span>`;
        const typeBadge   = `<span class="badge ${typeMeta.soft}">${escapeHtml(typeMeta.label)}</span>`;
        const privateBadge = ev.visibility === 'private'
            ? `<span class="badge bg-dark"><i class="bi bi-lock-fill me-1" aria-hidden="true"></i>Privado</span>`
            : '';
        const previewBadge = ev.is_preview
            ? `<span class="badge bg-info-soft"><i class="bi bi-eye me-1" aria-hidden="true"></i>Vista previa</span>`
            : '';
        const creatorBadge = ev.is_creator
            ? `<span class="badge bg-warning-soft"><i class="bi bi-person-fill-gear me-1" aria-hidden="true"></i>Tú lo creaste</span>`
            : '';

        // Meta (fecha, lugar)
        const dateLine = ev.event_date
            ? `<p class="card-meta mb-0"><i class="bi bi-calendar3" aria-hidden="true"></i> ${timeTag(ev.event_date)}</p>`
            : '';
        const locationLine = `<p class="card-meta mb-0"><i class="bi bi-geo-alt" aria-hidden="true"></i> ${escapeHtml(ev.location || 'Lugar por definir')}</p>`;

        // Chips de ponentes (placeholder — se llenará por lazy load)
        const hostsPlaceholder = `<div class="host-chips" id="host-chips-${ev.id}"></div>`;

        // Barra de cupo
        let capacityHtml = '';
        if (ev.capacity_type === 'multiple') {
            const pct       = ev.max_capacity > 0 ? Math.min(100, Math.round((ev.current_registrations / ev.max_capacity) * 100)) : 0;
            const fillClass = pct >= 90 ? 'fill-danger' : pct >= 60 ? 'fill-warn' : 'fill-ok';
            capacityHtml = `
                <div>
                    <div class="d-flex justify-content-between mb-1 capacity-legend">
                        <span>Inscritos</span>
                        <span class="fw-semibold">${ev.current_registrations} / ${ev.max_capacity}</span>
                    </div>
                    <div class="card-capacity-bar" role="img"
                         aria-label="${ev.current_registrations} de ${ev.max_capacity} lugares ocupados">
                        <div class="card-capacity-bar-fill ${fillClass}" style="--progress:${pct}"></div>
                    </div>
                </div>`;
        }

        // Footer: estado + acciones
        const statusBadge = isRegistered
            ? SIIAP.statusBadge('enrolled', 'Inscrito', 'sm')
            : isFull
            ? SIIAP.statusBadge('rejected', 'Cupo lleno', 'sm')
            : '';

        let actionBtn = '';
        if (isRegistered && myReg && myReg.status !== 'attended') {
            actionBtn = `<button type="button" class="btn btn-outline-danger btn-sm tap-target btn-unregister"
                                 data-event-id="${ev.id}"
                                 aria-label="Cancelar mi registro en ${escapeHtml(ev.title)}"
                                 title="Cancelar registro">
                            <i class="bi bi-person-dash" aria-hidden="true"></i>
                         </button>`;
        } else if (!isRegistered && !isFull) {
            const label = isPendingInvitation
                ? '<i class="bi bi-check2-circle me-1" aria-hidden="true"></i>Aceptar y registrarme'
                : '<i class="bi bi-person-plus me-1" aria-hidden="true"></i>Registrarme';
            const btnClass = isPendingInvitation ? 'btn btn-success' : 'btn btn-primary';
            actionBtn = `<button type="button" class="${btnClass} btn-sm btn-register" data-event-id="${ev.id}">
                            ${label}
                         </button>`;
        }

        // Botón rechazar visible solo cuando hay invitación pendiente y no está registrado
        const rejectBtn = isPendingInvitation && !isRegistered
            ? `<button type="button" class="btn btn-outline-danger btn-sm tap-target btn-inv-reject-card"
                       data-event-id="${ev.id}"
                       aria-label="Rechazar la invitación a ${escapeHtml(ev.title)}"
                       title="Rechazar invitación">
                    <i class="bi bi-x-lg" aria-hidden="true"></i>
               </button>`
            : '';

        const hasInv   = hasInvitation;
        const cardClass = hasInv
            ? `event-card has-invitation ${isPendingInvitation ? 'invitation-pending' : 'invitation-accepted'}`
            : 'event-card';

        return `
            <div class="col-sm-6 col-lg-4">
                <div class="${cardClass}" data-event-id="${ev.id}">
                    ${coverHtml}
                    <div class="card-body">
                        <div class="d-flex flex-wrap gap-1 mb-1">
                            ${typeBadge}${programBadge}${privateBadge}${previewBadge}${creatorBadge}
                        </div>
                        <h2 class="card-title">${escapeHtml(ev.title)}</h2>
                        ${dateLine}
                        ${locationLine}
                        ${hostsPlaceholder}
                        <p class="card-description">${escapeHtml(ev.description || 'Sin descripción')}</p>
                        ${capacityHtml}
                    </div>
                    <div class="card-footer">
                        <div class="footer-status">${statusBadge}</div>
                        <div class="footer-actions">
                            <a href="/events/${ev.id}" class="btn btn-outline-primary btn-sm">
                                <i class="bi bi-eye me-1" aria-hidden="true"></i>Ver detalle
                                <span class="visually-hidden">de ${escapeHtml(ev.title)}</span>
                            </a>
                            ${rejectBtn}
                            ${actionBtn}
                        </div>
                    </div>
                </div>
            </div>`;
    }

    // ── Lazy load: covers ──────────────────────────────────────────────

    /**
     * Sets up an IntersectionObserver to lazy-fetch event covers when cards
     * enter the viewport. Uses coverCache to avoid duplicate requests.
     */
    function initLazyCovers() {
        // Cover URL ya viene pre-computada en data-cover-url desde el render.
        // Observer lazy aplica la imagen al entrar en viewport (evita descargar
        // todas las portadas al mismo tiempo).
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                const el  = entry.target;
                const url = el.dataset.coverUrl;
                observer.unobserve(el);
                if (url) applyCover(el, url);
            });
        }, { rootMargin: '200px' });

        document.querySelectorAll('.event-card-cover[data-event-id]').forEach(el => {
            observer.observe(el);
        });
    }

    /**
     * Construye URL de portada servida por /files/event/<id>/cover/<filename>.
     * Si no hay cover_path retorna empty string para mantener fallback.
     */
    function buildCoverUrl(eventId, coverPath) {
        if (!coverPath) return '';
        const filename = coverPath.split('/').pop();
        return `/files/event/${eventId}/cover/${filename}`;
    }

    /**
     * Applies the real cover photo through the --event-cover-src custom
     * property. El color plano del tipo queda debajo como respaldo y el icono
     * se oculta solo via .event-cover--has-image.
     * @param {HTMLElement} el
     * @param {string} url
     */
    function applyCover(el, url) {
        el.style.setProperty('--event-cover-src', `url('${url}')`);
        el.classList.add('event-cover--has-image');
    }

    // ── Hosts: vienen pre-computados en hosts_summary del endpoint /public ──

    /**
     * Renderiza chips de ponentes para cada evento usando hosts_summary del payload.
     * Sin fetches adicionales (elimina N+1).
     */
    function initVisibleHosts() {
        const chipEls = document.querySelectorAll('[id^="host-chips-"]');
        chipEls.forEach(el => {
            const eventId = parseInt(el.id.replace('host-chips-', ''), 10);
            const ev = allEvents.find(e => e.id === eventId);
            if (!ev) return;
            const summary = ev.hosts_summary || [];
            renderHostChips(el, summary, ev.hosts_total || summary.length, ev.id);
        });
    }

    /**
     * Renders host avatar chips inside a container element.
     * Shows up to 3 avatars overlapped, with +N for the rest.
     * @param {HTMLElement} el - The .host-chips container.
     * @param {Array} hosts
     */
    function renderHostChips(el, hosts, totalHosts, eventId) {
        if (!hosts.length) return;

        const total = totalHosts || hosts.length;
        const shown = hosts.slice(0, 3);
        const extra = total - shown.length;
        const names = hosts.slice(0, 2).map(h => (h.name || '').split(' ')[0]).join(', ');
        const nameSuffix = total > 2 ? ` y ${total - 2} más` : '';

        const avatarsHtml = shown.map(h => {
            const initials = (h.name || '?').charAt(0).toUpperCase();
            const src = buildHostPhotoUrl(eventId, h);
            return src
                ? `<img class="h-avatar" src="${escapeHtml(src)}" alt="${escapeHtml(h.name || '')}" title="${escapeHtml(h.name || '')}" loading="lazy">`
                : `<span class="h-avatar h-avatar--initials" aria-hidden="true" title="${escapeHtml(h.name || '')}">${escapeHtml(initials)}</span>`;
        }).join('');

        const extraHtml = extra > 0 ? `<span class="host-extra" aria-hidden="true">+${extra}</span>` : '';

        el.innerHTML = `
            <div class="host-avatars">${avatarsHtml}${extraHtml}</div>
            <span class="host-names">con ${escapeHtml(names)}${escapeHtml(nameSuffix)}</span>`;
    }

    /**
     * Resuelve URL de foto de host. Backend ya devuelve `photo_url` listo.
     * Mantiene fallback a `avatar_url` o construcción manual si falta.
     */
    function buildHostPhotoUrl(eventId, host) {
        return host.photo_url || host.avatar_url || '';
    }

    // ── Mis Registros (modal) ──────────────────────────────────────────

    function renderMyRegistrationsModal() {
        if (!myRegistrations.length) {
            myRegistrationsBody.innerHTML = `
                <div class="empty-state empty-state--compact">
                    <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">Sin registros</h3>
                    <p class="empty-state__description">No tienes registros en ningún evento.</p>
                </div>`;
            return;
        }

        /* Estados reales del registro -> modificadores del componente compartido. */
        const REG_STATUS = {
            registered: { key: 'enrolled',  label: 'Registrado'   },
            attended:   { key: 'approved',  label: 'Asististe'    },
            no_show:    { key: 'deferred',  label: 'No asististe' },
            cancelled:  { key: 'rejected',  label: 'Cancelado'    },
        };

        myRegistrationsBody.innerHTML = `
            <div class="list-group list-group-flush">
                ${myRegistrations.map(r => {
                    const ev = allEvents.find(e => e.id === r.event_id) || {};
                    const s = REG_STATUS[r.status] || { key: 'pending', label: SIIAP.statusLabel(r.status) };
                    const title = ev.title || r.event_title || 'Evento';
                    return `
                        <div class="list-group-item reg-item d-flex justify-content-between align-items-center gap-2">
                            <div>
                                <p class="mb-0 fw-semibold">${escapeHtml(title)}</p>
                                ${ev.event_date ? `<p class="card-meta mb-0">${timeTag(ev.event_date)}</p>` : ''}
                            </div>
                            <div class="d-flex align-items-center gap-2">
                                ${SIIAP.statusBadge(s.key, s.label, 'sm')}
                                <a href="/events/${r.event_id}" class="btn btn-outline-primary btn-sm">
                                    Ver<span class="visually-hidden"> ${escapeHtml(title)}</span>
                                </a>
                            </div>
                        </div>`;
                }).join('')}
            </div>`;
    }

    // ── Handlers de invitaciones ───────────────────────────────────────

    /**
     * Sends an invitation response.
     * @param {number} invitationId
     * @param {boolean} accept
     */
    async function respondToInvitation(invitationId, accept) {
        try {
            await apiRequest(`${API}/invitations/${invitationId}/respond`, {
                method: 'POST',
                body: JSON.stringify({ accept }),
            });
            flash('success', accept ? 'Invitación aceptada.' : 'Invitación rechazada.');
            await loadEvents();
            refreshGlobalInvitationsBadge();
        } catch (err) {
            flash('danger', `Error al responder la invitación: ${err.message}`);
        }
    }

    /**
     * Refresca el badge global de invitaciones del sidebar tras cambios locales.
     */
    async function refreshGlobalInvitationsBadge() {
        try {
            const res = await fetch(`${API}/invitations/my-invitations`, { credentials: 'same-origin' });
            if (!res.ok) return;
            const data = await res.json();
            const count = Number(data.total || 0);
            window.dispatchEvent(new CustomEvent('siiap:invitations:count_changed', {
                detail: { count }
            }));
        } catch (err) {
            console.warn('[events/list] no se pudo refrescar badge invitaciones:', err);
        }
    }

    /**
     * Reconsidera una invitación previamente rechazada (vuelve a aceptar).
     * @param {number} invitationId
     */
    async function handleReconsider(invitationId) {
        const ok = await siiapConfirm({
            type: 'info',
            title: 'Reconsiderar invitación',
            message: '¿Deseas aceptar esta invitación que habías rechazado?',
            confirmLabel: 'Sí, aceptar',
        });
        if (!ok) return;
        await respondToInvitation(invitationId, true);
    }

    // ── Handlers de registro ───────────────────────────────────────────

    /**
     * Registers the current user to an event.
     * @param {number} eventId
     */
    async function registerToEvent(eventId) {
        try {
            await apiRequest(`${API}/attendance/event/${eventId}/register`, {
                method: 'POST',
                body: JSON.stringify({ notes: '' }),
            });
            flash('success', 'Te has registrado exitosamente al evento.');
            await loadEvents();
            // Si tenía invitación pendiente, ahora está accepted -> refresh badge
            refreshGlobalInvitationsBadge();
        } catch (err) {
            flash('danger', `Error al registrarse: ${err.message}`);
        }
    }

    /**
     * Unregisters the current user from an event, with confirmation.
     * @param {number} eventId
     */
    async function unregisterFromEvent(eventId) {
        const ok = await siiapConfirm({
            type: 'warning',
            title: 'Cancelar registro',
            message: '¿Estás seguro de que deseas cancelar tu registro en este evento?',
            confirmLabel: 'Sí, cancelar',
        });
        if (!ok) return;

        try {
            await apiRequest(`${API}/attendance/event/${eventId}/unregister`, {
                method: 'POST',
                body: JSON.stringify({}),
            });
            flash('success', 'Registro cancelado exitosamente.');
            await loadEvents();
        } catch (err) {
            flash('danger', `Error al cancelar: ${err.message}`);
        }
    }

    // ── Escape helper ──────────────────────────────────────────────────

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text ?? '');
        return div.innerHTML;
    }

    // ── Event delegation ───────────────────────────────────────────────

    eventsContainer?.addEventListener('click', e => {
        const btnClear = e.target.closest('#btnClearFilters');
        if (btnClear) { clearFilters(); return; }

        const btnRetryEvents = e.target.closest('#btnRetryEvents');
        if (btnRetryEvents) { loadEvents(); return; }

        const btnReg = e.target.closest('.btn-register');
        if (btnReg) { registerToEvent(parseInt(btnReg.dataset.eventId, 10)); return; }

        const btnUnreg = e.target.closest('.btn-unregister');
        if (btnUnreg) { unregisterFromEvent(parseInt(btnUnreg.dataset.eventId, 10)); return; }

        const btnRejectCard = e.target.closest('.btn-inv-reject-card');
        if (btnRejectCard) {
            const eventId = parseInt(btnRejectCard.dataset.eventId, 10);
            const ev = allEvents.find(x => x.id === eventId);
            const inv = myInvitations.find(i => i.event_id === eventId);
            const invId = inv?.id || inv?.invitation_id;
            if (invId) respondToInvitation(invId, false);
            return;
        }
    });

    pendingList?.addEventListener('click', e => {
        const btnAccept = e.target.closest('.btn-inv-accept');
        if (btnAccept) { respondToInvitation(parseInt(btnAccept.dataset.invId, 10), true); return; }

        const btnReject = e.target.closest('.btn-inv-reject');
        if (btnReject) { respondToInvitation(parseInt(btnReject.dataset.invId, 10), false); return; }

        const btnReconsider = e.target.closest('.btn-inv-reconsider');
        if (btnReconsider) { handleReconsider(parseInt(btnReconsider.dataset.invId, 10)); return; }
    });

    // Filtros
    filterType?.addEventListener('change', applyFiltersAndRender);
    filterCapacity?.addEventListener('change', applyFiltersAndRender);
    filterDate?.addEventListener('change', applyFiltersAndRender);
    filterRegistered?.addEventListener('change', applyFiltersAndRender);
    searchEvents?.addEventListener('input', applyFiltersAndRender);

    // Botón mis registros
    btnShowMyRegistrations?.addEventListener('click', () => {
        renderMyRegistrationsModal();
        const modal = new bootstrap.Modal(document.getElementById('myRegistrationsModal'));
        modal.show();
    });

    // ── Mark events seen ──────────────────────────────────────────────

    /**
     * Notifica al servidor que el usuario visitó la lista de eventos.
     * Fire-and-forget: actualiza User.last_events_seen_at y limpia el flag
     * de sessionStorage para que promo-toast.js empiece limpio la próxima sesión.
     */
    async function markEventsSeen() {
        try {
            await fetch(`${API}/events/mark-seen`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'Content-Type': 'application/json',
                },
            });
            sessionStorage.removeItem('eventsPromoShown');
        } catch (err) {
            console.warn('[events/list] mark-seen fallo:', err);
        }
    }

    // ── Tiempo real ────────────────────────────────────────────────────

    let reloadTimer = null;
    function debouncedReload() {
        if (reloadTimer) clearTimeout(reloadTimer);
        reloadTimer = setTimeout(() => loadEvents(), 700);
    }

    window.addEventListener('siiap:event:changed', e => {
        const detail = e.detail || {};
        if (detail.action === 'created') flash('info', 'Nuevo evento disponible.');
        else if (detail.action === 'deleted') flash('warning', 'Un evento fue eliminado.');
        debouncedReload();
    });

    window.addEventListener('siiap:appointment:changed', () => {
        debouncedReload();
    });

    // ── Init ───────────────────────────────────────────────────────────

    markEventsSeen(); // fire-and-forget: marca visita y limpia flag de promo-toast
    loadEvents();
})();
