"""Fit existing artwork without cropping. Presets are working canvas sizes,
not universal platform limits. Baked-in text is not translated by this module.
Only a portrait cover built from chosen parts crops, and only to the parts it was given.
"""
from copy import deepcopy
import os
from pathlib import Path
import re
import shutil
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
from .core import VpubError, atomic_json, digest, file_sha, load_plan, read_json, shared_portrait
from .runtime_scratch import scratch

PRESETS = {
    "bilibili": {"landscape": (1600, 1000)},
    "youtube": {"landscape": (1920, 1080)},
    "douyin": {"landscape": (1200, 900), "portrait": (1080, 1440)},
    "instagram": {"landscape": (1920, 1080), "portrait": (1080, 1920)},
    "x": {"landscape": (1920, 1080)},
}
BACKGROUND = "#151923"
# A 9:16 cover whose central 3:4 area holds everything that matters, so Instagram's full frame and Douyin's crop both keep it.
CANVAS = (1080, 1920)
SAFE = (0, 240, 1080, 1440)
MARGIN = 60
GAP = 24
MOST_PARTS = 3
SMALLEST_SIDE = 100
# Below this the parts would float in a band of background, which is a letterboxed landscape in all but name.
LEAST_FILL = 0.6
SOFT_ABOVE = 2.5


def parse_box(value, width, height):
    found = re.fullmatch(r"(\d+),(\d+),(\d+),(\d+)", value or "")
    if not found:
        raise VpubError("invalid_box", "Write each part as x,y,width,height in the source image's pixels.")
    left, top, wide, tall = map(int, found.groups())
    if wide < SMALLEST_SIDE or tall < SMALLEST_SIDE or left + wide > width or top + tall > height:
        raise VpubError("invalid_box", f"Each part must be at least {SMALLEST_SIDE} pixels on both sides and inside the {width}x{height} source.")
    return left, top, wide, tall


def layout(boxes):
    """Where each part lands: as wide as the safe area allows, stacked and centred. The stack must fill that area."""
    room_wide, room_tall = SAFE[2] - 2 * MARGIN, SAFE[3] - 2 * MARGIN
    gaps = GAP * (len(boxes) - 1)
    natural = sum(tall * room_wide / wide for _, _, wide, tall in boxes)
    shrink = min(1, (room_tall - gaps) / natural)
    stack = natural * shrink + gaps
    if stack < LEAST_FILL * room_tall:
        raise VpubError("would_letterbox", "These parts would sit in a band across the cover, which reads as a letterboxed landscape.",
                        "Give a taller part, or separate parts for the subject and the title so they stack.")
    placed, top = [], SAFE[1] + (SAFE[3] - stack) / 2
    for box in boxes:
        scale = shrink * room_wide / box[2]
        wide, tall = round(box[2] * scale), round(box[3] * scale)
        placed.append({"box": list(box), "placed": [(CANVAS[0] - wide) // 2, round(top), wide, tall], "scale": round(scale, 3)})
        top += tall + GAP
    return placed


def portrait(source, boxes, output):
    """Lay chosen parts of an image into a portrait cover, with no AI. The agent looks at the image and picks the parts."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or source.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise VpubError("invalid_cover", "Choose a complete production JPG/PNG.")
    if output.suffix.lower() not in {".jpg", ".jpeg"}:
        raise VpubError("invalid_output", "The portrait cover is saved as a .jpg file.")
    if output.exists():
        raise VpubError("already_exists", "Choose a new output path; existing artwork will not be overwritten.")
    if not boxes or len(boxes) > MOST_PARTS:
        raise VpubError("invalid_box", f"Give from 1 to {MOST_PARTS} parts with --box.")
    before = file_sha(source)
    try:
        with Image.open(source) as opened:
            if opened.width * opened.height > 40000000:
                raise VpubError("invalid_cover", "Cover exceeds the 40-megapixel processing limit.")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise VpubError("invalid_cover", "Cannot decode the production image.") from exc
    parts = layout([parse_box(value, image.width, image.height) for value in boxes])
    blurred = ImageOps.fit(image, CANVAS, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(60))
    canvas = Image.blend(blurred, Image.new("RGB", CANVAS, BACKGROUND), 0.45)
    for part in parts:
        left, top, wide, tall = part["box"]
        canvas.paste(image.crop((left, top, left + wide, top + tall)).resize(tuple(part["placed"][2:]), Image.Resampling.LANCZOS), tuple(part["placed"][:2]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with scratch(output.parent) as tmp:
        target = Path(tmp) / output.name
        for quality in (92, 85, 75, 60):
            canvas.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= 1900000:
                break
        else:
            raise VpubError("cover_too_large", "Output exceeds the conservative local file-size budget.")
        if file_sha(source) != before:
            raise VpubError("source_changed", "Artwork changed during preparation; retry after it finishes.")
        os.replace(target, output)
    return {"state": "PREPARED", "mode": "layout_without_ai", "path": str(output), "sha256": file_sha(output), "bytes": output.stat().st_size,
            "size": list(CANVAS), "safe_area": {"left": SAFE[0], "top": SAFE[1], "width": SAFE[2], "height": SAFE[3]}, "parts": parts,
            "soft_parts": [number for number, part in enumerate(parts, 1) if part["scale"] > SOFT_ABOVE], "source_sha256": before, "platform_writes": 0,
            "next": "Look at the result before using it: text must read clearly and nothing important may be cut. Then use it as the --portrait cover, in vpub prepare or covers prepare."}


def check_supplied(cover, portrait, platforms):
    """The covers someone named, before anyone writes words around them. prepare still checks them when it fits them."""
    if not cover:
        raise VpubError("cover_required", "--portrait goes with --cover, because each platform also needs its landscape cover.")
    for path in filter(None, (cover, portrait)):
        if not Path(path).is_file() or Path(path).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise VpubError("invalid_cover", f"Not a JPG or PNG file: {path}")
    if {"douyin", "instagram"} & set(platforms) and not portrait:
        raise VpubError('portrait_required', 'Supply a dedicated shared Douyin/Instagram portrait; a letterboxed landscape thumbnail is not a portrait composition.', 'Prepare one portrait with text and faces inside the common grid safe area, then pass --portrait PATH. Without an image generator, build it with vpub covers portrait.')
    if portrait:
        try:
            with Image.open(portrait) as opened:
                tall = opened.height > opened.width
        except OSError:
            raise VpubError("invalid_cover", f"Not a JPG or PNG file: {portrait}")
        if not tall:
            raise VpubError('portrait_required', 'The shared portrait must have a portrait aspect ratio.')


def prepare(manifest, source, out_dir, output, portrait=None):
    path, source = Path(manifest).resolve(), Path(source).resolve()
    directory, output = Path(out_dir).resolve(), Path(output).resolve()
    original = read_json(path)
    load_plan(path)
    from .publication_review import carry_choice
    carry_choice(path, output, original)
    portrait = Path(portrait).resolve() if portrait else None
    variants = original.get('covers', {})
    shared = shared_portrait(variants)
    shared = portrait or ((path.parent / shared).resolve() if shared else None)
    jobs, skipped = [], {}
    for platform, opts in original["platforms"].items():
        if platform == "x" and opts.get('method', 'browser') == 'native':
            skipped[platform] = "Custom thumbnails are not implemented; video thumbnail remains unchanged."
            continue
        for role, size in PRESETS[platform].items():
            override = original.get("covers", {}).get(platform, {}).get(role)
            if role == 'portrait' and platform in {'douyin','instagram'}:
                if shared is None:
                    raise VpubError('portrait_required', 'Supply a dedicated shared Douyin/Instagram portrait; a letterboxed landscape thumbnail is not a portrait composition.', 'The host should prepare one portrait with text/face inside the common grid safe area, then pass --portrait PATH.')
                asset = shared
            else:
                asset = (path.parent / override).resolve() if override else source
            if not asset.is_file() or asset.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                raise VpubError("invalid_cover", "Choose a complete production JPG/PNG or dedicated variant.")
            jobs.append({"platform": platform, "role": role, "size": list(size),
                         "source": str(asset), "source_sha256": file_sha(asset)})
    recipe = {"version": 2, "manifest_sha256": digest(original), "jobs": jobs,
              "mode": "contain", "background": BACKGROUND, "skipped": skipped}
    recipe_hash = digest(recipe)
    receipt_path = directory / "cover-receipt.json"
    if directory.exists() or output.exists():
        if receipt_path.is_file() and output.is_file():
            saved = read_json(receipt_path)
            if (saved.get("recipe_sha256") == recipe_hash and
                    saved.get("manifest_sha256") == digest(read_json(output)) and
                    all(Path(a["path"]).is_file() and file_sha(a["path"]) == a["sha256"] for a in saved.get("assets", []))):
                return {**saved, "state": "REUSED", "manifest": str(output), "platform_writes": 0}
        raise VpubError("already_exists", "Choose new output paths; existing artwork/manifests will not be overwritten.")
    directory.parent.mkdir(parents=True, exist_ok=True)
    value = deepcopy(original)
    value["video"] = str((path.parent / value["video"]).resolve())
    value.pop("cover", None)
    value["covers"] = {}
    assets = []
    with scratch(directory.parent) as tmp:
        staging = Path(tmp)
        for job in jobs:
            if job['role']=='portrait':
                src=Path(job['source'])
                with Image.open(src) as opened:
                    if opened.height <= opened.width:
                        raise VpubError('portrait_required', 'The shared portrait must have a portrait aspect ratio.')
                    actual_size=[opened.width,opened.height]
                filename='shared-douyin-instagram-portrait'+src.suffix.lower()
                target=staging/filename
                shutil.copyfile(src,target)
                if file_sha(target)!=job['source_sha256']:
                    raise VpubError('source_changed','Shared portrait changed during preparation.')
                final=directory/filename
                value['covers'].setdefault(job['platform'],{})[job['role']]=str(final)
                assets.append({**job,'size':actual_size,'path':str(final),'sha256':file_sha(target),'bytes':target.stat().st_size,'mode':'shared_original'})
                continue
            filename = job["platform"] + "-" + job["role"] + ".jpg"
            target = staging / filename
            try:
                with Image.open(job["source"]) as opened:
                    if opened.width * opened.height > 40000000:
                        raise VpubError("invalid_cover", "Cover exceeds the 40-megapixel processing limit.")
                    image = ImageOps.exif_transpose(opened).convert("RGBA")
                    image = ImageOps.contain(image, tuple(job["size"]), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", tuple(job["size"]), recipe["background"])
                    canvas.paste(image, ((canvas.width-image.width)//2, (canvas.height-image.height)//2), image)
                    for quality in (92, 85, 75, 60):
                        canvas.save(target, "JPEG", quality=quality, optimize=True)
                        if target.stat().st_size <= 1900000:
                            break
                    else:
                        raise VpubError("cover_too_large", "Output exceeds the conservative local file-size budget.")
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise VpubError("invalid_cover", "Cannot decode the production image.") from exc
            if file_sha(job["source"]) != job["source_sha256"]:
                raise VpubError("source_changed", "Artwork changed during preparation; retry after it finishes.")
            final = directory / filename
            value["covers"].setdefault(job["platform"], {})[job["role"]] = str(final)
            assets.append({**job, "path": str(final), "sha256": file_sha(target), "bytes": target.stat().st_size})
        receipt = {"recipe_sha256": recipe_hash, "manifest_sha256": digest(value), "assets": assets,
                   "skipped": skipped, "state": "PREPARED", "platform_writes": 0,
                   "mode": "landscape_contain_shared_portrait", "text_translated": False,
                   "portrait_policy": "Douyin and Instagram receive the identical portrait file/hash. Keep titles and faces within the central crop-safe area; mobile grid adjustment remains separately verified.",
                   "next": "Look at each saved cover, then run vpub plan on the new manifest. Preparing covers uploads nothing."}
        atomic_json(staging / "cover-receipt.json", receipt)
        staging.rename(directory)
    atomic_json(output, value)
    return {**receipt, "manifest": str(output)}
