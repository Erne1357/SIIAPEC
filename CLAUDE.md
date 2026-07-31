# SIIAP — Project Instructions for Claude

## Stack
- **Backend**: Flask 3.x + SQLAlchemy + Flask-Migrate + Flask-Login + Flask-SocketIO
- **Database**: PostgreSQL 15 (Docker service: `db`, host `db`)
- **Task queue**: Celery + Redis
- **Frontend**: Bootstrap 5 + Bootstrap Icons + Vanilla JS (no framework)
- **Container**: Docker Compose

## Language Rules
| Where | Language |
|-------|----------|
| Code: variables, functions, classes, docstrings, comments | **English** |
| UI: labels, headings, flash messages, HTML text, placeholders | **Spanish** |

This matches the existing codebase. Never write UI text in English.

---

## Style Standard (CSS Design System)

**Source of truth:** `app/static/css/_tokens.css`. The CSS core (tokens + `base.css` + every `components/*.css`) is declared in **one place only**: `app/templates/_head_css.html`, included by both `base.html` and `auth_base.html`. Never write those `<link>` tags in a page template.

**Cascade order:** Bootstrap → `_tokens.css` → `base.css` → `components/*.css` → layout sheets → page sheet (`{% block styles %}`). Shared components carry **no `!important`**, so a page sheet that redeclares `.nav-link`, `.nav-tabs`, `.stat-card`, `.icon-*`, `.empty-state` or `.status-badge--*` **wins**. Don't redeclare them — extend with a new class.

### Token files
| File | Purpose |
|------|---------|
| `app/static/css/_tokens.css` | Custom properties + Bootstrap overrides. The only file allowed to define tokens. |
| `app/static/css/base.css` | Shell (header/sidebar/main/footer), reset, global utilities. |
| `app/static/css/components/` | Reusable BEM components, one file each. |
| `app/templates/_head_css.html` | The single declaration of the CSS core. |

### Reusable BEM components (from `components/`)
- `.status-badge` + `--{in-progress,interview-completed,deliberation,accepted,approved,rejected,deferred,enrolled,pending,review}` + `--task-{success,failure,started,retry,pending,revoked}` (Celery) + `--sm/--lg` + `--as-link`
- `.empty-state` + `__icon`, `__title`, `__description`, `__error-detail`, `__actions` (+ `--compact`, `--error`, `--inline` for use inside a `<td colspan>`)
- `.siiap-table-wrapper` + `.siiap-table` (sticky first column + scroll hint; auto-inits on `DOMContentLoaded`, no JS wiring needed)
- `.stepper` + `__step`, `__number`, `__label` + `--active`, `--completed`
- `.role-banner` + `__avatar`, `__content`, `__greeting`, `__role`, `__next-action`, `__progress`, `__progress-bar`, `__progress-label`
- `.skeleton`, `.skeleton-text`, `.skeleton-avatar`, `.skeleton-card`, `.skeleton-button`, `.skeleton-table`, `.skeleton-row`, `.skeleton-cell` + `--{xs,sm,md,lg,full}`; `.skeleton-row--cols-{2..8}`, `.skeleton-card--{sm,md,lg}`, `.skeleton-region` (announceable via `aria-busy`)
- `.stat-card` + `__icon`, `__value`, `__label`, `__hint` + `--{success,warning,danger,info,brand,interactive}`; standalone `.kpi-value` (+`--sm`/`--lg`) and `.kpi-label`
- `.nav-link`, `.nav-tabs`, `.nav-pills`, `.tab-content` — **all navigation lives in `_nav.css`**, nowhere else
- `.auth-hero` + `__content`, `__title`, `__tagline`, `__highlights`, `__highlight` (no `__eyebrow` — kickers are banned)
- `.events-widget` + `__header`, `__title`, `__body`, `__section`, `__section-title`, `__list`, `__item`, `__meta`, `__actions`, `__chips`, `__badge`, `__empty`
- `.event-cover` + `--{interview,defense,workshop,seminar,conference,info-session,default}` + `__icon`, `--has-image`, `--scrim`

### Token categories (always reference, never hardcode)
- **Colors:** `--color-brand-{primary,primary-50,primary-100,primary-600,primary-700,accent,accent-600,accent-100,secondary}`, `--color-{success,warning,danger,info,purple}` + `*-100` + `*-700`, `--color-neutral-{0..900}`.
  - The **`-700` ramp is for text on `-100` surfaces** (all ≥5.6:1). The base tones are for solid fills only: `--color-warning` and `--color-success` fail AA as text on white.
- **Roles:** `--bg-{page,surface,surface-raised,surface-sunken}`, `--border-{default,strong}`, `--text-{primary,secondary,muted,faint,inverse}`.
  - `--text-faint` is **never for text** — decorative icons and separators only.
- **Tinted borders:** `--border-{brand,success,warning,danger,info,purple}-subtle`. These replace every hand-written `rgba()` border.
- **Status triads:** `--status-{in-progress,interview-completed,deliberation,accepted,rejected,deferred,enrolled,pending,review}-{bg,fg,bd}`. Retheming every status chip in the app means editing these, not the component.
- **Event covers:** `--event-cover-{interview,defense,workshop,seminar,conference,info-session,default}`. Never inject these from JS — JS adds the class, CSS owns the color.
- **Type:** `--font-{sans,display,mono}`, `--fs-{xs,sm,base,md,lg,xl,2xl,3xl,display}`, `--fw-{regular,medium,semibold,bold}`, `--lh-{tight,normal,relaxed}`, `--tracking-{tight,normal,wide,display}`.
- **Spacing (8px scale):** `--space-{0,1,2,3,4,5,6,8,10,12,16,20}`.
- **Radii:** `--radius-{xs,sm,md,lg,xl,pill}`.
- **Shadows:** `--shadow-{0,1,2,3,4,5}`, `--shadow-focus`.
- **Z-index:** `--z-{base,raised,above,dropdown,sticky,fixed,fab,backdrop,offcanvas,modal,popover,tooltip,toast,max}`.
- **Motion:** `--duration-{instant,fast,normal,slow,pulse}`, `--ease-{out,in-out}`. `--ease-out` is exponential and is the default for entrances and state changes. There is **no** `--ease-spring`: bounce easing is banned.
- **Layout & sizing:** `--header-h{,-md,-sm}`, `--sidebar-w`, `--footer-h`, `--content-max-w`, `--content-padding`, `--icon-box-{sm,md,lg}`, `--avatar-{xs,sm,md,lg,xl}`, `--measure-prose` (65ch), `--measure-narrow`, `--tap-min` (44px).
- **Utility extensions:** `.bg-{success,warning,danger,info,primary}-soft`, `.text-{warning,success,danger,info,brand-primary,brand-accent}-strong`, `.avatar-{xs,sm,md,lg,xl}`, `.icon-{xl,2xl,3xl,5xl}`, `.measure-prose`, `.tap-target`, `.skip-link`, `.w-px-N`, `.w-sm-auto`, `.cursor-pointer`, `.modal-body-scroll`.

**Do not exist — never write them:** `--ease-spring`, `--fs-4xl`, `--fs-5xl`, `--offcanvas-w`, `--surface-translucent`, `--bg--tecnm`, `--rojoTec`, `--azulFuerte`. The last three were legacy brand aliases; use `--color-brand-{primary,accent,secondary}`.

### Jinja macros (`app/templates/_macros.html`) — prefer these over hand-written markup
`{% from '_macros.html' import <macro> %}` — **a macro used without its import fails at render, not at compile.**

| Macro | Guarantees |
|---|---|
| `modal_shell(id, title, size, icon, centered, scrollable)` | `aria-labelledby`, `<h2 class="modal-title h5">`, `btn-close` with `aria-label="Cerrar"` |
| `field(id, label, type, name, required, help, autocomplete, placeholder, value, options, rows, disabled)` | `<label for>` bound to the control, `required_mark()`, help text via `aria-describedby` |
| `data_table_open(caption, id, table_class, wrapper_class)` / `data_table_close()` | `.siiap-table-wrapper` + mandatory `<caption class="visually-hidden">` |
| `async_region(id, label)` | `role="region" aria-live="polite" aria-busy`, Spanish `aria-label` |
| `icon_button(icon, label, cls, href, **attrs)` | always emits `aria-label` + `aria-hidden` icon + 44px target |
| `status_badge(status, label, size)` | icon as the non-color signal (WCAG 1.4.1), `_` → `-` conversion |
| `empty_state(icon, title, description, action_label, action_url, compact, variant, retry_id, error_detail)` | full subcomponents, `--error`/`--inline` variants |
| `stepper(steps, current)` | `aria-current="step"`, 44px steps |
| `role_banner(user, role_key, ...)` | `progress` emitted as `style="--progress: N%"` |
| `confirm_modal(id, title, body, ...)` | labelled destructive confirmation |
| `skeleton_table(rows, cols)` / `skeleton_cards(count, size, col_class)` / `skeleton_text_block(lines)` | `skeleton_cards` takes **`size`** (`'sm'\|'md'\|'lg'`), not `height` |
| `required_mark()` | `<abbr title="Campo requerido" aria-label="requerido">` |

### Shared JS API (global `SIIAP` namespace)
| Call | Purpose |
|---|---|
| `SIIAP.statusBadgeEl(status, label, size)` | **preferred** — returns a real DOM node, no HTML-string injection |
| `SIIAP.statusBadge(status, label, size)` | HTML string for template literals; escapes the label |
| `SIIAP.statusLabel(status)` / `SIIAP.statusKey(status)` / `SIIAP.STATUS_META` | keep in sync with `_STATUS_META` in `_macros.html` |
| `SIIAP.announce(msg)` | writes to the single live region `#siiap-live` — never create another |
| `SIIAP.setBusy(el, busy, opts)` | `aria-busy` on the element and its `.skeleton-region` |
| `SIIAP.syncExpanded(trigger, target, open)` | `aria-expanded` + `aria-controls` + `show` class |
| `SIIAP.togglePassword(button, force)` | contract: `<button data-toggle-password="#inputId">` |
| `SIIAP.formatDate/formatDateTime/formatTime/timeEl/parseDate` | `es-MX` formatting; `parseDate` reads `YYYY-MM-DD` as **local** time (avoids the off-by-one-day bug) |
| `SIIAP.initDataTable(wrapper)` | already auto-runs on `DOMContentLoaded` |

### Dates
Never `strftime('%B')` — there is no `locale.setlocale` in this project, so months print **in English**. Use `{{ value|fecha_es }}` (`'31 de julio de 2026'`, styles `'short'`/`'numeric'`) and `{{ value|fechahora_es }}` (emits `<time datetime="…">`). Python side: `format_date_es` / `format_datetime_es` / `format_time_es` in `app/utils/datetime_utils.py`.

### Hard rules (never break)
1. **No hardcoded colors** in HTML/CSS — use `var(--color-*)`, `var(--text-*)`, `var(--bg-*)`, `var(--border-*)`.
2. **No hardcoded dimensions** in HTML — use `.w-px-N` utility or token-based CSS.
3. **Spacing only via** `var(--space-N)` — no random `padding: 14px`.
4. **Radii only via** `var(--radius-*)`.
5. **Type only via** `--fs-*` / `--fw-*` / `--font-*`.
6. **No `<style>` blocks in templates** — write `app/static/css/<feature>/<page>.css` and link. (Only exception: `coordinator/student_record/_pdf.html`, which renders to PDF.)
7. **No inline `style=`** — the sole exception is passing a genuinely dynamic Jinja value into a custom property, e.g. `style="--progress: {{ n }}%"`.
8. **BEM for new components:** `.block`, `.block__element`, `.block--modifier` (dashes, not underscores in block name).
9. **Icons:** Bootstrap Icons `<i class="bi bi-*"></i>` — no inline SVG, no emoji-as-icon, and **never `fa-*`** (Font Awesome is not loaded).
10. **Status/badges:** `.status-badge--*` for anything communicating **state**; plain `badge bg-*` stays for **numeric counters** only.
11. **Empty states:** `.empty-state` with full subcomponents — no improvising.
12. **Tables:** always `data_table_open()` / `.siiap-table-wrapper`, with `<caption>` and `scope="col"`.
13. **Mobile-first:** max-width media queries in component files (576/768/992/1200 px).
14. **WCAG 2.1 AA:** never rely on color alone, min touch target 44px (`.tap-target` / `--tap-min`), `:focus-visible` outlines, every async region announced.
15. **`@keyframes` are global per document** — always prefix `siiap-<component>-<effect>`.
16. **No dark mode in the app.** Only transactional emails declare `color-scheme`.

### Banned patterns
- Gradient text — emphasis comes from weight or size.
- `border-left` / `border-right` in color wider than 1px on cards, alerts or rows. Use a `-100` background + a 1px tinted border.
- Decorative glassmorphism and zero-offset colored halo shadows.
- Nested cards; a grid of identical cards used as the page structure.
- A kicker/eyebrow above a heading.
- Infinite looping animations for information already carried by text.
- Bounce/elastic easing.

### Anti-pattern reminders
- ❌ `<div style="background:#f0f0f0; padding:12px">` → ✅ `<div class="bg-surface-sunken p-3">`
- ❌ `<span class="badge bg-warning">Pendiente</span>` → ✅ `{{ status_badge('pending') }}`
- ❌ `<style>.my-card { background: #fff; }</style>` → ✅ external CSS file using `var(--bg-surface)`
- ❌ `width: 120px` → ✅ `class="w-px-120"`
- ❌ `color: var(--color-warning)` for text → ✅ `var(--color-warning-700)` (the base tone is 3.26:1 on white)
- ❌ `<table class="table">` → ✅ `{{ data_table_open('Solicitudes de admisión') }}…{{ data_table_close() }}`
- ❌ `.text-bg-success-subtle` → ✅ that class **does not exist** in Bootstrap 5.3; use `.bg-success-soft`

### Emails are different
`app/templates/emails/` follows **email rules, not app rules**: table layout, 600px max width, literal hex (Outlook strips custom properties), inline styles, bulletproof buttons. Every child extends `base_email.html` and must fill `{% block preheader %}`. Palette mirrors the tokens: `#0b1e8a`, `#b21f2d`, `#1f9d55`/`#0e6e3a`, `#b88600`/`#6b4f00`, `#f4f6f8`, `#1f2933`.

---

## Running Commands
Always run Flask/DB commands inside the Docker container:
```bash
docker exec siiap-web-1 flask db migrate -m "add_<description>"
docker exec siiap-web-1 flask db upgrade
docker exec siiap-web-1 flask shell
```
The container name may vary — use `docker ps` to confirm.

---

## Project Structure
```
app/
  models/        # SQLAlchemy models — one file per model
  services/      # Business logic — one file per domain
  routes/
    api/         # REST endpoints (url_prefix='/api/v1/<resource>')
    pages/       # HTML page routes
  templates/     # Jinja2 — Bootstrap 5
  static/
    js/          # Vanilla JS, organized by feature
    css/
  utils/         # Shared helpers (permissions, files, datetime, etc.)
  sockets/       # Flask-SocketIO event handlers
database/
  DML/
    permissions/ # 01_permissions.sql — seed for all permission codenames
migrations/
  versions/      # Flask-Migrate generated files
```

---

## API Endpoint Patterns

### Blueprint definition
```python
# app/routes/api/<resource>_api.py
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.utils.permissions import permission_required, any_permission_required
import app.services.<domain>_service as svc

api_<resource> = Blueprint('api_<resource>', __name__, url_prefix='/api/v1/<resource>')
```

### Standard response format — follow exactly
```python
# Success
return jsonify({"data": result, "error": None, "meta": {}}), 200

# Success with count
return jsonify({"data": items, "error": None, "meta": {"count": len(items)}}), 200

# Business error (caught domain exception → 400 or 404)
return jsonify({
    "data": None,
    "flash": [{"level": "warning", "message": str(e)}],
    "error": {"code": "BUSINESS_ERROR", "message": str(e)},
    "meta": {}
}), 400

# Not found
return jsonify({
    "data": None,
    "error": {"code": "NOT_FOUND", "message": str(e)},
    "meta": {}
}), 404

# Server error (unexpected exception → 500)
return jsonify({
    "data": None,
    "error": {"code": "SERVER_ERROR", "message": str(e)},
    "meta": {}
}), 500
```

### Permission decorators
```python
@login_required
@permission_required('resource.api.action')                              # no program scope
@permission_required('resource.api.action', program_id_kwarg='program_id')  # program-scoped
@any_permission_required('perm.one', 'perm.two')                         # at least one
```
- Source: `app/utils/permissions.py`
- Old `@roles_required(...)` is **deprecated** — never use it

### Registering a new Blueprint
Edit `app/routes/api/__init__.py`, add inside `register_api_blueprints()`:
```python
from app.routes.api.<resource>_api import api_<resource>
# then add api_<resource> to the blueprints list
```

### CSRF on mutations
Frontend must send header `X-CSRFToken` on POST/PUT/PATCH/DELETE requests.

---

## Service Patterns

### File skeleton
```python
# app/services/<domain>_service.py
"""
One-paragraph description of the domain and its flow.
"""

from app import db
from app.models import ...
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class <Domain>Error(Exception):
    """Base error for <domain> operations."""

class <Thing>NotFound(<Domain>Error):
    pass

class InvalidStateTransition(<Domain>Error):
    pass
```

### db.session pattern
```python
try:
    db.session.add(obj)
    db.session.commit()
except Exception:
    db.session.rollback()
    raise
```

### History + Notifications — required after every state mutation
```python
UserHistoryService.log_action(
    user_id=target_user.id,
    admin_id=acting_user_id,       # current_user.id passed from route
    action='descriptive_action_name',
    details={'key': 'value'}
)

NotificationService.create_notification(
    user_id=target_user.id,
    notification_type='type_name',
    title='Título en español',
    message='Mensaje en español',
    priority='normal'               # 'low' | 'normal' | 'high'
)
```

### Services must be framework-agnostic
- No `request`, `g`, `current_user` inside services
- Pass user IDs and data as explicit function arguments
- Routes are responsible for extracting context and passing it in

---

## Permission Naming Convention
Format: `{resource}.{type}.{action}`
- **resource**: functional module (`programs`, `acceptance`, `deliberation`, etc.)
- **type**: `api` for REST endpoints · `page` for HTML page routes
- **action**: short verb/description

Examples:
```
acceptance.api.upload_doc
acceptance.api.list_applicants
programs.page.view
admin_users.api.delete
permissions.api.delegate
```

**After defining new permissions**: add the INSERT rows to `database/DML/permissions/01_permissions.sql`.

---

## File Storage
```python
from app.utils.files import save_user_doc

path = save_user_doc(file_obj, user_id, phase)
# Served at: /files/doc/<user_id>/<phase>/<filename>
```
Valid phases: `admission` · `permanence` · `conclusion` · `acceptance`

---

## Migration Workflow
1. Make model changes
2. `docker exec siiap-web-1 flask db migrate -m "add_<what>"`
3. **Review** the generated file in `migrations/versions/` before applying
4. `docker exec siiap-web-1 flask db upgrade`

## Deploy Order (permission system)
After any schema migration that adds tables or after a fresh DB setup:
```bash
docker exec siiap-web-1 flask db upgrade          # 1. apply schema migrations
docker exec siiap-web-1 flask seed-permissions    # 2. populate permission catalog + role mappings
```
Both commands are **idempotent** — safe to re-run on existing data.
When adding a new permission codename: add it to `database/DML/permissions/01_permissions.sql` and re-run `flask seed-permissions`.

---

## Key Models Quick Reference
| Model | Location | Purpose |
|-------|----------|---------|
| `UserProgram` | `models/user_program.py` | Central: user↔program link, `admission_status` |
| `User` | `models/user.py` | Accounts + roles |
| `AcademicPeriod` | `models/academic_period.py` | Enrollment periods |
| `Submission` | `models/submission.py` | Generic document submissions |
| `AcceptanceDocument` | `models/acceptance_document.py` | Acceptance phase docs |
| `SemesterEnrollment` | `models/semester_enrollment.py` | Permanence tracking |
| `Permission` / `RolePermission` / `UserPermission` | `models/permission.py` etc. | Permission system |

### `admission_status` valid values
`in_progress` · `interview_completed` · `deliberation` · `accepted` · `rejected` · `deferred` · `enrolled`

### Roles
`applicant` · `program_admin` · `postgraduate_admin` · `social_service` · `student`

> **Nota:** "coordinator" no es un rol. Es un `program_admin` cuyo id está en `Program.coordinator_id`. Los permisos `coordinator.*` están mapeados al rol `program_admin`.

---

## Anti-patterns — Never Do These
- Business logic in routes — routes call services, that's it
- `Model.query` or `db.session` directly in routes
- `from app.utils.auth import ...` — file deleted, use `app.utils.permissions`
- `@roles_required(...)` — deprecated, use `@permission_required(...)`
- Hardcoded `datetime.now()` — use `now_local()` from `app.utils.datetime_utils`
- Missing `UserHistoryService.log_action()` on any state change
- UI labels or messages in English
- Adding new permissions without updating `01_permissions.sql`
