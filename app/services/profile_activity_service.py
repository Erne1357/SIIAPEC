"""
Profile Activity Service.

Aggregates a user's activity feed and upcoming events for the profile page.

Activity feed unifies four sources, ordered by timestamp desc:
  - UserHistory (state changes, document uploads, etc.)
  - Notifications (recent notifications received)
  - Submissions (recent uploads with their status)
  - EventAttendance (event registrations / attendance)

Upcoming events: Event records where the user has an EventAttendance and
event_date is in the future, ordered ascending by event_date.
"""

from datetime import timedelta

from app import db
from app.models.user_history import UserHistory
from app.models.notification import Notification
from app.models.submission import Submission
from app.models.archive import Archive
from app.models.step import Step
from app.models.phase import Phase
from app.models.event import Event, EventAttendance
from app.services import file_access_service
from app.utils.datetime_utils import now_local


def _format_history_item(h: UserHistory) -> dict:
    return {
        'type': 'history',
        'icon': 'bi-clock-history',
        'icon_color': 'primary',
        'title': h.get_action_label(),
        'description': h.details or '',
        'timestamp': h.timestamp.isoformat() if h.timestamp else None,
        'url': None,
    }


def _format_notification_item(n: Notification) -> dict:
    return {
        'type': 'notification',
        'icon': 'bi-bell',
        'icon_color': 'info',
        'title': n.title,
        'description': n.message or '',
        'timestamp': n.created_at.isoformat() if n.created_at else None,
        'url': n.action_url,
    }


def _format_submission_item(s: Submission) -> dict:
    archive_name = s.archive.name if s.archive else 'Documento'
    status_map = {
        'review': ('warning', 'En revisión'),
        'approved': ('success', 'Aprobado'),
        'rejected': ('danger', 'Rechazado'),
        'pending': ('secondary', 'Pendiente'),
    }
    color, label = status_map.get(s.status, ('secondary', s.status))
    return {
        'type': 'submission',
        'icon': 'bi-file-earmark-arrow-up',
        'icon_color': color,
        'title': f'Documento subido: {archive_name}',
        'description': f'Estado: {label}',
        'timestamp': s.upload_date.isoformat() if s.upload_date else None,
        # URL opaco: nombra la fila, no la ruta en disco.
        'url': file_access_service.submission_file_url(s),
    }


def _format_attendance_item(att: EventAttendance) -> dict:
    ev = att.event
    title = f'Inscripción a evento: {ev.title}' if ev else 'Inscripción a evento'
    if att.status == 'attended':
        title = f'Asistencia confirmada: {ev.title}' if ev else 'Asistencia confirmada'
    return {
        'type': 'event',
        'icon': 'bi-calendar-event',
        'icon_color': 'success',
        'title': title,
        'description': ev.location or '' if ev else '',
        'timestamp': (
            att.attended_at.isoformat() if att.attended_at
            else att.registered_at.isoformat() if att.registered_at
            else None
        ),
        'url': f'/events/{ev.id}' if ev else None,
    }


def get_recent_activity(user_id: int, limit: int = 6) -> list:
    """
    Returns a unified, sorted list of recent activity items for a user.
    """
    histories = (
        UserHistory.query
        .filter_by(user_id=user_id)
        .order_by(UserHistory.timestamp.desc())
        .limit(limit)
        .all()
    )
    notifications = (
        Notification.query
        .filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )
    submissions = (
        Submission.query
        .filter_by(user_id=user_id)
        .order_by(Submission.upload_date.desc())
        .limit(limit)
        .all()
    )
    attendances = (
        EventAttendance.query
        .filter_by(user_id=user_id)
        .order_by(EventAttendance.registered_at.desc())
        .limit(limit)
        .all()
    )

    items = []
    items.extend(_format_history_item(h) for h in histories)
    items.extend(_format_notification_item(n) for n in notifications)
    items.extend(_format_submission_item(s) for s in submissions)
    items.extend(_format_attendance_item(a) for a in attendances)

    items = [i for i in items if i.get('timestamp')]
    items.sort(key=lambda i: i['timestamp'], reverse=True)
    return items[:limit]


def get_upcoming_events(user_id: int, limit: int = 5) -> list:
    """
    Returns events the user is registered for whose event_date is in the future.
    """
    now = now_local()

    rows = (
        db.session.query(EventAttendance, Event)
        .join(Event, EventAttendance.event_id == Event.id)
        .filter(
            EventAttendance.user_id == user_id,
            EventAttendance.status.in_(['registered', 'attended']),
            Event.event_date.isnot(None),
            Event.event_date >= now,
            Event.status.in_(['published', 'ongoing']),
        )
        .order_by(Event.event_date.asc())
        .limit(limit)
        .all()
    )

    return [
        {
            'event_id': ev.id,
            'title': ev.title,
            'description': ev.description,
            'location': ev.location,
            'type': ev.type,
            'event_date': ev.event_date.isoformat() if ev.event_date else None,
            'event_end_date': ev.event_end_date.isoformat() if ev.event_end_date else None,
            'status': ev.status,
            'attendance_status': att.status,
            'url': f'/events/{ev.id}',
        }
        for att, ev in rows
    ]


def _phase_name_for_submission(s: Submission) -> str:
    """
    Returns the phase key (admission/permanence/conclusion) for a submission
    via Submission → Archive → Step → Phase.name.
    """
    if not s.archive:
        return 'unknown'
    step = s.archive.step
    if not step or not step.phase:
        return 'unknown'
    return (step.phase.name or '').lower()


def get_user_documents_grouped(user_id: int) -> dict:
    """
    Returns the user's submissions grouped by phase. Permanence submissions are
    further grouped by semester_number.

    Shape:
      {
        'admission':   [submission_dict, ...],
        'permanence':  {'1': [...], '2': [...]},
        'conclusion':  [submission_dict, ...],
        'other':       [...]
      }
    """
    submissions = (
        Submission.query
        .filter_by(user_id=user_id)
        .order_by(Submission.upload_date.desc())
        .all()
    )

    grouped = {
        'admission': [],
        'permanence': {},
        'conclusion': [],
        'other': [],
    }

    for s in submissions:
        phase = _phase_name_for_submission(s)
        item = s.to_dict()
        item['archive_name'] = s.archive.name if s.archive else 'Documento'
        # URL opaco: nombra la fila, no la ruta en disco.
        item['file_url'] = file_access_service.submission_file_url(s)

        if phase == 'admission':
            grouped['admission'].append(item)
        elif phase == 'permanence':
            sem_key = str(s.semester) if s.semester else 'sin_semestre'
            grouped['permanence'].setdefault(sem_key, []).append(item)
        elif phase == 'conclusion':
            grouped['conclusion'].append(item)
        else:
            grouped['other'].append(item)

    return grouped
