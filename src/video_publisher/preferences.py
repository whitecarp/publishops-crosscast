"""Small local choices for the host assistant, never permission to publish."""
from .core import PLATFORMS, VpubError, atomic_json, config, home, receipt_turn


def platform_list(value):
    names = [name.strip() for name in value.split(',')] if isinstance(value, str) else value
    if (not isinstance(names, list) or not names
            or not all(isinstance(name, str) and name in PLATFORMS for name in names)
            or len(names) != len(set(names))):
        raise VpubError('invalid_platform', 'Choose a unique comma-separated list of platforms: ' + ', '.join(PLATFORMS) + '.')
    return list(names)


def selected(current):
    if not isinstance(current, dict):
        raise VpubError('invalid_preferences', 'Local settings must be a JSON object.')
    language = current.get('report_language', 'auto')
    if language not in ('auto', 'zh', 'en'):
        raise VpubError('invalid_language', 'Report language must be auto, zh or en.')
    platforms = current.get('preferred_platforms')
    if platforms is not None:
        platforms = platform_list(platforms)
    show_first = current.get('show_first')
    if show_first is not None and type(show_first) is not bool:
        raise VpubError('invalid_preferences', 'show_first must be true or false; use vpub preferences --show-first yes or no.')
    return {'report_language': language, 'preferred_platforms': platforms, 'show_first': show_first}


def read():
    choices = selected(config())
    return {**choices, 'missing': [name for name in ('preferred_platforms', 'show_first') if choices[name] is None],
            'reply_language': 'comment language only; single language',
            'note': 'Preferences do not authorize uploads or posts. The host asks only for unknown choices; the CLI never prompts.'}


def update(language=None, platforms=None, show_first=None):
    if language is None and platforms is None and show_first is None:
        return read()
    changes = {}
    if language is not None:
        changes['report_language'] = language
    if platforms is not None:
        changes['preferred_platforms'] = platform_list(platforms)
    if show_first is not None:
        changes['show_first'] = show_first
    with receipt_turn(home() / 'config.json'):
        current = config()
        if not isinstance(current, dict):
            raise VpubError('invalid_preferences', 'Local settings must be a JSON object.')
        current = {**current, **changes}
        selected(current)
        atomic_json(home() / 'config.json', current)
    return read()
