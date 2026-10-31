"""Fit existing artwork without cropping. Presets are working canvas sizes,
not universal platform limits. Baked-in text is not translated by this module.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import shutil
from PIL import Image, ImageOps, UnidentifiedImageError
from .core import VpubError, atomic_json, digest, file_sha, load_plan, read_json, shared_portrait

PRESETS = {
    "bilibili": {"landscape": (1600, 1000)},
    "youtube": {"landscape": (1920, 1080)},
    "douyin": {"landscape": (1200, 900), "portrait": (1080, 1440)},
    "instagram": {"landscape": (1920, 1080), "portrait": (1080, 1920)},
    "x": {"landscape": (1920, 1080)},
}


def prepare(manifest, source, out_dir, output, portrait=None):
    path, source = Path(manifest).resolve(), Path(source).resolve()
    directory, output = Path(out_dir).resolve(), Path(output).resolve()
    original = read_json(path)
    load_plan(path)
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
              "mode": "contain", "background": "#151923", "skipped": skipped}
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
    with tempfile.TemporaryDirectory(prefix=".crosscast-covers-", dir=directory.parent) as tmp:
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
                   "next": "Apply supported covers to the existing work and inspect each saved variant; preparation is not upload verification."}
        atomic_json(staging / "cover-receipt.json", receipt)
        staging.rename(directory)
    atomic_json(output, value)
    return {**receipt, "manifest": str(output)}
