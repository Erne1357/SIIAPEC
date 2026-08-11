# app/services/acceptance_scope_service.py
"""
Resolución de alcance para los objetos que expone el blueprint de aceptación.

Varias rutas de `/api/v1/acceptance` operan sobre ids opacos —el `doc_id` de un
AcceptanceDocument, el `deferral_id` de un EnrollmentDeferral— que por sí solos
no dicen a qué programa pertenece el objeto. Tener el permiso
`acceptance.api.review_doc` responde QUÉ puede hacer el usuario, nunca SOBRE
QUÉ documento. Este módulo traduce ese id al programa dueño del objeto y aplica
el predicado compartido `program_scope_service.program_in_scope`.

Política de respuesta (deliberada): "no existe" y "existe pero es de otro
programa" devuelven exactamente lo mismo (False). Un coordinador ajeno no debe
poder distinguir ambos casos, porque un 403 sólo en el segundo confirmaría la
existencia del documento de otro programa. Las rutas responden 404 en ambos.

Framework-agnostic por contrato: recibe el `User` solicitante como argumento,
igual que `program_scope_service`. Sin `request`, `g` ni `current_user`.
"""

from typing import Optional

from app import db
from app.models.acceptance_document import AcceptanceDocument
from app.models.enrollment_deferral import EnrollmentDeferral
from app.models.user_program import UserProgram
from app.services import program_scope_service as scope_service


# ─── Resolución objeto → programa ────────────────────────────────────────────

def document_program_id(doc_id) -> Optional[int]:
    """
    Programa dueño de un AcceptanceDocument (vía su UserProgram).

    Returns:
        int | None — None cuando el documento no existe.
    """
    if doc_id is None:
        return None
    row = (
        db.session.query(UserProgram.program_id)
        .join(
            AcceptanceDocument,
            AcceptanceDocument.user_program_id == UserProgram.id,
        )
        .filter(AcceptanceDocument.id == doc_id)
        .first()
    )
    return row[0] if row else None


def deferral_program_id(deferral_id) -> Optional[int]:
    """
    Programa dueño de un EnrollmentDeferral (vía su UserProgram).

    Returns:
        int | None — None cuando el diferimiento no existe.
    """
    if deferral_id is None:
        return None
    row = (
        db.session.query(UserProgram.program_id)
        .join(
            EnrollmentDeferral,
            EnrollmentDeferral.user_program_id == UserProgram.id,
        )
        .filter(EnrollmentDeferral.id == deferral_id)
        .first()
    )
    return row[0] if row else None


# ─── Predicados de alcance ───────────────────────────────────────────────────

def document_in_scope(requester, doc_id) -> bool:
    """
    True si `requester` puede leer y modificar ese AcceptanceDocument.

    False tanto si el documento no existe como si pertenece a un programa fuera
    de su alcance: la ruta responde 404 en ambos casos (ver docstring del
    módulo). El jefe de posgrado (alcance global) siempre pasa.
    """
    program_id = document_program_id(doc_id)
    if program_id is None:
        return False
    return scope_service.program_in_scope(requester, program_id)


def deferral_in_scope(requester, deferral_id) -> bool:
    """
    True si `requester` puede aprobar, rechazar o consultar ese
    EnrollmentDeferral.

    Aprobar un diferimiento borra la tira de materias y la boleta del aspirante
    (`deferral_service._reset_docs_for_deferral`), así que esta comprobación es
    la única barrera entre un coordinador y los documentos de otro programa.

    False tanto si el diferimiento no existe como si es de otro programa.
    """
    program_id = deferral_program_id(deferral_id)
    if program_id is None:
        return False
    return scope_service.program_in_scope(requester, program_id)
