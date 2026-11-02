"""Explicit YouTube category selection; no content-independent default."""
import re
from .core import VpubError

# The categories YouTube Studio's menu offers: Studio's own code for each category ID, the same in every interface language.
STUDIO_CATEGORIES = {"1": "FILM", "2": "AUTOS", "10": "MUSIC", "15": "PETS", "17": "SPORTS", "19": "TRAVEL", "20": "GADGETS", "22": "PEOPLE",
                     "23": "COMEDY", "24": "ENTERTAINMENT", "25": "NEWS", "26": "HOWTO", "27": "EDUCATION", "28": "SCIENCE", "29": "GOVERNMENT"}


def validate_youtube(category_id):
    if not isinstance(category_id, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", category_id):
        raise VpubError("category_required", "Choose category_id from the video's content and current YouTube categories. Use metadata request/apply or set it explicitly; there is no default.")
    return category_id
