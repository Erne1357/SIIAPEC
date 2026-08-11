"""
Comandos CLI de Flask para administracion del sistema SIIAP.
"""

import os
import glob
import shutil
import click
from flask import current_app
from flask.cli import with_appcontext


def register_cli(app):
    """Registra los comandos CLI en la aplicacion Flask."""

    @app.cli.command('seed-test-data')
    @click.option('--confirm', is_flag=True, help='Confirmar la ejecucion sin prompt')
    @with_appcontext
    def seed_test_data(confirm):
        """
        Ejecuta los SQL de prueba en database/DML/pruebas/ y copia archivos
        de ejemplo para simular documentos subidos.

        Uso:
            flask seed-test-data
            flask seed-test-data --confirm
        """
        from app import db

        pruebas_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'database', 'DML', 'pruebas'
        )

        if not os.path.isdir(pruebas_dir):
            click.echo(click.style(f'No se encontro el directorio: {pruebas_dir}', fg='red'))
            return

        # Listar archivos SQL ordenados
        sql_files = sorted(glob.glob(os.path.join(pruebas_dir, '*.sql')))
        if not sql_files:
            click.echo(click.style('No hay archivos SQL en el directorio de pruebas.', fg='yellow'))
            return

        click.echo(click.style('\n=== SEED TEST DATA ===', fg='cyan', bold=True))
        click.echo(f'Directorio: {pruebas_dir}')
        click.echo(f'Archivos encontrados: {len(sql_files)}')
        for f in sql_files:
            click.echo(f'  - {os.path.basename(f)}')

        if not confirm:
            if not click.confirm('\nEsto insertara datos de prueba en la base de datos. Continuar?'):
                click.echo('Cancelado.')
                return

        # Ejecutar cada archivo SQL
        errors = []
        for sql_file in sql_files:
            filename = os.path.basename(sql_file)
            click.echo(f'\nEjecutando {click.style(filename, fg="blue")}...')
            try:
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_content = f.read()

                db.session.execute(db.text(sql_content))
                db.session.commit()
                click.echo(click.style('  OK', fg='green'))
            except Exception as e:
                db.session.rollback()
                errors.append((filename, str(e)))
                click.echo(click.style(f'  ERROR: {e}', fg='red'))

        # Copiar archivos de ejemplo para simular documentos
        click.echo(click.style('\n=== COPIANDO ARCHIVOS DE EJEMPLO ===', fg='cyan', bold=True))
        _copy_test_files()

        # Resumen
        click.echo(click.style('\n=== RESUMEN ===', fg='cyan', bold=True))
        success = len(sql_files) - len(errors)
        click.echo(f'Exitosos: {click.style(str(success), fg="green")}')
        if errors:
            click.echo(f'Errores:  {click.style(str(len(errors)), fg="red")}')
            for filename, err in errors:
                click.echo(f'  - {filename}: {err[:100]}')
        click.echo('')

    @app.cli.command('seed-permissions')
    @click.option('--confirm', is_flag=True, help='Confirmar la ejecucion sin prompt')
    @with_appcontext
    def seed_permissions(confirm):
        """
        Pobla el catálogo de permisos y el mapeo de roles ejecutando los SQL
        en database/DML/permissions/ en orden alfabético.

        Es idempotente: se puede ejecutar múltiples veces sin duplicar datos.

        Uso:
            flask seed-permissions
            flask seed-permissions --confirm
        """
        from app import db

        permissions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'database', 'DML', 'permissions'
        )

        if not os.path.isdir(permissions_dir):
            click.echo(click.style(f'No se encontro el directorio: {permissions_dir}', fg='red'))
            return

        sql_files = sorted(glob.glob(os.path.join(permissions_dir, '*.sql')))
        if not sql_files:
            click.echo(click.style('No hay archivos SQL en el directorio de permisos.', fg='yellow'))
            return

        click.echo(click.style('\n=== SEED PERMISSIONS ===', fg='cyan', bold=True))
        click.echo(f'Directorio: {permissions_dir}')
        click.echo(f'Archivos encontrados: {len(sql_files)}')
        for f in sql_files:
            click.echo(f'  - {os.path.basename(f)}')

        if not confirm:
            if not click.confirm('\nEsto insertara/actualizara el catalogo de permisos. Continuar?'):
                click.echo('Cancelado.')
                return

        errors = []
        for sql_file in sql_files:
            filename = os.path.basename(sql_file)
            click.echo(f'\nEjecutando {click.style(filename, fg="blue")}...')
            try:
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_content = f.read()

                db.session.execute(db.text(sql_content))
                db.session.commit()
                click.echo(click.style('  OK', fg='green'))
            except Exception as e:
                db.session.rollback()
                errors.append((filename, str(e)))
                click.echo(click.style(f'  ERROR: {e}', fg='red'))

        # Resumen con conteos reales
        from app.models.permission import Permission
        from app.models.role_permission import RolePermission

        total_perms = Permission.query.count()
        total_rp = RolePermission.query.count()

        click.echo(click.style('\n=== RESUMEN ===', fg='cyan', bold=True))
        click.echo(f'Permisos en catalogo:      {click.style(str(total_perms), fg="green")}')
        click.echo(f'Mapeos rol-permiso:        {click.style(str(total_rp), fg="green")}')
        if errors:
            click.echo(f'Errores:                   {click.style(str(len(errors)), fg="red")}')
            for filename, err in errors:
                click.echo(f'  - {filename}: {err[:120]}')
        else:
            click.echo(click.style('Sin errores.', fg='green'))
        click.echo('')

    @app.cli.command('apply-patches')
    @click.option('--confirm', is_flag=True, help='Confirmar la ejecucion sin prompt')
    @click.option('--status', is_flag=True, help='Solo mostrar que parches faltan, sin aplicar nada')
    @click.option('--only', default=None, help='Aplicar unicamente el parche con ese nombre de archivo')
    @click.option('--force', is_flag=True, help='Re-aplicar parches ya registrados como aplicados')
    @with_appcontext
    def apply_patches(confirm, status, only, force):
        """
        Aplica los parches de datos de database/DML/patches/ en orden alfabetico.

        Son SQL incrementales pensados para correr sobre una base YA sembrada,
        incluida produccion. Los archivos de database/DML/permissions/ son la
        semilla de una base nueva y no se tocan.

        Lleva registro en la tabla sql_patch (la crea si no existe), asi que un
        parche ya aplicado se salta. Los parches son idempotentes de todos
        modos: el registro es comodidad, no la garantia.

        Uso:
            flask apply-patches --status
            flask apply-patches
            flask apply-patches --confirm
            flask apply-patches --only 2026_08_03_01_programs_register_interest.sql
            flask apply-patches --confirm --force
        """
        from app import db

        patches_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'database', 'DML', 'patches'
        )

        if not os.path.isdir(patches_dir):
            click.echo(click.style(f'No se encontro el directorio: {patches_dir}', fg='red'))
            return

        sql_files = sorted(glob.glob(os.path.join(patches_dir, '*.sql')))
        if only:
            sql_files = [f for f in sql_files if os.path.basename(f) == only]
            if not sql_files:
                click.echo(click.style(f'No existe el parche "{only}" en {patches_dir}', fg='red'))
                return

        if not sql_files:
            click.echo(click.style('No hay parches en el directorio.', fg='yellow'))
            return

        # Registro de parches aplicados. Se crea aqui para que el comando
        # funcione en una base que aun no lo tenga, sin depender de una
        # migracion de esquema.
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS sql_patch (
                id          SERIAL PRIMARY KEY,
                filename    VARCHAR(255) NOT NULL UNIQUE,
                applied_at  TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        db.session.commit()

        applied = {
            row[0] for row in
            db.session.execute(db.text('SELECT filename FROM sql_patch')).fetchall()
        }

        pending = [f for f in sql_files if force or os.path.basename(f) not in applied]

        click.echo(click.style('\n=== APPLY PATCHES ===', fg='cyan', bold=True))
        click.echo(f'Directorio: {patches_dir}')
        for f in sql_files:
            name = os.path.basename(f)
            if name in applied and not force:
                click.echo(f'  {click.style("[aplicado]", fg="green")} {name}')
            else:
                click.echo(f'  {click.style("[pendiente]", fg="yellow")} {name}')

        if status:
            click.echo(f'\nPendientes: {click.style(str(len(pending)), fg="yellow")}')
            click.echo('')
            return

        if not pending:
            click.echo(click.style('\nNada que aplicar: todo al dia.', fg='green'))
            click.echo('')
            return

        if not confirm:
            if not click.confirm(f'\nSe aplicaran {len(pending)} parche(s). Continuar?'):
                click.echo('Cancelado.')
                return

        errors = []
        ok = 0
        for sql_file in pending:
            filename = os.path.basename(sql_file)
            click.echo(f'\nAplicando {click.style(filename, fg="blue")}...')
            try:
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_content = f.read()

                db.session.execute(db.text(sql_content))
                db.session.execute(
                    db.text('INSERT INTO sql_patch (filename) VALUES (:fn) '
                            'ON CONFLICT (filename) DO NOTHING'),
                    {'fn': filename}
                )
                db.session.commit()
                ok += 1
                click.echo(click.style('  OK', fg='green'))
            except Exception as e:
                db.session.rollback()
                errors.append((filename, str(e)))
                click.echo(click.style(f'  ERROR: {e}', fg='red'))

        click.echo(click.style('\n=== RESUMEN ===', fg='cyan', bold=True))
        click.echo(f'Aplicados: {click.style(str(ok), fg="green")}')
        if errors:
            click.echo(f'Errores:   {click.style(str(len(errors)), fg="red")}')
            for filename, err in errors:
                click.echo(f'  - {filename}: {err[:120]}')
        else:
            click.echo(click.style('Sin errores.', fg='green'))
        click.echo('')

    @app.cli.command('clean-test-data')
    @click.option('--confirm', is_flag=True, help='Confirmar la ejecucion sin prompt')
    @with_appcontext
    def clean_test_data(confirm):
        """
        Elimina todos los datos de prueba creados por seed-test-data.

        Uso:
            flask clean-test-data
            flask clean-test-data --confirm
        """
        from app import db
        from app.models.user import User
        from app.models.user_program import UserProgram
        from app.models.submission import Submission
        from app.models.semester_enrollment import SemesterEnrollment
        from app.models.acceptance_document import AcceptanceDocument
        from app.models.extension_request import ExtensionRequest
        from app.models.enrollment_deferral import EnrollmentDeferral
        from app.models.document_deadline import DocumentDeadline
        from app.models.retention_policy import RetentionPolicy
        from app.models.appointment import Appointment
        from app.models.event import Event

        test_usernames = _get_test_usernames()

        if not confirm:
            if not click.confirm('Esto eliminara TODOS los datos de prueba. Continuar?'):
                click.echo('Cancelado.')
                return

        upload_folder = str(current_app.config.get('UPLOAD_FOLDER', 'instance/uploads'))
        deleted_users = 0

        for username in test_usernames:
            user = User.query.filter_by(username=username).first()
            if not user:
                continue

            # Eliminar archivos fisicos
            user_doc_dir = os.path.join(upload_folder, 'documents', str(user.id))
            if os.path.isdir(user_doc_dir):
                shutil.rmtree(user_doc_dir)

            # Eliminar registros en orden de dependencias
            ups = UserProgram.query.filter_by(user_id=user.id).all()
            for up in ups:
                SemesterEnrollment.query.filter_by(user_program_id=up.id).delete()
                AcceptanceDocument.query.filter_by(user_program_id=up.id).delete()
                EnrollmentDeferral.query.filter_by(user_program_id=up.id).delete()

            # Eliminar appointments donde el usuario es aspirante
            Appointment.query.filter_by(applicant_id=user.id).delete()

            ExtensionRequest.query.filter_by(user_id=user.id).delete()
            Submission.query.filter_by(user_id=user.id).delete()
            UserProgram.query.filter_by(user_id=user.id).delete()
            User.query.filter_by(id=user.id).delete()
            deleted_users += 1

        # Limpiar document_deadlines y retention_policies creados por test data
        # (creados por el usuario admin para los periodos de prueba)
        from app.models.academic_period import AcademicPeriod
        test_period_codes = ['20241', '20243', '20251', '20253', '20261', '20263', '20271']
        test_periods = AcademicPeriod.query.filter(
            AcademicPeriod.code.in_(test_period_codes)
        ).all()
        test_period_ids = [p.id for p in test_periods]

        deleted_deadlines = 0
        if test_period_ids:
            deleted_deadlines = DocumentDeadline.query.filter(
                DocumentDeadline.academic_period_id.in_(test_period_ids)
            ).delete(synchronize_session=False)

        deleted_policies = RetentionPolicy.query.delete(synchronize_session=False)

        # Limpiar eventos de prueba (creados con titulos especificos)
        test_event_titles = [
            'Entrevistas Admision MII — Ago-Dic 2024',
            'Entrevistas Admision MII — Ago-Dic 2025',
            'Entrevistas Admision MANI — Ago-Dic 2025',
            'Entrevistas Admision MII — Ago-Dic 2026',
            'Entrevistas Admision MANI — Ago-Dic 2026',
        ]
        deleted_events = Event.query.filter(
            Event.title.in_(test_event_titles)
        ).delete(synchronize_session=False)

        db.session.commit()
        click.echo(click.style(f'Usuarios de prueba eliminados: {deleted_users}', fg='green'))
        if deleted_deadlines:
            click.echo(click.style(f'Ventanas de entrega eliminadas: {deleted_deadlines}', fg='green'))
        if deleted_policies:
            click.echo(click.style(f'Politicas de retencion eliminadas: {deleted_policies}', fg='green'))
        if deleted_events:
            click.echo(click.style(f'Eventos de prueba eliminados: {deleted_events}', fg='green'))


def _get_test_usernames():
    """Lista de usernames de los usuarios de prueba (18 usuarios)."""
    return [
        # Aspirantes (A1-A13)
        'test_a01_nuevo', 'test_a02_parcial', 'test_a03_listo',
        'test_a04_rech_parcial', 'test_a05_rech_total',
        'test_a06_prorroga', 'test_a07_prorr_venc', 'test_a08_viejo',
        'test_a09_acept_nopaga', 'test_a10_acept_listo',
        'test_a11_diferido1', 'test_a12_diferido2', 'test_a13_doc_vencido',
        # Estudiantes (B1-B5)
        'M24110001', 'M24110002', 'M25110001', 'M25110002', 'M25110003',
    ]


def _copy_test_files():
    """
    Copia un archivo PDF de ejemplo a todas las rutas referenciadas
    en las submissions de los usuarios de prueba.
    """
    upload_folder = str(current_app.config.get('UPLOAD_FOLDER', 'instance/uploads'))

    # Archivo fuente de ejemplo
    source_file = os.path.join(upload_folder, 'documents', '3', 'admission', 'Titulo.pdf')

    if not os.path.exists(source_file):
        alt_source = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'instance', 'uploads', 'documents', '3', 'admission', 'Titulo.pdf'
        )
        if os.path.exists(alt_source):
            source_file = alt_source
        else:
            click.echo(click.style(
                f'  No se encontro archivo fuente en:\n'
                f'    {source_file}\n'
                f'    {alt_source}\n'
                f'  Los archivos no seran copiados. Puedes colocar un PDF en esa ruta y volver a ejecutar.',
                fg='yellow'
            ))
            return

    click.echo(f'  Archivo fuente: {source_file}')

    from app.models.submission import Submission
    from app.models.user import User
    from app.models.acceptance_document import AcceptanceDocument
    from app.models.user_program import UserProgram

    copied = 0
    for username in _get_test_usernames():
        user = User.query.filter_by(username=username).first()
        if not user:
            continue

        # Copiar archivos de submissions
        submissions = Submission.query.filter_by(user_id=user.id).all()
        for sub in submissions:
            if sub.file_path:
                dest = os.path.join(upload_folder, sub.file_path)
                dest_dir = os.path.dirname(dest)
                if not os.path.exists(dest):
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.copy2(source_file, dest)
                    copied += 1

        # Copiar archivos de acceptance_document
        ups = UserProgram.query.filter_by(user_id=user.id).all()
        for up in ups:
            acc_docs = AcceptanceDocument.query.filter_by(user_program_id=up.id).all()
            for doc in acc_docs:
                if doc.file_path:
                    dest = os.path.join(upload_folder, doc.file_path)
                    dest_dir = os.path.dirname(dest)
                    if not os.path.exists(dest):
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.copy2(source_file, dest)
                        copied += 1

    click.echo(click.style(f'  Archivos copiados: {copied}', fg='green'))
