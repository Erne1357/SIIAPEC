# app/services/extensions_scope_service.py
"""
Listados de prórrogas acotados al alcance de programas del solicitante.

`ExtensionsService.list_requests` filtra por `program_id` sólo si el llamador
lo pide, así que `GET /api/v1/extensions/requests` y
`GET /api/v1/extensions/requests/for-review` devolvían, sin filtros, TODAS las
solicitudes de la institución —con nombre y correo del solicitante— a cualquier
cuenta con `extensions.api.list_for_review`. Aquí el filtro por programa deja
de ser opcional: se fuerza `ProgramStep.program_id.in_(alcance)` salvo para el
jefe de posgrado, cuyo alcance es global.

Framework-agnostic por contrato: recibe el `User` solicitante como argumento,
igual que `program_scope_service`. Sin `request`, `g` ni `current_user`.
"""

from typing import Optional

from app import db
from app.models.extension_request import ExtensionRequest
from app.models.program_step import ProgramStep
from app.services import program_scope_service as scope_service


def _scoped_query(requester, user_id=None, archive_id=None,
                  status=None, program_id=None):
    """
    Query base de ExtensionRequest unida a ProgramStep y recortada al alcance.

    Returns:
        Query | None — None cuando el solicitante no tiene ningún programa a su
        alcance (p. ej. servicio social sin delegación); el llamador devuelve
        una lista vacía sin tocar la base de datos.
    """
    scope = scope_service.accessible_program_ids(requester)
    if scope is not None and not scope:
        return None

    query = db.session.query(ExtensionRequest).join(
        ProgramStep, ExtensionRequest.program_step_id == ProgramStep.id
    )

    if scope is not None:
        query = query.filter(ProgramStep.program_id.in_(scope))

    if user_id:
        query = query.filter(ExtensionRequest.user_id == user_id)
    if archive_id:
        query = query.filter(ExtensionRequest.archive_id == archive_id)
    if status:
        query = query.filter(ExtensionRequest.status == status)
    if program_id:
        query = query.filter(ProgramStep.program_id == program_id)

    return query.order_by(ExtensionRequest.created_at.desc())


def list_requests_in_scope(requester, user_id=None, archive_id=None,
                           status=None, program_id=None) -> list:
    """
    Solicitudes de prórroga de los programas que el solicitante puede revisar.

    Mismos filtros opcionales que `ExtensionsService.list_requests`; la
    diferencia es que el recorte por programa no es opcional. Pedir un
    `program_id` ajeno devuelve una lista vacía, no un error: es un filtro, no
    un objeto, y no queremos confirmar qué programas tienen prórrogas.
    """
    query = _scoped_query(
        requester,
        user_id=user_id,
        archive_id=archive_id,
        status=status,
        program_id=program_id,
    )
    if query is None:
        return []
    return query.all()


def request_in_scope(requester, request_id) -> bool:
    """
    True si el solicitante puede decidir sobre esa solicitud de prórroga.

    False tanto si la solicitud no existe como si pertenece a un programa fuera
    de su alcance: la ruta responde 404 en ambos casos para no confirmar la
    existencia de solicitudes ajenas.
    """
    program_id = request_program_id(request_id)
    if program_id is None:
        return False
    return scope_service.program_in_scope(requester, program_id)


def request_program_id(request_id) -> Optional[int]:
    """
    Programa dueño de una ExtensionRequest (vía su ProgramStep).

    Returns:
        int | None — None cuando la solicitud no existe.
    """
    if request_id is None:
        return None
    row = (
        db.session.query(ProgramStep.program_id)
        .join(
            ExtensionRequest,
            ExtensionRequest.program_step_id == ProgramStep.id,
        )
        .filter(ExtensionRequest.id == request_id)
        .first()
    )
    return row[0] if row else None
