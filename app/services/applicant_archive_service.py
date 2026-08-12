# app/services/applicant_archive_service.py
"""
Respaldo ZIP previo a la purga física de archivos de aspirantes/estudiantes.

Política unificada: ningún `os.remove` ocurre sin (1) ZIP descargado y
(2) confirmación explícita post-descarga.

Categorías soportadas (`purge_type`):
  - admission_expired_with_files: UserProgram con admission_status='expired'
    y submissions con file_path en disco.
  - admission_delta3_plus: aspirantes con periodo de inscripción ≥ 2 periodos
    cerrados atrás (equivalente al cleanup task original).
  - retention_policy: enrolled/rejected viejos según RetentionPolicy.keep_years.
  - transition_snapshot: respaldo preventivo emitido durante /transition/execute
    (no purga real; se queda en 'downloaded' como terminal funcional).

Flujo:
  pending_download → downloaded → purged
  cualquier estado intermedio → cancelled (admin) | expired (sweep > 7d)
"""

import csv
import hashlib
import io
import json
import logging
import os
import shutil
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Optional

from app import db
from app.services import public_id_service
from app.config import Config
from app.models.purge_run import PurgeRun, PURGE_TYPES
from app.models.submission import Submission
from app.models.user_program import UserProgram
from app.models.academic_period import AcademicPeriod
from app.models.user_history import UserHistory
from app.models.retention_policy import RetentionPolicy
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class PurgeError(Exception):
    """Error base para operaciones de purga."""


class PurgeRunNotFound(PurgeError):
    pass


class InvalidPurgeState(PurgeError):
    pass


class InvalidPurgeType(PurgeError):
    pass


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKUPS_DIR = Config.INSTANCE_DIR / 'backups' / 'purge'


def _ensure_backups_dir() -> Path:
    _BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    return _BACKUPS_DIR


def _archive_path_for(run_id: str) -> Path:
    return _ensure_backups_dir() / f'{run_id}.zip'


# ---------------------------------------------------------------------------
# Candidate listing
# ---------------------------------------------------------------------------

def _periods_elapsed_since(period_code: str) -> int:
    """Cuenta cuántos periodos cerraron su admisión después de period_code."""
    from datetime import datetime
    return (
        AcademicPeriod.query
        .filter(
            AcademicPeriod.code > period_code,
            AcademicPeriod.admission_end_date < datetime.utcnow(),
        )
        .count()
    )


def _files_count_for(user_program: UserProgram) -> tuple:
    """Devuelve (count, total_bytes) de archivos físicamente en disco para este UP."""
    subs = Submission.query.filter_by(
        user_id=user_program.user_id,
    ).all()
    # Filtrar a los del programa via program_step
    from app.models.program_step import ProgramStep
    program_step_ids = {
        ps.id for ps in ProgramStep.query.filter_by(
            program_id=user_program.program_id
        ).all()
    }
    count = 0
    total = 0
    for s in subs:
        if s.program_step_id not in program_step_ids:
            continue
        if not s.file_path:
            continue
        full = Path(Config.UPLOAD_FOLDER) / s.file_path
        if full.exists():
            count += 1
            total += full.stat().st_size
    return count, total


def list_candidates(category: str) -> list:
    """
    Devuelve lista de aspirantes/estudiantes candidatos a purga según categoría.

    Cada item: {
        'user_program_id', 'user_id', 'name', 'email', 'program_name',
        'admission_status', 'admission_period', 'periods_elapsed',
        'files_count', 'total_size_bytes',
    }
    """
    if category not in (
        'admission_expired_with_files',
        'admission_delta3_plus',
        'retention_policy',
    ):
        raise InvalidPurgeType(f'Categoría desconocida: {category}')

    sweep_expired_runs()  # housekeeping

    if category == 'admission_expired_with_files':
        ups = UserProgram.query.filter(
            UserProgram.admission_status == 'expired',
        ).all()
        result = []
        for up in ups:
            count, total = _files_count_for(up)
            if count == 0:
                continue
            ap = (
                AcademicPeriod.query.get(up.admission_period_id)
                if up.admission_period_id else None
            )
            result.append({
                'user_program_id': up.id,
                # Public handle: `user_program_id` sigue siendo entero porque es
                # lo que la consola devuelve para lanzar la purga.
                'user_id': public_id_service.user_uuid(up.user_id),
                'name': _full_name(up.user),
                'email': up.user.email,
                'program_name': up.program.name if up.program else None,
                'admission_status': up.admission_status,
                'admission_period': ap.name if ap else None,
                'periods_elapsed': None,
                'files_count': count,
                'total_size_bytes': total,
            })
        return result

    if category == 'admission_delta3_plus':
        candidates = (
            UserProgram.query
            .filter(
                UserProgram.admission_status.in_(
                    ['in_progress', 'interview_completed', 'deliberation']
                ),
                UserProgram.admission_period_id.isnot(None),
            )
            .all()
        )
        result = []
        for up in candidates:
            ap = AcademicPeriod.query.get(up.admission_period_id)
            if not ap:
                continue
            elapsed = _periods_elapsed_since(ap.code)
            if elapsed < 2:
                continue
            count, total = _files_count_for(up)
            result.append({
                'user_program_id': up.id,
                # Public handle: `user_program_id` sigue siendo entero porque es
                # lo que la consola devuelve para lanzar la purga.
                'user_id': public_id_service.user_uuid(up.user_id),
                'name': _full_name(up.user),
                'email': up.user.email,
                'program_name': up.program.name if up.program else None,
                'admission_status': up.admission_status,
                'admission_period': ap.name,
                'periods_elapsed': elapsed,
                'files_count': count,
                'total_size_bytes': total,
            })
        return result

    # retention_policy
    from datetime import datetime
    policies = RetentionPolicy.query.filter_by(keep_forever=False).all()
    result = []
    for policy in policies:
        if not policy.keep_years:
            continue
        cutoff = datetime.utcnow() - timedelta(days=policy.keep_years * 365)
        ups = UserProgram.query.filter(
            UserProgram.admission_status == policy.apply_after,
            UserProgram.updated_at < cutoff,
        ).all()
        for up in ups:
            subs = Submission.query.filter_by(
                user_id=up.user_id,
                archive_id=policy.archive_id,
            ).all()
            count = 0
            total = 0
            for s in subs:
                if not s.file_path:
                    continue
                full = Path(Config.UPLOAD_FOLDER) / s.file_path
                if full.exists():
                    count += 1
                    total += full.stat().st_size
            if count == 0:
                continue
            result.append({
                'user_program_id': up.id,
                # Public handle: `user_program_id` sigue siendo entero porque es
                # lo que la consola devuelve para lanzar la purga.
                'user_id': public_id_service.user_uuid(up.user_id),
                'name': _full_name(up.user),
                'email': up.user.email,
                'program_name': up.program.name if up.program else None,
                'admission_status': up.admission_status,
                'admission_period': None,
                'periods_elapsed': None,
                'files_count': count,
                'total_size_bytes': total,
                'policy': {
                    'archive_id': public_id_service.archive_uuid(policy.archive_id),
                    'keep_years': policy.keep_years,
                    'apply_after': policy.apply_after,
                },
            })
    return result


def _full_name(user) -> str:
    return f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip()


# ---------------------------------------------------------------------------
# Archive build
# ---------------------------------------------------------------------------

def _serialize_user_program(up: UserProgram) -> dict:
    user = up.user
    submissions = (
        Submission.query.filter_by(user_id=up.user_id).all()
    )
    sub_rows = [
        {
            'id': s.id,
            'archive_id': s.archive_id,
            'program_step_id': s.program_step_id,
            'file_path': s.file_path,
            'status': s.status,
            'upload_date': s.upload_date.isoformat() if s.upload_date else None,
            'review_date': s.review_date.isoformat() if s.review_date else None,
            'reviewer_id': s.reviewer_id,
            'reviewer_comment': s.reviewer_comment,
            'semester': s.semester,
            'academic_period_id': s.academic_period_id,
            'document_deadline_id': s.document_deadline_id,
        }
        for s in submissions
    ]
    history_rows = [
        h.to_dict() for h in (
            UserHistory.query.filter_by(user_id=up.user_id).all()
        )
    ]
    return {
        'user_program_id': up.id,
        'user': {
            'id': user.id,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'mother_last_name': user.mother_last_name,
            'email': user.email,
            'control_number': getattr(user, 'control_number', None),
        },
        'user_program': {
            'admission_status': up.admission_status,
            'admission_period_id': up.admission_period_id,
            'program_id': up.program_id,
            'current_semester': up.current_semester,
            'has_conacyt_scholarship': getattr(up, 'has_conacyt_scholarship', None),
            'updated_at': up.updated_at.isoformat() if getattr(up, 'updated_at', None) else None,
        },
        'program': {
            'id': up.program.id,
            'name': up.program.name,
        } if up.program else None,
        'submissions': sub_rows,
        'history': history_rows,
    }


def _walk_user_files(up: UserProgram) -> list:
    """
    Devuelve [(rel_path_in_zip, abs_path_on_disk, submission_id), ...] para
    todos los archivos físicos del UserProgram que existen en disco.
    """
    from app.models.program_step import ProgramStep
    program_step_ids = {
        ps.id for ps in ProgramStep.query.filter_by(
            program_id=up.program_id
        ).all()
    }
    out = []
    subs = Submission.query.filter_by(user_id=up.user_id).all()
    for s in subs:
        if s.program_step_id not in program_step_ids:
            continue
        if not s.file_path:
            continue
        abs_path = Path(Config.UPLOAD_FOLDER) / s.file_path
        if not abs_path.exists():
            continue
        rel = f'documents/user_{up.user_id}/{Path(s.file_path).name}'
        out.append((rel, abs_path, s.id))
    return out


def _build_summary_csv(items: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'user_program_id', 'user_id', 'full_name', 'email',
        'program', 'admission_status', 'admission_period',
        'files_count', 'total_size_bytes',
    ])
    for it in items:
        writer.writerow([
            it.get('user_program_id'),
            it.get('user_id'),
            it.get('name'),
            it.get('email'),
            it.get('program_name'),
            it.get('admission_status'),
            it.get('admission_period'),
            it.get('files_count'),
            it.get('total_size_bytes'),
        ])
    return buf.getvalue()


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(64 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def create_purge_run(
    user_program_ids: Iterable[int],
    purge_type: str,
    initiated_by_id: int,
    program_id: Optional[int] = None,
    source_period_id: Optional[int] = None,
    target_period_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> PurgeRun:
    """
    Crea un PurgeRun con su ZIP en instance/backups/purge/<run_id>.zip.

    Estado inicial: 'pending_download'. expires_at = now + 7d.
    """
    if purge_type not in PURGE_TYPES:
        raise InvalidPurgeType(f'purge_type inválido: {purge_type}')

    ups = (
        UserProgram.query
        .filter(UserProgram.id.in_(list(user_program_ids)))
        .all()
    )
    if not ups:
        raise PurgeError('No se encontraron UserProgram para los IDs proporcionados')

    run_id = str(uuid.uuid4())
    archive_path = _archive_path_for(run_id)
    tmp_path = archive_path.with_suffix('.zip.tmp')

    # Construir lista de items (similar al list_candidates pero por id)
    #
    # OJO — aquí `user_id` es el ENTERO, a diferencia de `list_candidates`, que
    # publica el UUID público. Esto no es una omisión:
    #
    #   * el ZIP es un artefacto de retención OFFLINE que sobrevive a la fila,
    #     y sus carpetas se llaman `documents/user_<entero>/` (decisión del
    #     dueño: los archivos en disco no se mueven ni se renombran). Si el
    #     manifiesto y el CSV hablaran UUID, no casarían con las carpetas del
    #     propio ZIP y quien lo abra dentro de cinco años no podría relacionarlos;
    #   * es un REGISTRO PERSISTIDO, y los registros persistidos conservan el id
    #     interno igual que el historial, los logs y los argumentos de Celery.
    #
    # La legibilidad humana la dan `name` y `email`, que ya viajan en la misma
    # fila. Lo que sale por la API en vivo (`list_candidates`) sí es el UUID.
    items_meta = []
    for up in ups:
        count, total = _files_count_for(up)
        ap = (
            AcademicPeriod.query.get(up.admission_period_id)
            if up.admission_period_id else None
        )
        items_meta.append({
            'user_program_id': up.id,
            'user_id': up.user_id,
            'name': _full_name(up.user),
            'email': up.user.email,
            'program_name': up.program.name if up.program else None,
            'admission_status': up.admission_status,
            'admission_period': ap.name if ap else None,
            'files_count': count,
            'total_size_bytes': total,
        })

    manifest = {
        'run_id': run_id,
        'purge_type': purge_type,
        'initiated_at': now_local().isoformat(),
        'initiated_by_id': initiated_by_id,
        'program_id': program_id,
        'source_period_id': source_period_id,
        'target_period_id': target_period_id,
        'item_count': len(ups),
        'items': [_serialize_user_program(up) for up in ups],
    }

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                'manifest.json',
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                'summary.csv',
                _build_summary_csv(items_meta),
            )
            for up in ups:
                for rel, abs_path, _sub_id in _walk_user_files(up):
                    zf.write(abs_path, arcname=rel)

        os.replace(tmp_path, archive_path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    size_bytes = archive_path.stat().st_size
    sha = _sha256_of_file(archive_path)

    run = PurgeRun(
        run_id=run_id,
        initiated_by=initiated_by_id,
        purge_type=purge_type,
        program_id=program_id,
        source_period_id=source_period_id,
        target_period_id=target_period_id,
        target_user_program_ids=[up.id for up in ups],
        archive_path=str(archive_path),
        archive_size_bytes=size_bytes,
        archive_sha256=sha,
        status='pending_download',
        notes=notes,
    )
    db.session.add(run)

    for up in ups:
        UserHistoryService.log_action(
            user_id=up.user_id,
            admin_id=initiated_by_id,
            action='purge_run_created',
            details=(
                f'Respaldo ZIP generado (run_id={run_id}, tipo={purge_type}). '
                f'Archivos pendientes de purga física hasta confirmación.'
            ),
        )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        if archive_path.exists():
            try:
                archive_path.unlink()
            except OSError:
                pass
        raise

    return run


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def get_run(run_id: str) -> PurgeRun:
    run = PurgeRun.query.filter_by(run_id=run_id).first()
    if not run:
        raise PurgeRunNotFound(f'PurgeRun {run_id} no encontrado')
    return run


def stream_archive(run_id: str, downloader_user_id: int):
    """
    Devuelve (path, size, on_complete_callback) para que la capa HTTP
    haga send_file/stream + register on_close.

    Marca downloaded_at solo cuando el callback se invoca (stream completo).
    """
    run = get_run(run_id)
    if not run.can_download():
        raise InvalidPurgeState(
            f'No se puede descargar PurgeRun en estado {run.status}'
        )
    path = Path(run.archive_path) if run.archive_path else None
    if not path or not path.exists():
        raise PurgeError('Archivo ZIP no disponible en disco')

    def on_complete():
        # Llamado por response.call_on_close — solo si la respuesta cerró ok.
        try:
            run_local = PurgeRun.query.filter_by(run_id=run_id).first()
            if not run_local:
                return
            if run_local.status == 'pending_download':
                run_local.status = 'downloaded'
            run_local.archive_downloaded_at = now_local()
            UserHistoryService.log_action(
                user_id=downloader_user_id,
                admin_id=downloader_user_id,
                action='purge_run_downloaded',
                details=f'ZIP descargado (run_id={run_id})',
            )
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f'Error marcando download de {run_id}: {e}')

    return path, run.archive_size_bytes, on_complete


# ---------------------------------------------------------------------------
# Confirm purge
# ---------------------------------------------------------------------------

def confirm_purge(run_id: str, confirmer_user_id: int) -> dict:
    """
    Aplica purga física. Solo procede si status='downloaded'.

    Comportamiento por tipo:
      - admission_expired_with_files / admission_delta3_plus:
          BORRA Submissions enteras del DB (delete row) + archivos físicos.
          El aspirante queda 'expired' sin rastro de docs aprobados — si
          vuelve a postular, arranca limpio (sin "ya estaba aprobado X").
      - retention_policy:
          Borra archivo físico y la fila de Submission del DB. El usuario
          (egresado/rechazado) ya cumplió su retención, no se reabre proceso.

    Borra el ZIP local. Marca run.status='purged'.
    """
    run = get_run(run_id)
    if not run.can_confirm_purge():
        raise InvalidPurgeState(
            f'No se puede purgar PurgeRun en estado {run.status} '
            f'(tipo {run.purge_type})'
        )

    deleted_files = 0
    purged_subs = 0
    user_program_ids = list(run.target_user_program_ids or [])

    try:
        ups = (
            UserProgram.query
            .filter(UserProgram.id.in_(user_program_ids))
            .all()
        )

        for up in ups:
            from app.models.program_step import ProgramStep

            if run.purge_type == 'retention_policy':
                applicable_archive_ids = {
                    p.archive_id for p in RetentionPolicy.query.filter_by(
                        keep_forever=False,
                        apply_after=up.admission_status,
                    ).all() if p.archive_id
                }
                subs = Submission.query.filter(
                    Submission.user_id == up.user_id,
                    Submission.archive_id.in_(applicable_archive_ids),
                ).all() if applicable_archive_ids else []
            else:
                program_step_ids = {
                    ps.id for ps in ProgramStep.query.filter_by(
                        program_id=up.program_id
                    ).all()
                }
                subs = Submission.query.filter(
                    Submission.user_id == up.user_id,
                    Submission.program_step_id.in_(program_step_ids),
                ).all() if program_step_ids else []

            for s in subs:
                if s.file_path:
                    full = Path(Config.UPLOAD_FOLDER) / s.file_path
                    if full.exists():
                        try:
                            full.unlink()
                            deleted_files += 1
                        except OSError as e:
                            logger.warning(
                                f'No se pudo borrar {full}: {e} (continuando)'
                            )
                # Borrado completo de la fila: el respaldo ZIP es el único
                # registro auditable del documento.
                db.session.delete(s)
                purged_subs += 1

            if run.purge_type in (
                'admission_expired_with_files', 'admission_delta3_plus'
            ):
                if up.admission_status != 'expired':
                    up.admission_status = 'expired'
                    up.updated_at = now_local()

            UserHistoryService.log_action(
                user_id=up.user_id,
                admin_id=confirmer_user_id,
                action='purge_run_confirmed',
                details=(
                    f'Purga física aplicada (run_id={run.run_id}, '
                    f'tipo={run.purge_type})'
                ),
            )

        run.status = 'purged'
        run.purged_at = now_local()

        # Borrar ZIP del disco — ya está en el cliente.
        if run.archive_path:
            zp = Path(run.archive_path)
            if zp.exists():
                try:
                    zp.unlink()
                except OSError as e:
                    logger.warning(f'No se pudo borrar ZIP {zp}: {e}')
            run.archive_path = None

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        'run_id': run.run_id,
        'deleted_files': deleted_files,
        'purged_submissions': purged_subs,
        'user_programs_affected': len(user_program_ids),
    }


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def cancel_purge_run(run_id: str, canceller_user_id: int) -> None:
    run = get_run(run_id)
    if run.status not in ('pending_download', 'downloaded'):
        raise InvalidPurgeState(
            f'No se puede cancelar PurgeRun en estado {run.status}'
        )
    if run.archive_path:
        zp = Path(run.archive_path)
        if zp.exists():
            try:
                zp.unlink()
            except OSError as e:
                logger.warning(f'No se pudo borrar ZIP {zp}: {e}')
        run.archive_path = None
    run.status = 'cancelled'

    for upid in (run.target_user_program_ids or []):
        up = UserProgram.query.get(upid)
        if not up:
            continue
        UserHistoryService.log_action(
            user_id=up.user_id,
            admin_id=canceller_user_id,
            action='purge_run_cancelled',
            details=f'Respaldo {run.run_id} cancelado por admin',
        )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ---------------------------------------------------------------------------
# Sweep expired
# ---------------------------------------------------------------------------

def sweep_expired_runs() -> int:
    """Marca como 'expired' los runs con expires_at<now y status no terminal."""
    now = now_local()
    pending = PurgeRun.query.filter(
        PurgeRun.status.in_(['pending_download', 'downloaded']),
        PurgeRun.expires_at < now,
    ).all()
    count = 0
    for run in pending:
        if run.archive_path:
            zp = Path(run.archive_path)
            if zp.exists():
                try:
                    zp.unlink()
                except OSError:
                    pass
            run.archive_path = None
        run.status = 'expired'
        count += 1
    if count:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return count


# ---------------------------------------------------------------------------
# Listing runs (UI)
# ---------------------------------------------------------------------------

def list_runs(limit: int = 50) -> list:
    runs = (
        PurgeRun.query
        .order_by(PurgeRun.initiated_at.desc())
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in runs]
