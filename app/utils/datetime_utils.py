# app/utils/datetime_utils.py

from datetime import date, datetime
import zoneinfo

from markupsafe import Markup, escape

# Zona horaria local de Ciudad Juárez
LOCAL_TZ = zoneinfo.ZoneInfo("America/Ciudad_Juarez")

def now_local():
    """
    Devuelve la hora actual en la zona horaria local de Ciudad Juárez.
    """
    return datetime.now(LOCAL_TZ)

def to_local_timezone(dt):
    """
    Convierte un datetime a la zona horaria local de Ciudad Juárez.
    
    Args:
        dt: datetime object (puede tener o no zona horaria)
    
    Returns:
        datetime object en zona horaria local
    """
    if dt.tzinfo is None:
        # Si no tiene zona horaria, asumir que es hora local
        return dt.replace(tzinfo=LOCAL_TZ)
    else:
        # Convertir a hora local
        return dt.astimezone(LOCAL_TZ)


# ---------------------------------------------------------------------------
# Spanish date formatting
#
# The app never calls locale.setlocale(), so strftime('%B') always renders the
# month name in English. These helpers are the only supported way to print a
# date in the UI. They are registered as the Jinja filters `fecha_es` and
# `fechahora_es` in app/__init__.py.
# ---------------------------------------------------------------------------

# Index 0 = enero ... index 11 = diciembre (use MONTHS_ES[dt.month - 1]).
MONTHS_ES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]

# Abbreviated month names used by the 'short' style.
MONTHS_ES_SHORT = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic',
]

# Supported style keys for format_date_es / format_datetime_es.
DATE_STYLES = ('long', 'short', 'numeric')


def _coerce_date(value):
    """
    Normalize a template value into a date/datetime instance.

    Accepts datetime, date and ISO-8601 strings. Datetimes are converted to the
    local timezone so a UTC-aware value never renders the previous day.

    Args:
        value: datetime, date, ISO-8601 string or None

    Returns:
        datetime/date instance, or None when the value cannot be interpreted
    """
    if value is None or value == '':
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            return None

    if isinstance(value, datetime):
        return to_local_timezone(value)

    if isinstance(value, date):
        return value

    return None


def format_date_es(dt, style='long'):
    """
    Format a date in Spanish.

    Styles:
        'long'    -> '31 de julio de 2026'
        'short'   -> '31 jul 2026'
        'numeric' -> '31/07/2026'

    Args:
        dt: datetime, date, ISO-8601 string or None
        style: one of DATE_STYLES; unknown styles fall back to 'long'

    Returns:
        str — empty string when dt is None or unparseable (never raises)
    """
    value = _coerce_date(dt)
    if value is None:
        return ''

    if style == 'numeric':
        return f'{value.day:02d}/{value.month:02d}/{value.year}'

    if style == 'short':
        return f'{value.day} {MONTHS_ES_SHORT[value.month - 1]} {value.year}'

    return f'{value.day} de {MONTHS_ES[value.month - 1]} de {value.year}'


def format_time_es(dt):
    """
    Format the time part of a datetime as 24h 'HH:MM'.

    Args:
        dt: datetime, date, ISO-8601 string or None

    Returns:
        str — empty string when there is no time information available
    """
    value = _coerce_date(dt)
    if not isinstance(value, datetime):
        return ''
    return f'{value.hour:02d}:{value.minute:02d}'


def format_datetime_es(dt, style='long', include_time=True):
    """
    Format a datetime in Spanish wrapped in a semantic <time> element.

    Styles (text content):
        'long'    -> '31 de julio de 2026 a las 14:30'
        'short'   -> '31 jul 2026, 14:30'
        'numeric' -> '31/07/2026 14:30'

    The machine-readable value of the datetime attribute is always
    value.isoformat(). Set include_time=False to render only the date while
    keeping the <time> wrapper.

    Args:
        dt: datetime, date, ISO-8601 string or None
        style: one of DATE_STYLES; unknown styles fall back to 'long'
        include_time: append the clock time when the value carries one

    Returns:
        Markup — empty Markup when dt is None or unparseable (never raises)
    """
    value = _coerce_date(dt)
    if value is None:
        return Markup('')

    text = format_date_es(value, style)
    clock = format_time_es(value) if include_time else ''

    if clock:
        if style == 'long':
            text = f'{text} a las {clock}'
        elif style == 'short':
            text = f'{text}, {clock}'
        else:
            text = f'{text} {clock}'

    return Markup('<time datetime="{iso}">{text}</time>').format(
        iso=escape(value.isoformat()),
        text=text,
    )