/**
 * socket-client.js
 * Gestor global de la conexión Socket.IO para SIIAP.
 *
 * Se carga en base.html DESPUÉS de socket.io.min.js y ANTES de notifications.js.
 * Expone `window.siiapSocket` para que los módulos de página escuchen eventos.
 *
 * Eventos que emite al window (custom events):
 *   siiap:notification:new     → { notification: {...} }
 *   siiap:deliberation:updated → { user_id, user_name, program_id, status }
 *   siiap:email:queue_update   → { pending, failed }
 *
 * TIPO DE LOS IDENTIFICADORES EN TODOS LOS PAYLOADS DE ABAJO
 * ----------------------------------------------------------
 * `user_id`, `applicant_id`, `submission_id`, `archive_id` y `requested_by`
 * nombran una fila de User / Submission / Archive: viajan como el UUID
 * PÚBLICO en cadena, exactamente igual que en las respuestas REST. Cualquier
 * comparación con un valor sacado de un `data-*` debe hacerse como cadena;
 * `parseInt` sobre uno de ellos da NaN y la comparación es siempre falsa.
 *
 * El resto de ids (`program_id`, `event_id`, `slot_id`, `appointment_id`,
 * `extension_request_id`, `notification.id`…) siguen siendo enteros: esos
 * modelos no tienen identificador público.
 *
 * La SALA (`user:{id}`) sigue usando el id interno, pero eso no es público:
 * el servidor la resuelve desde el handshake y el cliente nunca la nombra.
 */

(function () {
    'use strict';

    // Solo conectar si el usuario está autenticado (el badge de notificaciones existe)
    const isBellPresent = !!document.getElementById('notificationBell');
    if (!isBellPresent) return;

    // ─── Conectar ────────────────────────────────────────────────────────────
    const socket = io({
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionAttempts: 10,
        reconnectionDelay: 2000,
        reconnectionDelayMax: 30000,
    });

    window.siiapSocket = socket;

    // ─── Gestión de conexión ─────────────────────────────────────────────────
    socket.on('connect', () => {
        console.debug('[WS] Conectado al servidor Socket.IO');
    });

    socket.on('disconnect', (reason) => {
        console.debug('[WS] Desconectado:', reason);
    });

    socket.on('connect_error', (err) => {
        console.warn('[WS] Error de conexión:', err.message);
    });

    // ─── Eventos del servidor → re-emit como CustomEvents del window ─────────

    /**
     * notification:new
     * Emitido por el servidor cada vez que se crea una notificación para el usuario.
     * Payload: { notification: { id, type, title, message, action_url, ... } }
     */
    socket.on('notification:new', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:notification:new', { detail: data }));
    });

    /**
     * deliberation:updated
     * Emitido cuando un coordinador acepta/rechaza a un aspirante.
     * Payload: { user_id, user_name, program_id, status }
     */
    socket.on('deliberation:updated', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:deliberation:updated', { detail: data }));
    });

    /**
     * email:queue_update
     * Emitido cuando cambia el estado de la cola de correos.
     * Payload: { pending, failed }
     */
    socket.on('email:queue_update', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:email:queue_update', { detail: data }));
    });

    /**
     * submission:reviewed
     * Emitido cuando un admin aprueba/rechaza un documento del aspirante.
     * Payload: { user_id, submission_id, archive_id, status }
     */
    socket.on('submission:reviewed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:submission:reviewed', { detail: data }));
    });

    /**
     * submission:new
     * Emitido cuando un estudiante/aspirante sube un documento nuevo.
     * Payload: { user_id, submission_id, archive_name, program_id, context? }
     */
    socket.on('submission:new', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:submission:new', { detail: data }));
    });

    /**
     * acceptance:updated
     * Emitido cuando hay cambios en documentos de aceptación.
     * Payload: { user_id, program_id, action }
     */
    socket.on('acceptance:updated', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:acceptance:updated', { detail: data }));
    });

    /**
     * admission:status_changed
     * Emitido al aspirante cuando su admission_status cambia:
     * interview_completed, deliberation, accepted, rejected, in_progress (reset).
     * Payload: { user_id, program_id, new_status, decision_notes?, rejection_type?, correction_required? }
     */
    socket.on('admission:status_changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:admission:status_changed', { detail: data }));
    });

    /**
     * permanence:status_changed
     * Emitido al estudiante cuando cambia su estado de permanencia:
     * semester_confirmed, doc_reviewed, leave_decided.
     * Payload: { user_id, action, ...campos específicos de la acción }
     */
    socket.on('permanence:status_changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:permanence:status_changed', { detail: data }));
    });

    /**
     * extension:decided
     * Emitido al solicitante cuando el coordinador decide sobre su solicitud de prórroga.
     * Payload: { user_id, extension_request_id, archive_id, status, granted_until? }
     */
    socket.on('extension:decided', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:extension:decided', { detail: data }));
    });

    /**
     * academic_period:changed
     * Emitido cuando un postgraduate_admin activa o desactiva un periodo académico.
     * Llega a role:postgraduate_admin y role:coordinator.
     * Payload: { period_id, code, name, action: 'activated' | 'deactivated' }
     */
    socket.on('academic_period:changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:academic_period:changed', { detail: data }));
    });

    /**
     * admin_user:changed
     * Emitido cuando se crea/modifica/elimina un usuario desde admin.
     * Llega a role:postgraduate_admin (alcance global) y, sólo si la cuenta
     * afectada es aspirante/estudiante, a coordinator:program:{pid} de sus
     * programas. Una cuenta de personal no es alumno de nadie: su nombre y
     * correo no salen del alcance global.
     * Payload: { action: 'created' | 'updated' | 'deleted', user_id, role?, email?, full_name? }
     */
    socket.on('admin_user:changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:admin_user:changed', { detail: data }));
    });

    /**
     * role_permission:changed
     * Emitido cuando el jefe de posgrado agrega/revierte un override de permiso de rol.
     * Payload: { action: 'grant' | 'revert', role_id, role_name, codename }
     */
    socket.on('role_permission:changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:role_permission:changed', { detail: data }));
    });

    /**
     * appointment:changed
     * Emitido cuando una cita se reserva o cancela (por aspirante o coordinador).
     * Llega al dueño de la cita (user:{applicant_id}) y a los coordinadores con
     * alcance sobre el programa del evento; un evento institucional sólo llega
     * al alcance global, igual que su regla de gestión.
     * Payload: { action: 'booked' | 'cancelled', appointment_id, event_id, slot_id, applicant_id, cancelled_by_coordinator? }
     */
    socket.on('appointment:changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:appointment:changed', { detail: data }));
    });

    /**
     * appointment:change_requested
     * Emitido cuando un aspirante solicita un cambio de horario.
     * Payload: { change_request_id, appointment_id, requested_by }
     */
    socket.on('appointment:change_requested', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:appointment:change_requested', { detail: data }));
    });

    /**
     * event:changed
     * Emitido cuando un admin crea, edita, concluye, archiva o elimina un evento.
     *
     * NO es un broadcast global (lo era, y filtraba el título de un evento en
     * borrador o privado a todo el que estuviera conectado). El servidor
     * reparte por audiencia, `app/sockets/emitters.py::emit_event_change`:
     *   - gestores del programa del evento → payload completo, siempre;
     *   - participantes → payload completo sólo si el evento está publicado,
     *     es público y visible para estudiantes;
     *   - al archivar/concluir, los participantes reciben una señal reducida
     *     { action, event_id } SIN título, suficiente para recargar la lista;
     *   - al eliminar, los participantes no reciben nada (la fila ya no existe
     *     y no se puede comprobar si era pública): la página pública se entera
     *     en el siguiente fetch.
     * Por eso todo consumidor debe tolerar un payload sin `title` ni `program_id`.
     *
     * Payload: { action: 'created' | 'updated' | 'concluded' | 'archived' |
     *            'unarchived' | 'deleted', event_id, program_id?, title? }
     */
    socket.on('event:changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:event:changed', { detail: data }));
    });

    /**
     * invitations:count_changed
     * Emitido al usuario cuando cambia su contador de invitaciones pendientes
     * (recibe nueva, responde, cancelan, etc.).
     * Payload: { count: number }
     */
    socket.on('invitations:count_changed', (data) => {
        window.dispatchEvent(new CustomEvent('siiap:invitations:count_changed', { detail: data }));
    });

})();
