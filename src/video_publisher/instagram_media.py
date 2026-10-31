"""Prepare Instagram handoff without editing video; reuse Douyin portrait artwork."""
from copy import deepcopy
from pathlib import Path
from .core import VpubError, atomic_json, digest, file_sha, load_plan, read_json, shared_portrait


def prepare(manifest, output, mobile_cover=None):
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    if manifest == output:
        raise VpubError("invalid_output", "Choose a separate prepared manifest.")
    source = read_json(manifest)
    plan = load_plan(manifest, ["instagram"])
    video = Path(plan["video"])
    variants = source.get("covers", {})
    portrait = mobile_cover or shared_portrait(variants)
    if portrait:
        portrait = Path(portrait) if mobile_cover else manifest.parent / portrait
        portrait = portrait.resolve()
        if not portrait.is_file() or portrait.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not portrait.stat().st_size:
            raise VpubError("invalid_cover", "Choose the complete shared production portrait JPG/PNG.")
    web_cover = plan.get("cover") or plan.get("covers", {}).get("instagram", {}).get("landscape", {}).get("path")
    if not web_cover:
        web_cover = str(portrait) if portrait else None
    value = deepcopy(source)
    value["platforms"] = {"instagram": deepcopy(source["platforms"]["instagram"])}
    value["video"] = str(video)
    # The mobile image is a handoff asset, not evidence of a saved website setting.
    value.pop("covers", None)
    if web_cover:
        value["cover"] = str(Path(web_cover).resolve())
    else:
        value.pop("cover", None)
    assets = {str(video): plan["video_sha256"]}
    if web_cover:
        assets[str(web_cover)] = file_sha(web_cover)
    if portrait:
        assets[str(portrait)] = file_sha(portrait)
    if output in {Path(p).resolve() for p in assets}:
        raise VpubError("invalid_output", "Do not overwrite a source asset.")
    if output.exists():
        if digest(read_json(output)) != digest(value):
            raise VpubError("already_exists", "Prepared manifest differs; choose a new output path.")
        state = "REUSED"
    else:
        state = "PREPARED"
    if any(file_sha(path) != sha for path, sha in assets.items()):
        raise VpubError("source_changed", "A source asset changed during preparation.")
    if state == "PREPARED":
        atomic_json(output, value)
    result = {"state": state, "mode": "ORIGINAL_VIDEO", "manifest": str(output),
              "video": str(video), "video_sha256": plan["video_sha256"], "video_modified": False,
              "upload_crop": "original", "platforms": ["instagram"], "platform_writes": 0,
              "web_cover": str(web_cover) if web_cover else None,
              "mobile_cover": {"path": str(portrait), "sha256": file_sha(portrait)} if portrait else None,
              "next": "Upload the original video with Original crop and select a separate cover. On a phone use Edit cover > Add from camera roll with the shared Douyin portrait artwork, then inspect profile-grid cropping. No cover frame is inserted into the video."}
    atomic_json(output.with_suffix(output.suffix + ".handoff.json"), result)
    return result
