"""Explicit YouTube category selection; no content-independent default."""
import re
from .core import VpubError


def validate_youtube(category_id):
    if not isinstance(category_id, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", category_id):
        raise VpubError("category_required", "Choose category_id from the video's content and current YouTube categories. Use metadata request/apply or set it explicitly; there is no default.")
    return category_id
