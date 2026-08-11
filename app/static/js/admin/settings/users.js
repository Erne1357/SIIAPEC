// app/static/js/admin/settings/users.js
(function() {
    'use strict';
    
    // Usar el origen actual para evitar problemas de mixed content en HTTPS
    const API_BASE = `${window.location.origin}/api/v1/admin/users`;
    let currentPage = 1;
    let currentFilters = {};
    
    // Escapado de salida. Son referencias al helper canónico
    // (app/static/js/utils/escape.js), no copias locales: se resuelven en cada
    // llamada para no depender del orden de carga de los scripts.
    const escapeHtml = (value) => window.SIIAP.escapeHtml(value);
    const escapeAttr = (value) => window.SIIAP.escapeAttr(value);

    // Anuncia el resultado de una carga asíncrona en la región viva compartida.
    const announce = (message) => {
        if (window.SIIAP && typeof window.SIIAP.announce === 'function') {
            window.SIIAP.announce(message);
        }
    };

    // Función para obtener CSRF token
    const getCsrf = () => {
        const el = document.querySelector('meta[name="csrf-token"]');
        return el ? el.getAttribute('content') : '';
    };

    // Silueta genérica. Nunca dejar `src=""`: el navegador lo resuelve contra la
    // URL de la página, vuelve a descargarla como imagen y pinta un icono roto.
    const DEFAULT_AVATAR = '/static/assets/images/default.jpg';

    /**
     * ¿Esta fila viene recortada al nivel reducido entre programas?
     *
     * El backend marca la fila con `restricted: true` cuando el usuario queda
     * fuera del alcance del llamador; el payload conserva sólo id, nombre y
     * correo (ver `program_scope_service.CROSS_PROGRAM_SUMMARY_FIELDS`).
     *
     * La comprobación estructural es la red de seguridad: `role` y `username`
     * viajan en TODA fila completa y nunca están en la lista blanca, así que su
     * ausencia identifica una fila recortada aunque la marca no llegue. Importa
     * porque el fallo silencioso es afirmativo: sin esto, `user.is_active`
     * indefinido se pintaba como «Inactivo» —un dato falso— en la misma fila
     * desde la que un coordinador decide si actuar.
     */
    const isRestrictedRow = (user) =>
        !user || user.restricted === true ||
        user.role === undefined || user.username === undefined;

    // Cargar lista de usuarios
    async function loadUsers(page = 1) {
        const loadingIndicator = document.getElementById('loadingIndicator');
        const tableContainer = document.getElementById('usersTableContainer');
        const noResults = document.getElementById('noResultsMessage');
        
        loadingIndicator.classList.remove('d-none');
        tableContainer.classList.add('d-none');
        noResults.classList.add('d-none');
        
        // Construir query params
        const params = new URLSearchParams({
            page: page,
            per_page: 20,
            ...currentFilters
        });
        
        try {
            const res = await fetch(`${API_BASE}?${params}`);
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message || 'Error al cargar usuarios');
            
            console.log("JSON DATA en load users:", json);


            const { users, pagination } = json.data;
            
            loadingIndicator.classList.add('d-none');
            
            if (users.length === 0) {
                noResults.classList.remove('d-none');
                announce('Ningún usuario coincide con los filtros aplicados.');
                return;
            }
            
            renderUsersTable(users);
            renderPagination(pagination);
            updateTotalCount(pagination.total);
            
            tableContainer.classList.remove('d-none');
            announce(
                pagination.total === 1
                    ? '1 usuario encontrado.'
                    : `${pagination.total} usuarios encontrados.`
            );
            
        } catch (error) {
            console.error('Error:', error);
            loadingIndicator.classList.add('d-none');
            showFlash('danger', 'Error al cargar usuarios: ' + error.message);
        }
    }
    
    // Celda sin dato POR FALTA DE PERMISO. No es un valor vacío: es una
    // pregunta que esta cuenta no tiene derecho a hacer, y así se dice.
    const unavailableCell = () => `
        <span class="text-muted small">
            <i class="bi bi-shield-lock me-1" aria-hidden="true"></i>No disponible
        </span>`;

    /**
     * Fila del nivel reducido: nombre y correo reales, todo lo demás declarado
     * como no disponible y sin una sola acción que vaya a devolver 403.
     */
    function renderRestrictedRow(user) {
        const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
        const idAttr = escapeAttr(user.id);

        return `
            <tr class="user-row table-secondary">
                <th scope="row" class="fw-normal">
                    <div class="d-flex align-items-center">
                        <img src="${escapeAttr(DEFAULT_AVATAR)}" class="rounded-circle avatar-sm me-2" alt="">
                        <div>
                            <button type="button" class="user-row__trigger"
                                    data-action="show-detail" data-user-id="${idAttr}">
                                ${escapeHtml(fullName)}
                            </button>
                            <small class="text-muted d-block">${escapeHtml(user.email)}</small>
                            <small class="text-muted d-block">
                                <i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>Pertenece a otro programa
                            </small>
                        </div>
                    </div>
                </th>
                <td>${unavailableCell()}</td>
                <td>${unavailableCell()}</td>
                <td>${unavailableCell()}</td>
                <td>${unavailableCell()}</td>
                <td class="text-end">
                    <i class="bi bi-lock text-muted" aria-hidden="true"></i>
                    <span class="visually-hidden">
                        Sin acciones disponibles: esta cuenta pertenece a un programa que no gestionas.
                    </span>
                </td>
            </tr>
        `;
    }

    // Renderizar tabla de usuarios
    function renderUsersTable(users) {
        const tbody = document.getElementById('usersTableBody');

        tbody.innerHTML = users.map(user => {
            if (isRestrictedRow(user)) return renderRestrictedRow(user);

            // Mismo texto que antes; se escapa una sola vez por contexto.
            const fullName = `${user.first_name} ${user.last_name}`;
            const nameText = escapeHtml(fullName);
            const nameAttr = escapeAttr(fullName);
            const idAttr = escapeAttr(user.id);
            const toggleLabel = user.is_active ? 'Desactivar' : 'Activar';

            // getRoleBadgeClass() devuelve siempre un valor de su propio mapa,
            // nunca texto de la base de datos: no requiere escapado.
            return `
            <tr class="user-row">
                <th scope="row" class="fw-normal">
                    <div class="d-flex align-items-center">
                        <img src="${escapeAttr(user.avatar_url || DEFAULT_AVATAR)}" class="rounded-circle avatar-sm me-2" alt="">
                        <div>
                            <button type="button" class="user-row__trigger"
                                    data-action="show-detail" data-user-id="${idAttr}">
                                ${nameText}
                            </button>
                            <small class="text-muted d-block">${escapeHtml(user.email)}</small>
                        </div>
                    </div>
                </th>
                <td>
                    <span class="badge ${getRoleBadgeClass(user.role)}">
                        ${escapeHtml(getRoleLabel(user.role))}
                    </span>
                </td>
                <td>
                    ${user.program
                        ? `<span class="badge bg-secondary">${escapeHtml(user.program.name)}</span>`
                        : '<small class="text-muted">Sin asignar</small>'
                    }
                </td>
                <td>
                    ${user.control_number
                        ? `<span class="badge bg-dark control-number-badge">${escapeHtml(user.control_number)}</span>`
                        : '<small class="text-muted">Sin asignar</small>'
                    }
                </td>
                <td>
                    <span class="badge badge-status ${user.is_active ? 'bg-success' : 'bg-danger'}">
                        ${user.is_active ? 'Activo' : 'Inactivo'}
                    </span>
                </td>
                <td class="text-end">
                    <div class="btn-group btn-group-sm" role="group"
                         aria-label="Acciones de ${nameAttr}">
                        <button type="button" class="btn btn-outline-primary tap-target"
                                data-action="edit-user" data-user-id="${idAttr}"
                                aria-label="Editar a ${nameAttr}"
                                title="Editar a ${nameAttr}">
                            <i class="bi bi-pencil" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="btn btn-outline-danger tap-target"
                                data-action="reset-password" data-user-id="${idAttr}"
                                data-user-name="${nameAttr}"
                                aria-label="Restablecer la contraseña de ${nameAttr}"
                                title="Restablecer la contraseña de ${nameAttr}">
                            <i class="bi bi-key" aria-hidden="true"></i>
                        </button>
                        ${(!user.control_number && user.role === 'applicant') ? `
                        <button type="button" class="btn btn-outline-primary tap-target"
                                data-action="assign-control-number" data-user-id="${idAttr}"
                                aria-label="Asignar número de control a ${nameAttr}"
                                title="Asignar número de control y convertir en estudiante">
                            <i class="bi bi-123" aria-hidden="true"></i>
                        </button>
                        ` : ''}
                        <button type="button" class="btn btn-outline-${user.is_active ? 'danger' : 'success'} tap-target"
                                data-action="toggle-active" data-user-id="${idAttr}"
                                aria-label="${toggleLabel} a ${nameAttr}"
                                title="${toggleLabel} a ${nameAttr}">
                            <i class="bi bi-${user.is_active ? 'x-circle' : 'check-circle'}" aria-hidden="true"></i>
                        </button>
                        ${window.siiapStudentRecordBtn ? window.siiapStudentRecordBtn(user.id) : ''}
                    </div>
                </td>
            </tr>
        `;
        }).join('');
    }

    // Una sola escucha delegada para toda la tabla: ningún botón lleva onclick
    // en línea, así el nombre del usuario nunca se concatena dentro de código.
    function onUsersTableClick(event) {
        const btn = event.target.closest('[data-action]');
        if (!btn) return;

        const userId = btn.dataset.userId;

        switch (btn.dataset.action) {
            case 'show-detail':
                showUserDetail(userId);
                break;
            case 'edit-user':
                editUser(userId);
                break;
            case 'reset-password':
                resetPassword(userId, btn.dataset.userName || '');
                break;
            case 'assign-control-number':
                assignControlNumber(userId);
                break;
            case 'toggle-active':
                toggleUserActive(userId);
                break;
        }
    }
    
    // Renderizar paginación
    function renderPagination(pagination) {
        const container = document.getElementById('paginationControls');
        const { page, pages, has_prev, has_next } = pagination;
        
        if (pages <= 1) {
            container.innerHTML = '';
            return;
        }
        
        let html = '';
        
        // Anterior
        html += `
            <li class="page-item ${!has_prev ? 'disabled' : ''}">
                <a class="page-link" href="#" aria-label="Página anterior"
                   data-page="${escapeAttr(page - 1)}">
                    <i class="bi bi-chevron-left" aria-hidden="true"></i>
                </a>
            </li>
        `;

        // Páginas
        for (let i = 1; i <= pages; i++) {
            if (i === 1 || i === pages || (i >= page - 2 && i <= page + 2)) {
                html += `
                    <li class="page-item ${i === page ? 'active' : ''}">
                        <a class="page-link" href="#" aria-label="Página ${i}"
                           ${i === page ? 'aria-current="page"' : ''}
                           data-page="${escapeAttr(i)}">
                            ${i}
                        </a>
                    </li>
                `;
            } else if (i === page - 3 || i === page + 3) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        // Siguiente
        html += `
            <li class="page-item ${!has_next ? 'disabled' : ''}">
                <a class="page-link" href="#" aria-label="Página siguiente"
                   data-page="${escapeAttr(page + 1)}">
                    <i class="bi bi-chevron-right" aria-hidden="true"></i>
                </a>
            </li>
        `;

        container.innerHTML = html;
    }

    // Escucha delegada de la paginación: el enlace sólo declara su página.
    function onPaginationClick(event) {
        const link = event.target.closest('[data-page]');
        if (!link) return;

        event.preventDefault();

        const item = link.closest('.page-item');
        if (item && item.classList.contains('disabled')) return;

        loadUsers(Number(link.dataset.page));
    }
    
    // Actualizar contador total
    function updateTotalCount(total) {
        document.getElementById('totalUsersCount').textContent = `${total} usuario${total !== 1 ? 's' : ''}`;
    }
    
    /**
     * Detalle del nivel reducido.
     *
     * El endpoint responde 200 con nombre y correo, sin historial, sin foto,
     * sin rol y sin estado de la cuenta. Se dice qué falta y por qué, en vez de
     * imprimir «No registrado» —que afirmaría que el dato no existe— o
     * «Inactivo» —que afirmaría algo directamente falso—.
     */
    function renderRestrictedDetail(user) {
        const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim();
        return `
            <div class="alert alert-info d-flex align-items-start gap-2">
                <i class="bi bi-shield-lock-fill flex-shrink-0 mt-1" aria-hidden="true"></i>
                <div>
                    <strong>Información limitada.</strong>
                    Esta cuenta pertenece a un programa que no gestionas. Solo
                    puedes consultar su nombre y su correo; el rol, el estado de
                    la cuenta, el número de control, la foto y el historial
                    corresponden a su programa.
                </div>
            </div>
            <h3 class="h6">Información básica</h3>
            <dl class="row mb-0">
                <dt class="col-5">Nombre</dt>
                <dd class="col-7">${escapeHtml(fullName)}</dd>
                <dt class="col-5">Correo</dt>
                <dd class="col-7">${escapeHtml(user.email)}</dd>
                <dt class="col-5">Rol</dt>
                <dd class="col-7">${unavailableCell()}</dd>
                <dt class="col-5">Estado de la cuenta</dt>
                <dd class="col-7">${unavailableCell()}</dd>
                <dt class="col-5">Historial</dt>
                <dd class="col-7">${unavailableCell()}</dd>
            </dl>
        `;
    }

    // Ver detalles de usuario
    async function showUserDetail(userId) {
        const modal = new bootstrap.Modal(document.getElementById('userDetailModal'));
        const content = document.getElementById('userDetailContent');
        
        content.innerHTML = '<div class="text-center"><div class="spinner-border"></div></div>';
        modal.show();
        
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}`);
            const json = await res.json();

            if (!res.ok) throw new Error(json.error?.message);

            console.log("JSON DATA en showUserDetail:", json);


            const user = json.data.user;
            const program = json.data.user.program;
            const history = json.data.history || [];

            if (isRestrictedRow(user)) {
                content.innerHTML = renderRestrictedDetail(user);
                return;
            }

            content.innerHTML = `
                <div class="row">
                    <div class="col-md-4 text-center">
                        <img src="${escapeAttr(user.avatar_url || DEFAULT_AVATAR)}" alt="" loading="lazy" class="rounded-circle avatar-xl">
                        <h5 class="mt-3">${escapeHtml(user.first_name)} ${escapeHtml(user.last_name)}</h5>
                        <span class="badge ${getRoleBadgeClass(user.role)}">${escapeHtml(getRoleLabel(user.role))}</span>
                        <br>
                        <span class="badge ${user.is_active ? 'bg-success' : 'bg-danger'} mt-2">
                            ${user.is_active ? 'Activo' : 'Inactivo'}
                        </span>
                    </div>
                    <div class="col-md-8">
                        <h6>Información básica</h6>
                        <dl class="row mb-0">
                            <dt class="col-5">Correo</dt><dd class="col-7">${escapeHtml(user.email)}</dd>
                            <dt class="col-5">Usuario</dt><dd class="col-7">${escapeHtml(user.username)}</dd>
                            <dt class="col-5">Teléfono</dt><dd class="col-7">${escapeHtml(user.phone || 'No registrado')}</dd>
                            <dt class="col-5">CURP</dt><dd class="col-7">${escapeHtml(user.curp || 'No registrado')}</dd>
                            <dt class="col-5">Fecha de registro</dt><dd class="col-7">${formatDate(user.registration_date)}</dd>
                            <dt class="col-5">Último acceso</dt><dd class="col-7">${formatDate(user.last_login)}</dd>
                        </dl>

                        ${program ? `
                        <h6 class="mt-3">Programa</h6>
                        <p><strong>${escapeHtml(program.name)}</strong> (${escapeHtml(program.slug)})</p>
                        ` : ''}

                        ${user.control_number ? `
                        <h6 class="mt-3">Número de Control</h6>
                        <p class="font-monospace fs-5">${escapeHtml(user.control_number)}</p>
                        ` : ''}

                        <h6 class="mt-3">Historial Reciente</h6>
                        ${history.length > 0 ? `
                            <ul class="list-group list-group-flush">
                                ${history.slice(0, 5).map(entry => `
                                    <li class="list-group-item px-0 py-2">
                                        <small>
                                            <strong>${escapeHtml(entry.action_label)}</strong><br>
                                            ${escapeHtml(entry.admin_name)} - ${formatDate(entry.timestamp)}
                                        </small>
                                    </li>
                                `).join('')}
                            </ul>
                        ` : '<p class="text-muted">Sin historial</p>'}

                        ${history.length > 5 ? `
                            <button type="button" class="btn btn-sm btn-link"
                                    data-action="show-history" data-user-id="${escapeAttr(userId)}">
                                Ver historial completo
                            </button>
                        ` : ''}
                    </div>
                </div>
                <div id="userDelegationsSection"></div>
            `;

            if (user.role === 'social_service') {
                const section = document.getElementById('userDelegationsSection');
                if (section) {
                    section.innerHTML = '<div class="text-center py-2"><div class="spinner-border spinner-border-sm"></div></div>';
                    const delsHtml = await loadUserDelegations(userId);
                    section.innerHTML = delsHtml;
                }
            }

        } catch (error) {
            content.innerHTML = `<div class="alert alert-danger">${escapeHtml(error.message)}</div>`;
        }
    }

    // Escucha delegada del modal de detalle: cubre el botón de historial y los
    // botones de revocación que se inyectan después dentro del mismo contenedor.
    function onUserDetailClick(event) {
        const btn = event.target.closest('[data-action]');
        if (!btn) return;

        switch (btn.dataset.action) {
            case 'show-history':
                showHistory(btn.dataset.userId);
                break;
            case 'revoke-delegation':
                revokeDelegation(btn.dataset.delegationId, btn.dataset.userId);
                break;
        }
    }

    // Editar usuario
    async function editUser(userId) {
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}`);
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            const user = json.data.user;
            
            document.getElementById('edit_user_id').value = user.id;
            document.getElementById('edit_first_name').value = user.first_name;
            document.getElementById('edit_last_name').value = user.last_name;
            document.getElementById('edit_mother_last_name').value = user.mother_last_name || '';
            document.getElementById('edit_email').value = user.email;
            
            const modal = new bootstrap.Modal(document.getElementById('editUserModal'));
            modal.show();
            
        } catch (error) {
            showFlash('danger', 'Error al cargar usuario: ' + error.message);
        }
    }
    
    // Guardar cambios de edición
    async function saveUserEdit(event) {
        event.preventDefault();
        
        const userId = document.getElementById('edit_user_id').value;
        const data = {
            first_name: document.getElementById('edit_first_name').value,
            last_name: document.getElementById('edit_last_name').value,
            mother_last_name: document.getElementById('edit_mother_last_name').value,
            email: document.getElementById('edit_email').value
        };
        
        try {
            const res = await fetch(`${API_BASE}/${userId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrf()
                },
                body: JSON.stringify(data)
            });
            
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            if (json.flash) {
                json.flash.forEach(f => showFlash(f.level, f.message));
            }
            
            bootstrap.Modal.getInstance(document.getElementById('editUserModal')).hide();
            loadUsers(currentPage);
            
        } catch (error) {
            showFlash('danger', 'Error al guardar: ' + error.message);
        }
    }
    
    // Password reset: the admin never sees a credential. The backend
    // invalidates the stored password and mails a single-use link.
    async function resetPassword(userId, userName) {
        const ok = await siiapConfirm({
            type: 'warning',
            title: 'Restablecer contraseña',
            message: `¿Restablecer la contraseña de ${userName}?\n\nSu contraseña actual dejará de funcionar de inmediato y recibirá en su correo un enlace de un solo uso, válido 45 minutos, para definir una nueva. Tú no verás ninguna contraseña.`,
            confirmLabel: 'Sí, enviar enlace',
        });
        if (!ok) return;
        
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}/reset-password`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrf()
                }
            });
            
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            if (json.flash) {
                json.flash.forEach(f => showFlash(f.level, f.message));
            }
            
        } catch (error) {
            showFlash('danger', 'Error: ' + error.message);
        }
    }
    
    // Toggle activo/inactivo
    async function toggleUserActive(userId) {
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}/toggle-active`, {
                method: 'PATCH',
                headers: {
                    'X-CSRFToken': getCsrf()
                }
            });
            
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            if (json.flash) {
                json.flash.forEach(f => showFlash(f.level, f.message));
            }
            
            loadUsers(currentPage);
            
        } catch (error) {
            showFlash('danger', 'Error: ' + error.message);
        }
    }
    
    // Asignar número de control
    async function assignControlNumber(userId) {
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}`);
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            const user = json.data.user;
            const program = json.data.user.program;
            
            if (!program) {
                showFlash('warning', 'El usuario debe tener un programa asignado primero.');
                return;
            }
            
            document.getElementById('control_user_id').value = user.id;
            document.getElementById('control_user_name').value = `${user.first_name} ${user.last_name}`;
            document.getElementById('control_program_name').value = program.name;
            document.getElementById('control_number').value = '';
            document.getElementById('controlNumberFeedback').textContent = '';
            
            const modal = new bootstrap.Modal(document.getElementById('assignControlNumberModal'));
            modal.show();
            
        } catch (error) {
            showFlash('danger', 'Error: ' + error.message);
        }
    }
    
    // Guardar número de control
    async function saveControlNumber(event) {
        event.preventDefault();
        
        const userId = document.getElementById('control_user_id').value;
        const controlNumber = document.getElementById('control_number').value.trim().toUpperCase();
        
        // Validación básica del formato
        if (!/^[MD]\d{8}$/.test(controlNumber)) {
            showFlash('danger', 'Formato inválido. Debe ser M o D seguido de 8 dígitos.');
            return;
        }
        
        try {
            const res = await fetch(`${API_BASE}/${userId}/assign-control-number`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrf()
                },
                body: JSON.stringify({ control_number: controlNumber })
            });
            
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            if (json.flash) {
                json.flash.forEach(f => showFlash(f.level, f.message));
            }
            
            bootstrap.Modal.getInstance(document.getElementById('assignControlNumberModal')).hide();
            loadUsers(currentPage);
            
        } catch (error) {
            showFlash('danger', 'Error: ' + error.message);
        }
    }
    
    // Ver historial completo
    async function showHistory(userId) {
        const modal = new bootstrap.Modal(document.getElementById('userHistoryModal'));
        const content = document.getElementById('userHistoryContent');
        
        content.innerHTML = '<div class="text-center"><div class="spinner-border"></div></div>';
        modal.show();
        
        try {
            const res = await fetch(`${API_BASE}/${encodeURIComponent(userId)}/history`);
            const json = await res.json();
            
            if (!res.ok) throw new Error(json.error?.message);
            
            const history = json.data.history || [];
            
            if (history.length === 0) {
                content.innerHTML = '<p class="text-center text-muted">Sin historial</p>';
                return;
            }
            
            content.innerHTML = `
                <div class="list-group">
                    ${history.map(entry => `
                        <div class="list-group-item">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    <h6 class="mb-1">${escapeHtml(entry.action_label)}</h6>
                                    ${entry.details ? `<p class="mb-1 small">${escapeHtml(entry.details)}</p>` : ''}
                                    <small class="text-muted">Por: ${escapeHtml(entry.admin_name)}</small>
                                </div>
                                <small class="text-muted">${formatDate(entry.timestamp)}</small>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;

        } catch (error) {
            content.innerHTML = `<div class="alert alert-danger">${escapeHtml(error.message)}</div>`;
        }
    }
    
    // ========================================================================
    // Servicio Social — crear usuario con delegación
    // ========================================================================

    const PERMISSIONS_API = `${window.location.origin}/api/v1/permissions`;
    const SCOPE_PROGRAMS_API = `${window.location.origin}/api/v1/coordinator/programs`;

    let delegatableCache = null;
    let scopeProgramsCache = null;

    /**
     * Programas que el creador alcanza realmente (coordinados + delegados).
     * Para el jefe de posgrado son todos. El endpoint ya aplica el alcance, así
     * que aquí no hay que reconstruirlo.
     */
    async function loadScopePrograms() {
        if (scopeProgramsCache) return scopeProgramsCache;
        const res = await fetch(SCOPE_PROGRAMS_API);
        const json = await res.json();
        if (!res.ok) throw new Error(json.error?.message || 'No se pudieron cargar los programas');
        scopeProgramsCache = json.programs || [];
        return scopeProgramsCache;
    }

    /**
     * Selector de programas de la cuenta de servicio social.
     *
     * Es obligatorio para un creador con alcance global. Antes el modal no lo
     * tenía: el JS enviaba `program_ids: null`, cada delegación se guardaba con
     * `program_id = NULL` y `get_accessible_program_ids()` descarta esas filas,
     * de modo que la cuenta nacía sin alcance alguno. La persona activaba su
     * contraseña, entraba y encontraba la revisión vacía y cada endpoint en 403,
     * mientras el coordinador veía sus delegaciones «activas».
     */
    function renderProgramSelector(programs) {
        const list = document.getElementById('ss_programs_list');
        if (!programs.length) {
            list.innerHTML = `
                <div class="alert alert-warning mb-0">
                    No hay programas dentro de tu alcance, así que no puedes crear
                    una cuenta de servicio social.
                </div>`;
            return;
        }

        list.innerHTML = programs.map(p => `
            <div class="form-check">
                <input class="form-check-input ss-program-check" type="checkbox"
                       id="ss_program_${escapeAttr(p.id)}" value="${escapeAttr(p.id)}">
                <label class="form-check-label" for="ss_program_${escapeAttr(p.id)}">
                    ${escapeHtml(p.name)}
                </label>
            </div>
        `).join('');
    }

    async function openCreateSocialService() {
        const ctx = window.SIIAP_USERS_CTX || {};
        const form = document.getElementById('createSocialServiceForm');
        if (form) form.reset();

        const scopeInfo = document.getElementById('ss_scope_info');
        const programsBlock = document.getElementById('ss_programs_block');
        const programsList = document.getElementById('ss_programs_list');

        scopeInfo.classList.add('d-none');
        programsBlock.classList.toggle('d-none', !ctx.canDelegateGlobal);
        if (ctx.canDelegateGlobal) {
            programsList.innerHTML = `
                <div class="text-center py-2">
                    <div class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></div>
                    <span class="ms-2">Cargando programas…</span>
                </div>`;
        }

        const list = document.getElementById('ss_permissions_list');
        const loading = document.getElementById('ss_permissions_loading');
        list.classList.add('d-none');
        loading.classList.remove('d-none');

        const modal = new bootstrap.Modal(document.getElementById('createSocialServiceModal'));
        modal.show();

        // Alcance: selector obligatorio para el jefe de posgrado, informativo
        // (y con el alcance REAL, no sólo lo coordinado) para un coordinador.
        try {
            const programs = await loadScopePrograms();
            if (ctx.canDelegateGlobal) {
                renderProgramSelector(programs);
            } else {
                const names = programs.map(p => p.name).join(', ')
                    || (ctx.coordinatedProgramNames || []).join(', ')
                    || '(sin programas)';
                scopeInfo.innerHTML = `<i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>Ámbito de delegación: <strong>${escapeHtml(names)}</strong>. Los permisos se aplicarán a cada uno de los programas a tu alcance.`;
                scopeInfo.classList.remove('d-none');
            }
        } catch (error) {
            if (ctx.canDelegateGlobal) {
                programsList.innerHTML = `<div class="alert alert-danger mb-0">${escapeHtml(error.message)}</div>`;
            }
        }

        try {
            if (!delegatableCache) {
                const res = await fetch(`${PERMISSIONS_API}/delegatable`);
                const json = await res.json();
                if (!res.ok) throw new Error(json.error?.message || 'Error al cargar permisos');
                delegatableCache = json.data || [];
            }
            renderDelegatablePermissions(delegatableCache);
        } catch (error) {
            loading.innerHTML = `<div class="alert alert-danger mb-0">${escapeHtml(error.message)}</div>`;
        }
    }

    function renderDelegatablePermissions(perms) {
        const list = document.getElementById('ss_permissions_list');
        const loading = document.getElementById('ss_permissions_loading');

        if (!perms.length) {
            loading.innerHTML = `<div class="alert alert-warning mb-0">No tienes permisos delegables.</div>`;
            return;
        }

        const byResource = {};
        perms.forEach(p => {
            const res = p.codename.split('.')[0];
            if (!byResource[res]) byResource[res] = [];
            byResource[res].push(p);
        });

        const html = Object.keys(byResource).sort().map(resource => {
            const items = byResource[resource].map(p => `
                <div class="form-check">
                    <input class="form-check-input ss-perm-check" type="checkbox"
                           id="ss_perm_${escapeAttr(p.permission_id)}" value="${escapeAttr(p.codename)}">
                    <label class="form-check-label small" for="ss_perm_${escapeAttr(p.permission_id)}">
                        <code class="small">${escapeHtml(p.codename)}</code>
                        <span class="text-muted ms-1">— ${escapeHtml(p.display_name)}</span>
                    </label>
                </div>
            `).join('');
            return `
                <div class="mb-2">
                    <div class="fw-semibold text-uppercase small text-muted mb-1">${escapeHtml(resource)}</div>
                    ${items}
                </div>
            `;
        }).join('');

        // Sin scroller propio: el diálogo ya es modal-dialog-scrollable y dos
        // contenedores de scroll anidados atrapaban la rueda del ratón.
        list.innerHTML = `
            <div class="d-flex justify-content-between mb-2">
                <small class="text-muted"><span id="ssSelectedCount">0</span> de ${perms.length} seleccionados</small>
                <div>
                    <button type="button" class="btn btn-link btn-sm p-0 me-2" id="ssSelectAllBtn">Seleccionar todos</button>
                    <button type="button" class="btn btn-link btn-sm p-0" id="ssClearAllBtn">Limpiar</button>
                </div>
            </div>
            <div class="border rounded p-3">${html}</div>
        `;

        loading.classList.add('d-none');
        list.classList.remove('d-none');

        list.querySelectorAll('.ss-perm-check').forEach(cb => {
            cb.addEventListener('change', updateSelectedCount);
        });
        document.getElementById('ssSelectAllBtn').addEventListener('click', () => {
            list.querySelectorAll('.ss-perm-check').forEach(cb => cb.checked = true);
            updateSelectedCount();
        });
        document.getElementById('ssClearAllBtn').addEventListener('click', () => {
            list.querySelectorAll('.ss-perm-check').forEach(cb => cb.checked = false);
            updateSelectedCount();
        });

        updateSelectedCount();
    }

    function updateSelectedCount() {
        const count = document.querySelectorAll('.ss-perm-check:checked').length;
        const el = document.getElementById('ssSelectedCount');
        if (el) el.textContent = count;
    }

    async function submitCreateSocialService(event) {
        event.preventDefault();

        const selected = Array.from(document.querySelectorAll('.ss-perm-check:checked')).map(cb => cb.value);
        if (!selected.length) {
            showFlash('warning', 'Selecciona al menos un permiso.');
            return;
        }

        const ctx = window.SIIAP_USERS_CTX || {};

        // Sin programa la cuenta nace sin alcance y no puede trabajar. El
        // backend también lo rechaza; aquí se avisa antes de enviar nada.
        let programIds = null;
        if (ctx.canDelegateGlobal) {
            programIds = Array.from(document.querySelectorAll('.ss-program-check:checked'))
                .map(cb => Number(cb.value));
            if (!programIds.length) {
                showFlash('warning', 'Selecciona al menos un programa para la cuenta.');
                document.getElementById('ss_programs_list')?.scrollIntoView({ block: 'nearest' });
                return;
            }
        }

        const expiresDate = document.getElementById('ss_expires_at').value;
        let expires_at = null;
        if (expiresDate) {
            expires_at = new Date(expiresDate + 'T23:59:59').toISOString();
        }

        const payload = {
            first_name:       document.getElementById('ss_first_name').value.trim(),
            last_name:        document.getElementById('ss_last_name').value.trim(),
            mother_last_name: document.getElementById('ss_mother_last_name').value.trim() || null,
            email:            document.getElementById('ss_email').value.trim().toLowerCase(),
            permissions:      selected,
            expires_at:       expires_at,
        };

        if (programIds) {
            payload.program_ids = programIds;
        }

        const btn = event.submitter;
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creando…'; }

        try {
            const res = await fetch(`${API_BASE}/social-service`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrf()
                },
                body: JSON.stringify(payload)
            });
            const json = await res.json();
            if (!res.ok) throw new Error(json.error?.message || 'Error al crear usuario');

            if (json.flash) json.flash.forEach(f => showFlash(f.level, f.message));

            bootstrap.Modal.getInstance(document.getElementById('createSocialServiceModal')).hide();
            delegatableCache = null;
            currentFilters = { role: 'social_service' };
            document.getElementById('roleFilter').value = 'social_service';
            loadUsers(1);
        } catch (error) {
            showFlash('danger', error.message);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-lg me-1" aria-hidden="true"></i>Crear y delegar'; }
        }
    }

    // ========================================================================
    // Delegaciones de un usuario (vista + revocación)
    // ========================================================================

    async function loadUserDelegations(userId) {
        const ctx = window.SIIAP_USERS_CTX || {};
        if (!ctx.canViewDelegations) return '';

        try {
            const res = await fetch(`${PERMISSIONS_API}/user/${encodeURIComponent(userId)}`);
            const json = await res.json();
            if (!res.ok) return '';
            const dels = json.data || [];
            if (!dels.length) {
                return `<div class="text-muted small mt-2">Sin permisos delegados directos.</div>`;
            }

            const rows = dels.map(d => {
                const active = d.is_active && !d.is_expired;
                const scope = d.program_name
                    ? d.program_name
                    : (d.program_id ? `Programa #${d.program_id}` : 'Global');
                const codename = d.permission_codename || '';
                const expires = d.expires_at
                    ? `<small class="text-muted ms-2">Vence: ${formatDate(d.expires_at)}</small>`
                    : '';
                const revokeBtn = (active && ctx.canRevokeDelegations)
                    ? `<button type="button" class="btn btn-sm btn-outline-danger tap-target"
                               data-action="revoke-delegation"
                               data-delegation-id="${escapeAttr(d.id)}"
                               data-user-id="${escapeAttr(userId)}"
                               aria-label="Revocar el permiso ${escapeAttr(codename)}"
                               title="Revocar el permiso ${escapeAttr(codename)}">
                           <i class="bi bi-x-circle" aria-hidden="true"></i>
                       </button>`
                    : '';
                return `
                    <tr class="${active ? '' : 'text-muted'}">
                        <th scope="row" class="fw-normal">
                            <code class="small">${escapeHtml(codename)}</code>
                            ${d.permission_display_name ? `<span class="text-muted small d-block">${escapeHtml(d.permission_display_name)}</span>` : ''}
                        </th>
                        <td>${escapeHtml(scope)}</td>
                        <td>
                            ${active
                                ? '<span class="status-badge status-badge--accepted status-badge--sm"><i class="bi bi-check-circle-fill" aria-hidden="true"></i><span>Activa</span></span>'
                                : (d.is_expired
                                    ? '<span class="status-badge status-badge--deliberation status-badge--sm"><i class="bi bi-hourglass-bottom" aria-hidden="true"></i><span>Vencida</span></span>'
                                    : '<span class="status-badge status-badge--deferred status-badge--sm"><i class="bi bi-slash-circle" aria-hidden="true"></i><span>Revocada</span></span>')}
                            ${expires}
                        </td>
                        <td class="text-end">${revokeBtn}</td>
                    </tr>
                `;
            }).join('');

            return `
                <h3 class="h6 mt-3"><i class="bi bi-shield-check me-1" aria-hidden="true"></i>Permisos delegados</h3>
                <div class="siiap-table-wrapper">
                    <table class="table siiap-table table-sm table-hover mb-0">
                        <caption class="visually-hidden">Permisos delegados directamente a este usuario</caption>
                        <thead class="table-light">
                            <tr>
                                <th scope="col">Permiso</th>
                                <th scope="col">Ámbito</th>
                                <th scope="col">Estado</th>
                                <th scope="col" class="text-end"><span class="visually-hidden">Acciones</span></th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            `;
        } catch (error) {
            return `<div class="alert alert-warning small mt-2">No se pudieron cargar las delegaciones.</div>`;
        }
    }

    async function revokeDelegation(upId, userId) {
        const ok = await siiapConfirm({
            type: 'warning',
            title: 'Revocar delegación',
            message: '¿Revocar este permiso delegado? El usuario perderá acceso inmediatamente.',
            confirmLabel: 'Sí, revocar',
        });
        if (!ok) return;

        try {
            const res = await fetch(`${PERMISSIONS_API}/delegation/${encodeURIComponent(upId)}`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCsrf() }
            });
            const json = await res.json();
            if (!res.ok) throw new Error(json.error?.message || 'Error al revocar');
            if (json.flash) json.flash.forEach(f => {
                const level = Array.isArray(f) ? f[1] : f.level;
                const message = Array.isArray(f) ? f[0] : f.message;
                showFlash(level, message);
            });
            if (userId) showUserDetail(userId);
        } catch (error) {
            showFlash('danger', error.message);
        }
    }

    // Aplicar filtros
    function applyFilters() {
        currentFilters = {};
        
        const search = document.getElementById('searchInput').value.trim();
        if (search) currentFilters.search = search;
        
        const role = document.getElementById('roleFilter').value;
        if (role) currentFilters.role = role;
        
        const active = document.getElementById('activeFilter').value;
        if (active !== 'all') currentFilters.active = active;
        
        currentPage = 1;
        loadUsers(1);
    }
    
    // Limpiar filtros
    function clearFilters() {
        document.getElementById('searchInput').value = '';
        document.getElementById('roleFilter').value = '';
        document.getElementById('activeFilter').value = 'true';
        currentFilters = {};
        currentPage = 1;
        loadUsers(1);
    }
    
    // Utilidades
    function getRoleBadgeClass(role) {
        const classes = {
            'applicant': 'bg-info',
            'student': 'bg-primary',
            'graduate': 'bg-success',
            'program_admin': 'bg-warning text-dark',
            'postgraduate_admin': 'bg-danger',
            'social_service': 'bg-secondary'
        };
        return classes[role] || 'bg-secondary';
    }
    
    function getRoleLabel(role) {
        const labels = {
            'applicant': 'Aspirante',
            'student': 'Estudiante',
            'graduate': 'Egresado',
            'program_admin': 'Admin. Programa',
            'postgraduate_admin': 'Admin. Posgrado',
            'social_service': 'Servicio Social'
        };
        return labels[role] || role;
    }
    
    function formatDate(isoString) {
        if (!isoString) return '—';
        if (window.SIIAP && window.SIIAP.formatDate) return window.SIIAP.formatDate(isoString, 'short');
        const date = new Date(isoString);
        return date.toLocaleDateString('es-MX', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }
    
    function showFlash(level, message) {
        window.dispatchEvent(new CustomEvent('flash', {
            detail: { level, message }
        }));
    }
    
    // Event Listeners
    document.addEventListener('DOMContentLoaded', function() {
        // Cargar usuarios inicial
        loadUsers();

        // Delegación de eventos sobre contenedores estables: las filas, la
        // paginación y el detalle se repintan, la escucha se registra una vez.
        document.getElementById('usersTableBody').addEventListener('click', onUsersTableClick);
        document.getElementById('paginationControls').addEventListener('click', onPaginationClick);
        const detailContent = document.getElementById('userDetailContent');
        if (detailContent) detailContent.addEventListener('click', onUserDetailClick);

        // Tiempo real: otro admin creó/modificó/eliminó un usuario → refrescar lista
        window.addEventListener('siiap:admin_user:changed', (e) => {
            const d = e.detail || {};
            const labels = { created: 'creado', updated: 'modificado', deleted: 'eliminado' };
            const verb = labels[d.action] || 'modificado';
            showFlash('info', `Usuario ${verb}: ${d.full_name || d.email || ''}. Actualizando lista...`);
            loadUsers(currentPage);
        });

        // Búsqueda
        document.getElementById('btnSearch').addEventListener('click', applyFilters);
        document.getElementById('searchInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') applyFilters();
        });
        
        // Limpiar filtros
        document.getElementById('btnClearFilters').addEventListener('click', clearFilters);
        // Acción de recuperación del estado vacío.
        const btnClearFiltersEmpty = document.getElementById('btnClearFiltersEmpty');
        if (btnClearFiltersEmpty) btnClearFiltersEmpty.addEventListener('click', clearFilters);
        
        // Formularios
        document.getElementById('editUserForm').addEventListener('submit', saveUserEdit);
        document.getElementById('assignControlNumberForm').addEventListener('submit', saveControlNumber);

        // Crear servicio social
        const btnCreateSS = document.getElementById('btnCreateSocialService');
        if (btnCreateSS) btnCreateSS.addEventListener('click', openCreateSocialService);
        const ssForm = document.getElementById('createSocialServiceForm');
        if (ssForm) ssForm.addEventListener('submit', submitCreateSocialService);
        
        // Validación en tiempo real del número de control
        const controlInput = document.getElementById('control_number');
        if (controlInput) {
            controlInput.addEventListener('input', function() {
                const value = this.value.toUpperCase();
                const feedback = document.getElementById('controlNumberFeedback');
                
                if (!value) {
                    feedback.textContent = '';
                    feedback.className = 'form-text';
                } else if (!/^[MD]\d{0,8}$/.test(value)) {
                    feedback.textContent = 'Debe comenzar con M o D seguido de 8 dígitos';
                    feedback.className = 'form-text text-danger';
                } else if (value.length < 9) {
                    feedback.textContent = `Faltan ${9 - value.length} dígitos`;
                    feedback.className = 'form-text text-warning';
                } else {
                    feedback.textContent = '✓ Formato correcto';
                    feedback.className = 'form-text text-success';
                }
            });
        }
    });
    
    // Exportar funciones al window para usarlas desde el HTML
    window.usersManager = {
        loadUsers,
        showUserDetail,
        editUser,
        resetPassword,
        toggleUserActive,
        assignControlNumber,
        showHistory,
        openCreateSocialService,
        revokeDelegation
    };
})();