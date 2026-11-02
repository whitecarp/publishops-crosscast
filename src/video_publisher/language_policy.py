"""Source-language policy; the host model supplies translations, not this CLI."""
from .core import VpubError, language_tag

TARGETS = {p: 'zh-Hans' if p in {'bilibili', 'douyin'} else 'en'
           for p in ('bilibili', 'douyin', 'youtube', 'instagram', 'x')}


def route(source, platform):
    source = language_tag(source)
    base = source.lower().split('-')[0]
    if base in {'und', 'mul', 'zxx'}:
        raise VpubError('source_language_required',
                        'Identify the spoken/content language before generating platform copy.',
                        'The host model must inspect the supplied script/transcript and set content_language; never infer it from the docs or account locale.')
    target = TARGETS[platform]
    matches = base in {'zh', 'cmn', 'yue'} if target == 'zh-Hans' else base == 'en'
    return {'language': target, 'source_language': source,
            'mode': 'target_only' if matches else 'bilingual',
            'description_languages': [target] if matches else [source, target]}
