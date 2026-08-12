# app/routes/pages/events_pages.py
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app.models.event import Event
from app.services.events_service import EventsService
from app import db

pages_events_public = Blueprint(
    'pages_events_public',
    __name__,
    url_prefix='/events'
)

@pages_events_public.route('/')
@login_required
def list_events():
    """Lista de eventos disponibles para estudiantes"""
    return render_template('events/list.html')

@pages_events_public.route('/<int:event_id>')
@login_required
def view_event(event_id: int):
    """
    Ver detalles de un evento.

    La plantilla imprime `event.title` en el servidor (título de la pestaña,
    migaja de pan y encabezado del hero), así que la página tiene que aplicar
    el MISMO control de objeto que `events_api.get_public_event_detail`, que es
    de donde saca el resto del contenido. Sin él, cualquier sesión iniciada
    leía por id el título de cada borrador, cada evento privado y cada evento
    archivado de la institución, aunque el resto de la página se quedara vacía.

    404 —no 403— cuando la regla dice que no: con un 403 los ids serían
    enumerables (el código de estado ya distingue "existe pero no es tuyo" de
    "no existe") y el título es justo lo que se quiere ocultar.
    """
    event = db.session.get(Event, event_id)
    if not event:
        return render_template('404.html'), 404

    may_view = (
        EventsService.user_may_manage_event(current_user, event)
        or EventsService.user_may_participate_in_event(current_user, event)
        or event.created_by == current_user.id
    )
    if not may_view:
        return render_template('404.html'), 404

    return render_template('events/view.html', event=event)
