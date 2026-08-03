// app/static/js/events/view.js
(() => {
    'use strict';

    const API = '/api/v1';
    const eventId = window.SIIAP_EVENT?.eventId;

    // ── State ────────────────────────────────────────────────
    let eventData       = null;
    let myReg           = null;
    let myAppt          = null;
    let windows         = [];
    let eventInvitation = null;

    // ── DOM references ───────────────────────────────────────
    const loadingState   = document.getElementById('loadingState');
    const errorState     = document.getElementById('errorState');
    const errorMessage   = document.getElementById('errorMessage');
    const eventContent   = document.getElementById('eventContent');
    const breadcrumbTitle = document.getElementById('breadcrumbTitle');
    const viewContainer  = document.getElementById('eventViewContainer');

    // ── Helpers ──────────────────────────────────────────────

    function flash(level, message) {
        showFlash(level, message);
    }

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function setBusy(busy, message) {
        if (!viewContainer) return;
        if (window.SIIAP?.setBusy) {
            SIIAP.setBusy(viewContainer, busy, message ? { message } : undefined);
        } else {
            viewContainer.setAttribute('aria-busy', busy ? 'true' : 'false');
        }
    }

    function showLoading() {
        loadingState.classList.remove('d-none');
        errorState.classList.add('d-none');
        eventContent.classList.add('d-none');
        setBusy(true);
    }

    function showError(msg) {
        loadingState.classList.add('d-none');
        errorMessage.textContent = msg || 'Error inesperado.';
        errorState.classList.remove('d-none');
        eventContent.classList.add('d-none');
        setBusy(false);
    }

    function showContent() {
        loadingState.classList.add('d-none');
        errorState.classList.add('d-none');
        eventContent.classList.remove('d-none');
        setBusy(false);
    }

    /**
     * Fecha completa en español ('31 de julio de 2026 a las 14:30').
     * Delega en el helper compartido para no divergir del filtro de Jinja.
     * @param {string} iso
     * @param {'long'|'short'|'numeric'} [style]
     * @returns {string}
     */
    function formatDate(iso, style) {
        return SIIAP.formatDateTime(iso, style || 'long');
    }

    /**
     * Hora en formato 24 h.
     * @param {string} iso
     * @returns {string}
     */
    function formatTime(iso) {
        return SIIAP.formatTime(iso, '—');
    }

    /**
     * Devuelve un <time datetime> legible por máquinas.
     * @param {string} iso
     * @param {'long'|'short'|'numeric'} [style]
     * @returns {string} HTML
     */
    function timeTag(iso, style) {
        const parsed = SIIAP.parseDate(iso);
        if (!parsed) return '—';
        return `<time datetime="${escapeHtml(parsed.toISOString())}">${escapeHtml(formatDate(iso, style))}</time>`;
    }

    /* Tipos con portada propia en components/_event-cover.css. */
    const COVER_TYPES = ['interview', 'defense', 'workshop', 'seminar', 'conference', 'info_session'];

    /**
     * Modificador .event-cover--* para el tipo de evento. El JS no conoce colores.
     * @param {string} type
     * @returns {string}
     */
    function eventCoverClass(type) {
        const key = COVER_TYPES.indexOf(type) === -1 ? 'default' : type;
        return 'event-cover--' + key.replace(/_/g, '-');
    }

    /**
     * Etiqueta en español y superficie suave del chip de tipo de evento.
     * El tipo es una CATEGORÍA, no un estado: nunca usa .status-badge--*.
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
     * Estado de publicación del evento -> modificador del componente compartido.
     * @param {string} status
     * @returns {{ label: string, key: string }}
     */
    function statusMeta(status) {
        const map = {
            draft:      { label: 'Borrador',   key: 'pending'      },
            published:  { label: 'Publicado',  key: 'approved'     },
            cancelled:  { label: 'Cancelado',  key: 'rejected'     },
            completed:  { label: 'Completado', key: 'enrolled'     },
        };
        return map[status] || { label: SIIAP.statusLabel(status), key: 'pending' };
    }

    // ── API request helper ───────────────────────────────────

    /**
     * Performs a fetch request and returns parsed JSON data.
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

    // ── Data loading ─────────────────────────────────────────

    /**
     * Fetches event detail from the public API and triggers render.
     */
    /* Cache de /images: hero y galería comparten la MISMA respuesta, así que
       el detalle solo hace una petición por carga en vez de dos. */
    let imagesPromise = null;

    /**
     * Fetches (once per load) the cover + gallery payload.
     * @param {number} evId
     * @returns {Promise<{cover: object|null, gallery: Array}>}
     */
    function fetchEventImages(evId) {
        if (!imagesPromise) {
            imagesPromise = apiRequest(`${API}/events/${evId}/images`)
                .then(data => ({
                    cover:   data.cover   || data.data?.cover   || null,
                    gallery: data.gallery || data.data?.gallery || [],
                }))
                .catch(() => ({ cover: null, gallery: [] }));
        }
        return imagesPromise;
    }

    async function loadEventDetail() {
        imagesPromise = null;
        setBusy(true, 'Cargando información del evento…');
        try {
            const data = await apiRequest(`${API}/events/public/${eventId}`);
            eventData       = data.event;
            myReg           = data.my_registration || null;
            myAppt          = data.my_appointment  || null;
            windows         = data.windows         || [];
            eventInvitation = data.my_invitation   || null;
            // Anotar eventData con status de invitación para render del hero banner
            if (eventData) {
                eventData.my_invitation_status = eventInvitation?.status || null;
            }
            renderAll();
            showContent();
        } catch (err) {
            console.error('[view.js] Error loading event:', err);
            showError(err.message);
        }
    }

    // ── Render: hero (full-bleed con imagen) ─────────────────

    /**
     * Renders the full-bleed hero section with cover image or gradient fallback,
     * badges, title, meta, and invitation status banner.
     */
    function renderHero() {
        const typeMeta = eventTypeMeta(eventData.type);
        const statMeta = statusMeta(eventData.status);
        const heroEl   = document.getElementById('eventHeroEl');

        // Background: cover image or gradient fallback
        if (heroEl) {
            // Try to fetch cover image
            loadHeroCover(heroEl, eventData);
        }

        // Chips: categoría (tipo, programa) + estado real de publicación
        const heroBadges = document.getElementById('heroBadges');
        const programBadge = eventData.program_name
            ? `<span class="badge bg-primary-soft">${escapeHtml(eventData.program_name)}</span>`
            : `<span class="badge bg-secondary">Abierto a todos</span>`;
        const privateBadge = eventData.visibility === 'private'
            ? `<span class="badge bg-dark"><i class="bi bi-lock-fill me-1" aria-hidden="true"></i>Privado</span>`
            : '';
        heroBadges.innerHTML = `
            ${programBadge}
            <span class="badge ${typeMeta.soft}">${escapeHtml(typeMeta.label)}</span>
            ${SIIAP.statusBadge(statMeta.key, statMeta.label, 'sm')}
            ${privateBadge}
        `;

        // Title + breadcrumb (ya vienen renderizados por el servidor)
        document.getElementById('heroTitle').textContent = eventData.title;
        breadcrumbTitle.textContent = eventData.title;
        document.title = `${eventData.title} - SIIAP`;

        // Meta row
        const heroMeta = document.getElementById('heroMeta');
        const datePart = eventData.event_date
            ? `<span><i class="bi bi-calendar3 me-1" aria-hidden="true"></i>${timeTag(eventData.event_date)}</span>`
            : '';
        const endPart = eventData.event_end_date
            ? `<span><i class="bi bi-calendar-check me-1" aria-hidden="true"></i>${timeTag(eventData.event_end_date)}</span>`
            : '';
        const locationPart = `<span><i class="bi bi-geo-alt me-1" aria-hidden="true"></i>${escapeHtml(eventData.location || 'Por definir')}</span>`;
        heroMeta.innerHTML = [datePart, endPart, locationPart].filter(Boolean).join('');

        // Invitation banner
        renderHeroBanner();
    }

    /**
     * Applies the type cover class and, if there is a real photo, the
     * --event-cover-src custom property. El JS nunca inyecta colores.
     * @param {HTMLElement} heroEl
     * @param {object} ev
     */
    async function loadHeroCover(heroEl, ev) {
        COVER_TYPES.concat('default').forEach(t => {
            heroEl.classList.remove('event-cover--' + t.replace(/_/g, '-'));
        });
        heroEl.classList.add('event-cover', eventCoverClass(ev.type));

        const { cover } = await fetchEventImages(ev.id);
        if (cover?.path) {
            const filename = cover.path.split('/').pop();
            const url = `/files/event/${ev.id}/cover/${filename}`;
            heroEl.style.setProperty('--event-cover-src', `url('${url}')`);
            heroEl.classList.add('event-cover--has-image');
        }
    }

    /**
     * Renders the invitation/RSVP status banner on top of the hero.
     */
    function renderHeroBanner() {
        const bannerEl = document.getElementById('heroBannerInvitation');
        if (!bannerEl) return;

        const invStatus = eventData.my_invitation_status;

        if (eventData.visibility === 'private' && invStatus === 'pending') {
            bannerEl.innerHTML = `
                <div class="hero-invitation-banner">
                    <i class="bi bi-lock-fill" aria-hidden="true"></i>
                    Evento privado — te invitaron
                </div>`;
        } else if (invStatus === 'accepted') {
            bannerEl.innerHTML = `
                <div class="hero-invitation-banner accepted">
                    <i class="bi bi-check-circle-fill" aria-hidden="true"></i>
                    Has aceptado la invitación
                </div>`;
        } else if (invStatus === 'rejected') {
            bannerEl.innerHTML = `
                <div class="hero-invitation-banner rejected">
                    <i class="bi bi-x-circle-fill" aria-hidden="true"></i>
                    Rechazaste la invitación
                    <button type="button" class="btn btn-sm btn-outline-light ms-2" id="btnReconsider">
                        Reconsiderar
                    </button>
                </div>`;
            document.getElementById('btnReconsider')?.addEventListener('click', reconsiderInvitation);
        }
    }

    // ── Render: ponentes ─────────────────────────────────────

    /**
     * Fetches and renders the hosts section.
     * @param {number} evId
     */
    async function loadHosts(evId) {
        try {
            const data  = await apiRequest(`${API}/events/${evId}/hosts`);
            const hosts = data.hosts || data.data?.hosts || [];
            if (!hosts.length) return;

            const card = document.getElementById('hostsCard');
            const strip = document.getElementById('hostsStrip');
            if (!card || !strip) return;

            strip.innerHTML = hosts.map(h => {
                const initials = (h.name || '?').charAt(0).toUpperCase();
                const photoUrl = resolveHostPhotoUrl(evId, h);
                const photoHtml = photoUrl
                    ? `<img class="host-photo" src="${escapeHtml(photoUrl)}" alt="" loading="lazy">`
                    : `<div class="host-photo-placeholder" aria-hidden="true">${escapeHtml(initials)}</div>`;
                const hostPayload = { ...h, photo_url: photoUrl };
                const name = h.name || 'Ponente';
                return `
                    <button type="button" class="host-card"
                            aria-label="Ver la semblanza de ${escapeHtml(name)}"
                            data-host-json="${escapeHtml(JSON.stringify(hostPayload))}">
                        ${photoHtml}
                        <span class="host-name d-block">${escapeHtml(name)}</span>
                        <span class="host-role d-block">${escapeHtml(h.role_label || '')}</span>
                    </button>`;
            }).join('');

            card.classList.remove('d-none');

            // Click → open bio modal
            strip.addEventListener('click', e => {
                const card = e.target.closest('.host-card');
                if (!card) return;
                try {
                    const host = JSON.parse(card.dataset.hostJson || '{}');
                    openHostModal(host);
                } catch { /* ignore */ }
            });
        } catch (err) {
            console.warn('[view.js] Could not load hosts:', err.message);
        }
    }

    /**
     * Opens the host bio modal.
     * @param {object} host
     */
    function openHostModal(host) {
        const nameEl = document.getElementById('hostBioName');
        const roleEl = document.getElementById('hostBioRole');
        const bioEl  = document.getElementById('hostBioBio');
        const photoWrap = document.getElementById('hostBioPhotoWrap');

        if (nameEl) nameEl.textContent = host.name || 'Ponente';
        if (roleEl) roleEl.textContent = host.role_label || '';
        if (bioEl)  bioEl.textContent  = host.bio || 'Sin semblanza disponible.';

        if (photoWrap) {
            const src = host.photo_url || host.avatar_url || '';
            photoWrap.innerHTML = src
                ? `<img class="host-bio-photo" src="${escapeHtml(src)}" alt="${escapeHtml(host.name || '')}">`
                : `<div class="host-bio-photo-placeholder">${escapeHtml((host.name || '?').charAt(0).toUpperCase())}</div>`;
        }

        const modal = new bootstrap.Modal(document.getElementById('hostBioModal'));
        modal.show();
    }

    // ── Render: galería ──────────────────────────────────────

    /**
     * Fetches and renders the public gallery section.
     * @param {number} evId
     */
    async function loadGallery(evId) {
        try {
            const { gallery } = await fetchEventImages(evId);
            if (!gallery.length) return;

            const card = document.getElementById('galleryCard');
            const grid = document.getElementById('publicGallery');
            if (!card || !grid) return;

            grid.innerHTML = gallery.map((img, index) => {
                const filename = (img.path || '').split('/').pop();
                const url = `/files/event/${evId}/gallery/${filename}`;
                const caption = img.caption || `Imagen ${index + 1} del evento`;
                return `
                <button type="button" class="event-public-gallery-item"
                        data-img-url="${escapeHtml(url)}"
                        data-caption="${escapeHtml(caption)}"
                        aria-label="Ampliar: ${escapeHtml(caption)}">
                    <img src="${escapeHtml(url)}" alt="${escapeHtml(caption)}" loading="lazy">
                </button>`;
            }).join('');

            card.classList.remove('d-none');

            grid.addEventListener('click', e => {
                const item = e.target.closest('.event-public-gallery-item');
                if (!item) return;
                openImageLightbox(item.dataset.imgUrl, item.dataset.caption);
            });
        } catch (err) {
            console.warn('[view.js] Could not load gallery:', err.message);
        }
    }

    /**
     * Opens the native <dialog> lightbox with the given image URL.
     * @param {string} url
     * @param {string} [caption] Texto alternativo real de la imagen.
     */
    function openImageLightbox(url, caption) {
        const dialog = document.getElementById('eventLightbox');
        if (!dialog) return;

        // El <img> se crea aquí, no en la plantilla: así nunca existe en el DOM
        // sin src. Se reutiliza en aperturas sucesivas.
        let img = document.getElementById('lightboxImg');
        if (!img) {
            img = document.createElement('img');
            img.id = 'lightboxImg';
            dialog.appendChild(img);
        }
        img.src = url;
        img.alt = caption || 'Imagen del evento';
        dialog.showModal();
    }

    // ── Render: compartir URL ────────────────────────────────

    /**
     * Populates the share URL input with the current page URL.
     */
    function renderShareUrl() {
        const input = document.getElementById('shareUrlInput');
        if (input) input.value = window.location.href;
    }

    // ── Escape helper ────────────────────────────────────────

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text ?? '');
        return div.innerHTML;
    }

    // ── Invitación: reconsiderar ─────────────────────────────

    /**
     * Reconsidera la invitación rechazada (vuelve a aceptar).
     */
    async function reconsiderInvitation() {
        // Backend retorna payload["my_invitation"] = { id, status, ... } en /public/<id>.
        // state.eventData guarda payload.event (sin my_invitation), así que debemos
        // buscarlo en el payload completo. Lo guardamos en eventInvitation al cargar.
        const inv = eventInvitation?.id;
        if (!inv) {
            flash('warning', 'No se encontró la invitación para reconsiderar.');
            return;
        }
        try {
            await apiRequest(`${API}/invitations/${inv}/respond`, {
                method: 'POST',
                body: JSON.stringify({ accept: true }),
            });
            flash('success', 'Has aceptado la invitación.');
            await loadEventDetail();
        } catch (err) {
            flash('danger', `Error al reconsiderar: ${err.message}`);
        }
    }

    /**
     * Construye URL de foto de host.
     * Interno: `/files/avatar/<user_id>/<filename>` requiere user_id — si el backend
     * pasa `photo_path` como "42/avatar.webp" intentamos usarlo.
     * Externo: `/files/event/<event_id>/hosts/<filename>`.
     */
    function resolveHostPhotoUrl(eventId, host) {
        return host.photo_url || host.avatar_url || '';
    }

    // ── Render: details list ─────────────────────────────────

    function renderDetailsList() {
        const list = document.getElementById('eventDetailsList');

        const capacityLabel = (() => {
            if (eventData.capacity_type === 'unlimited') return 'Sin límite';
            if (eventData.capacity_type === 'multiple')
                return `${eventData.current_registrations} / ${eventData.max_capacity} inscritos`;
            return '1 a 1 (entrevista)';
        })();

        const items = [
            { label: 'Tipo',      value: escapeHtml(eventTypeMeta(eventData.type).label) },
            { label: 'Estado',    value: escapeHtml(statusMeta(eventData.status).label) },
            { label: 'Capacidad', value: escapeHtml(capacityLabel) },
            { label: 'Inicio',    value: timeTag(eventData.event_date) },
            { label: 'Fin',       value: eventData.event_end_date ? timeTag(eventData.event_end_date) : '—' },
            { label: 'Ubicación', value: escapeHtml(eventData.location || 'Por definir') },
            { label: 'Programa',  value: escapeHtml(eventData.program_name || 'Todos') },
        ];

        list.innerHTML = items.map(i => `
            <div class="list-group-item">
                <dt class="detail-label">${i.label}</dt>
                <dd class="detail-value">${i.value}</dd>
            </div>`).join('');
    }

    // ── Render: description ───────────────────────────────────

    function renderDescription() {
        const desc = document.getElementById('eventDescription');
        desc.textContent = eventData.description || 'Sin descripción disponible.';
    }

    // ── Render: action block ─────────────────────────────────

    function renderActionBlock() {
        const block = document.getElementById('actionBlock');
        const quickBody = document.getElementById('quickActionsBody');
        const timelineBlock = document.getElementById('timelineBlock');

        if (eventData.capacity_type === 'single') {
            renderSingleBlock(block, quickBody, timelineBlock);
        } else {
            renderMultiBlock(block, quickBody);
        }
    }

    /**
     * Renders the appointment card and timeline for single-slot events.
     * @param {HTMLElement} block
     * @param {HTMLElement} quickBody
     * @param {HTMLElement} timelineBlock
     */
    function renderSingleBlock(block, quickBody, timelineBlock) {
        // Main action block: appointment card or informational notice
        if (myAppt) {
            block.innerHTML = `
                <div class="appointment-card p-4 event-section-card">
                    <div class="d-flex flex-column flex-sm-row justify-content-between align-items-start gap-3">
                        <div>
                            <p class="text-muted small mb-1">
                                <i class="bi bi-calendar-heart-fill me-1" aria-hidden="true"></i>Tu Cita
                            </p>
                            <p class="appointment-time mb-0">${escapeHtml(formatTime(myAppt.starts_at))}</p>
                            <p class="appointment-date mb-0">${timeTag(myAppt.starts_at)}</p>
                            <p class="appointment-date mt-1 mb-0">
                                <i class="bi bi-clock me-1" aria-hidden="true"></i>
                                Hasta las ${escapeHtml(formatTime(myAppt.ends_at))}
                            </p>
                        </div>
                        <div class="d-flex flex-column gap-2">
                            <button type="button" class="btn btn-outline-warning btn-sm" id="btnChangeRequest">
                                <i class="bi bi-arrow-left-right me-1" aria-hidden="true"></i>Solicitar Cambio
                            </button>
                            <button type="button" class="btn btn-outline-danger btn-sm" id="btnCancelAppt">
                                <i class="bi bi-x-lg me-1" aria-hidden="true"></i>Cancelar Cita
                            </button>
                        </div>
                    </div>
                </div>`;

            // Wire up buttons
            document.getElementById('btnChangeRequest')?.addEventListener('click', openChangeRequestModal);
            document.getElementById('btnCancelAppt')?.addEventListener('click', cancelAppointment);
        } else {
            block.innerHTML = `
                <div class="info-notice d-flex align-items-center gap-3 event-section-card">
                    <i class="bi bi-info-circle-fill notice-icon" aria-hidden="true"></i>
                    <div>
                        <strong>Sin cita asignada aún</strong>
                        <p class="mb-0 mt-1 text-muted small">
                            El coordinador te asignará un horario. Recibirás una notificación cuando esté lista.
                        </p>
                    </div>
                </div>`;
        }

        // Quick actions for single — no register button, just contextual info
        quickBody.innerHTML = `
            <p class="text-muted small mb-0">
                <i class="bi bi-shield-fill-check me-1" aria-hidden="true"></i>
                Las citas son asignadas por el coordinador del programa.
            </p>`;

        // Show the timeline
        timelineBlock.classList.remove('d-none');
        renderTimeline();
    }

    /**
     * Renders capacity info and registration controls for multiple/unlimited events.
     * @param {HTMLElement} block
     * @param {HTMLElement} quickBody
     */
    function renderMultiBlock(block, quickBody) {
        const isFull = eventData.capacity_type === 'multiple' &&
            eventData.current_registrations >= eventData.max_capacity;
        const isUnlimited = eventData.capacity_type === 'unlimited';

        // Capacity bar section
        let capacityHtml = '';
        if (!isUnlimited) {
            const pct = Math.min(
                100,
                Math.round((eventData.current_registrations / eventData.max_capacity) * 100)
            );
            const fillClass = pct >= 90 ? 'fill-danger' : pct >= 60 ? 'fill-warn' : 'fill-ok';
            capacityHtml = `
                <div class="mb-3">
                    <div class="d-flex justify-content-between mb-1">
                        <small class="text-muted">Cupo</small>
                        <small class="fw-semibold">${eventData.current_registrations} / ${eventData.max_capacity}</small>
                    </div>
                    <div class="capacity-bar" role="img"
                         aria-label="${eventData.current_registrations} de ${eventData.max_capacity} lugares ocupados">
                        <div class="capacity-bar-fill ${fillClass}" style="--progress:${pct}"></div>
                    </div>
                </div>`;
        } else {
            capacityHtml = `
                <div class="mb-3">
                    <span class="badge bg-success-soft">
                        <i class="bi bi-infinity me-1" aria-hidden="true"></i>Sin límite de cupos
                    </span>
                    <span class="ms-2 text-muted small">${eventData.current_registrations} inscrito(s)</span>
                </div>`;
        }

        block.innerHTML = `
            <div class="card border-0 shadow-sm event-section-card">
                <div class="card-header bg-white border-bottom">
                    <h2 class="h5 mb-0">
                        <i class="bi bi-people-fill me-2 text-primary" aria-hidden="true"></i>Inscripción
                    </h2>
                </div>
                <div class="card-body">
                    ${capacityHtml}
                    <div id="registrationStatus"></div>
                </div>
            </div>`;

        const regStatus = document.getElementById('registrationStatus');

        if (myReg) {
            const attended = myReg.status === 'attended';
            regStatus.innerHTML = `
                <div class="alert alert-success d-flex align-items-center gap-2 mb-3">
                    <i class="bi bi-check-circle-fill fs-5" aria-hidden="true"></i>
                    <div>
                        <strong>Estás inscrito</strong>
                        <p class="small text-muted mt-1 mb-0">
                            Registrado el ${timeTag(myReg.registered_at)}
                        </p>
                        ${attended ? `<p class="mt-2 mb-0">${SIIAP.statusBadge('approved', 'Asististe', 'sm')}</p>` : ''}
                    </div>
                </div>
                ${!attended ? `
                    <button type="button" class="btn btn-outline-danger w-100" id="btnUnregister">
                        <i class="bi bi-person-dash me-1" aria-hidden="true"></i>Cancelar Registro
                    </button>` : ''}`;

            document.getElementById('btnUnregister')?.addEventListener('click', unregisterFromEvent);
        } else if (isFull) {
            regStatus.innerHTML = `
                <div class="alert alert-warning d-flex align-items-center gap-2">
                    <i class="bi bi-exclamation-triangle-fill fs-5" aria-hidden="true"></i>
                    <div><strong>Cupo lleno</strong><br>
                    <span class="small">No hay lugares disponibles en este momento.</span></div>
                </div>`;
        } else {
            regStatus.innerHTML = `
                <button type="button" class="btn btn-primary w-100" id="btnRegister">
                    <i class="bi bi-person-plus-fill me-1" aria-hidden="true"></i>Registrarme al evento
                </button>`;
            document.getElementById('btnRegister')?.addEventListener('click', registerToEvent);
        }

        // Quick actions column
        quickBody.innerHTML = `
            <p class="text-muted small mb-0">
                <i class="bi bi-calendar3 me-1" aria-hidden="true"></i>
                ${isUnlimited
                    ? 'Sin restricción de cupo.'
                    : isFull
                    ? 'Cupo agotado.'
                    : `${eventData.max_capacity - eventData.current_registrations} lugar(es) disponible(s).`}
            </p>`;
    }

    // ── Render: timeline (single capacity) ──────────────────

    function renderTimeline() {
        const container = document.getElementById('timelineContent');
        if (!windows.length) {
            container.innerHTML = `
                <div class="empty-state empty-state--compact">
                    <div class="empty-state__icon"><i class="bi bi-calendar-x" aria-hidden="true"></i></div>
                    <h3 class="empty-state__title">Sin horarios publicados</h3>
                    <p class="empty-state__description">No hay ventanas de horario configuradas aún.</p>
                </div>`;
            return;
        }

        container.innerHTML = windows.map(win => {
            const slots = win.slots || [];
            const slotsHtml = slots.length
                ? slots.map(slot => {
                    const isMine = myAppt && myAppt.slot_id === slot.id;
                    const cls    = isMine ? 'slot-mine' : slot.status === 'free' ? 'slot-free' : 'slot-booked';
                    const icon   = isMine ? 'bi-star-fill' : slot.status === 'free' ? 'bi-circle-fill' : 'bi-record-circle';
                    const label  = isMine ? 'Mi cita' : slot.status === 'free' ? 'Libre' : 'Ocupado';
                    return `
                        <span class="slot-pill ${cls}" title="${escapeHtml(label)}: ${formatTime(slot.starts_at)} – ${formatTime(slot.ends_at)}">
                            <i class="bi ${icon}" aria-hidden="true"></i>
                            ${escapeHtml(formatTime(slot.starts_at))}
                            <span class="slot-label-sm">${escapeHtml(label)}</span>
                        </span>`;
                }).join('')
                : `<span class="text-muted small">Sin horarios en esta ventana</span>`;

            const windowDate = win.date
                ? `<time datetime="${escapeHtml(win.date)}">${escapeHtml(SIIAP.formatDate(win.date, 'long'))}</time>`
                : '—';

            return `
                <div class="timeline-window">
                    <p class="timeline-window-header">
                        <i class="bi bi-calendar-day me-2 text-primary" aria-hidden="true"></i>${windowDate}
                        <span class="text-muted fw-normal ms-2 small">${escapeHtml(win.start_time || '')} – ${escapeHtml(win.end_time || '')}</span>
                    </p>
                    <div class="slots-grid">${slotsHtml}</div>
                </div>`;
        }).join('');
    }

    // ── Render: orchestrator ──────────────────────────────────

    function renderAll() {
        renderHero();
        renderDescription();
        renderDetailsList();
        renderActionBlock();
        renderShareUrl();
        loadHosts(eventId);
        loadGallery(eventId);
    }

    // ── Actions ───────────────────────────────────────────────

    async function registerToEvent() {
        try {
            await apiRequest(`${API}/attendance/event/${eventId}/register`, {
                method: 'POST',
                body: JSON.stringify({ notes: '' }),
            });
            flash('success', 'Te has inscrito exitosamente al evento.');
            await loadEventDetail();
        } catch (err) {
            flash('danger', `Error al inscribirse: ${err.message}`);
        }
    }

    async function unregisterFromEvent() {
        const ok = await siiapConfirm({
            type: 'warning',
            title: 'Cancelar inscripción',
            message: '¿Estás seguro de que deseas cancelar tu inscripción a este evento?',
            confirmLabel: 'Sí, cancelar',
        });
        if (!ok) return;

        try {
            await apiRequest(`${API}/attendance/event/${eventId}/unregister`, {
                method: 'POST',
                body: JSON.stringify({}),
            });
            flash('success', 'Inscripción cancelada exitosamente.');
            await loadEventDetail();
        } catch (err) {
            flash('danger', `Error al cancelar: ${err.message}`);
        }
    }

    function openChangeRequestModal() {
        const reasonEl = document.getElementById('changeReason');
        reasonEl.value = '';
        reasonEl.removeAttribute('aria-invalid');
        document.getElementById('changeSuggestions').value = '';
        const modal = new bootstrap.Modal(document.getElementById('changeRequestModal'));
        modal.show();
    }

    async function submitChangeRequest() {
        const reasonEl    = document.getElementById('changeReason');
        const reason      = reasonEl.value.trim();
        const suggestions = document.getElementById('changeSuggestions').value.trim();

        if (!myAppt?.id) {
            flash('danger', 'No tienes una cita activa. Recarga la página.');
            return;
        }

        if (!reason) {
            reasonEl.setAttribute('aria-invalid', 'true');
            reasonEl.focus();
            flash('warning', 'Por favor indica el motivo del cambio.');
            return;
        }
        reasonEl.removeAttribute('aria-invalid');

        const modal = bootstrap.Modal.getInstance(document.getElementById('changeRequestModal'));

        try {
            await apiRequest(`${API}/appointments/${myAppt.id}/change-requests`, {
                method: 'POST',
                body: JSON.stringify({ reason, suggestions }),
            });
            modal?.hide();
            flash('success', 'Solicitud de cambio enviada. El coordinador te notificará la respuesta.');
        } catch (err) {
            flash('danger', `Error al enviar la solicitud: ${err.message}`);
        }
    }

    async function cancelAppointment() {
        if (!myAppt?.id) {
            flash('danger', 'No tienes una cita activa. Recarga la página.');
            return;
        }

        const ok = await siiapConfirm({
            type: 'danger',
            title: 'Cancelar cita',
            message: '¿Estás seguro de que deseas cancelar tu cita? Esta acción no se puede deshacer.',
            confirmLabel: 'Sí, cancelar cita',
        });
        if (!ok) return;

        try {
            await apiRequest(`${API}/appointments/${myAppt.id}`, {
                method: 'DELETE',
            });
            flash('success', 'Cita cancelada exitosamente.');
            await loadEventDetail();
        } catch (err) {
            flash('danger', `Error al cancelar la cita: ${err.message}`);
        }
    }

    // ── Real-time socket listeners ───────────────────────────

    let reloadDebounce = null;

    function debouncedReload(delayMs = 500) {
        if (reloadDebounce) clearTimeout(reloadDebounce);
        reloadDebounce = setTimeout(() => loadEventDetail(), delayMs);
    }

    window.addEventListener('siiap:event:changed', (e) => {
        const detail = e.detail || {};
        if (String(detail.event_id) !== String(eventId)) return;

        if (detail.action === 'deleted') {
            flash('danger', 'Este evento ha sido eliminado.');
            setTimeout(() => { window.location.href = '/events/'; }, 2500);
            return;
        }

        if (detail.action === 'updated') {
            flash('info', 'El evento fue actualizado. Recargando información...');
            debouncedReload();
        }
    });

    window.addEventListener('siiap:appointment:changed', (e) => {
        const detail = e.detail || {};
        // Reload if the appointment belongs to the current event or current user's appointment
        const affectsEvent = String(detail.event_id) === String(eventId);
        const affectsMe    = myAppt && String(detail.appointment_id) === String(myAppt.id);
        if (affectsEvent || affectsMe) {
            debouncedReload(500);
        }
    });

    window.addEventListener('siiap:notification:new', () => {
        // Refresh if a new notification potentially relates to an appointment update
        if (myAppt) {
            debouncedReload(800);
        }
    });

    // ── Event listeners ───────────────────────────────────────

    document.getElementById('btnRetry')?.addEventListener('click', () => {
        showLoading();
        loadEventDetail();
    });

    document.getElementById('btnConfirmChangeRequest')?.addEventListener('click', submitChangeRequest);

    // Lightbox close
    document.getElementById('lightboxClose')?.addEventListener('click', () => {
        document.getElementById('eventLightbox')?.close();
    });

    // Close lightbox on backdrop click
    document.getElementById('eventLightbox')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) e.currentTarget.close();
    });

    // Copy share URL
    document.getElementById('btnCopyUrl')?.addEventListener('click', () => {
        const input = document.getElementById('shareUrlInput');
        if (!input) return;
        navigator.clipboard.writeText(input.value)
            .then(() => flash('success', 'Enlace copiado al portapapeles.'))
            .catch(() => {
                input.select();
                document.execCommand('copy');
                flash('success', 'Enlace copiado al portapapeles.');
            });
    });

    // ── Bootstrap ─────────────────────────────────────────────

    if (!eventId) {
        showError('ID de evento no encontrado. Vuelve al listado e intenta de nuevo.');
    } else {
        loadEventDetail();
    }
})();
