"""One video in two commands: prepare gets it ready and says what to write, go does the rest.

Both only run the existing commands in their usual order, exactly as if typed, so every check,
the confirmation and the ledger stay where they are, and whatever changes there reaches here too.
"""
import os
from pathlib import Path
import shutil
import tempfile

from .core import VpubError, atomic_json, digest, file_sha, read_json, when_the_file_is_free


def step(*words):
    from . import cli
    return cli.execute(cli.parser().parse_args([str(word) for word in words]))


def prepare(args):
    video = Path(args.video).resolve()
    if not video.is_file():
        raise VpubError("invalid_video", f"Video file not found: {args.video}")
    if not args.visibility and not args.draft_only:
        raise VpubError("visibility_required", "Say who can see it: --visibility public or private, or --draft-only to save it unsent.")
    if args.schedule and (args.draft_only or args.visibility != "public"):
        raise VpubError("invalid_schedule", "A schedule is when it goes public, so use it with --visibility public and without --draft-only.")
    folder = Path(args.folder).resolve() if args.folder else video.with_name(video.stem + "-crosscast")
    if folder.exists():
        raise VpubError("already_exists", f"{folder} is already there, and nothing was changed.",
                        f'If it is this video\'s, write its copy.json and run vpub go "{folder}". Otherwise choose another place with --folder.')
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        # A folder either appears complete or not at all, so a refusal leaves nothing to trip over next time.
        work = Path(tempfile.mkdtemp(prefix=".crosscast-", dir=folder.parent))
    except PermissionError:
        raise VpubError("folder_not_writable", f"Can't write to {folder.parent}.", "Choose a place with --folder.")
    try:
        answer = fill(work, args, video, folder)
        when_the_file_is_free(lambda: os.replace(work, folder))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return answer


def fill(work, args, video, folder):
    from .covers import check_supplied
    manifest = work / "publish.json"
    words = ["init", "--video", video, "--out", manifest, f"--language={args.language}", f"--platforms={args.platforms}", f"--ai-generated={args.ai_generated}"]
    step(*words, *([f"--visibility={args.visibility}"] if args.visibility else []), *(["--draft-only"] if args.draft_only else []))
    value = read_json(manifest)
    if args.schedule:
        if "instagram" in value["platforms"]:
            raise VpubError("unsupported_schedule", "Instagram publishes straight away, so it can't take a schedule.", "Prepare it separately without --schedule.")
        for name, options in value["platforms"].items():
            options["schedule"] = args.schedule
            if name == "youtube":
                options["visibility"] = "private"  # YouTube keeps a scheduled video private until its time.
        atomic_json(manifest, value)
    if args.cover or args.portrait:
        check_supplied(args.cover, args.portrait, value["platforms"])
        atomic_json(work / "covers.json", {name: str(Path(path).resolve()) for name, path in (("cover", args.cover), ("portrait", args.portrait)) if path})
    source = "\n\n".join([*([args.about] if args.about else []), *([Path(args.source).read_text(encoding="utf-8-sig")] if args.source else [])])
    if source:
        (work / "source.txt").write_text(source, encoding="utf-8")
    job = step("metadata", "request", manifest, *(["--source", work / "source.txt"] if source else []), "--out", work / "request.json")
    template = {"platforms": job["response_template"]["platforms"]}
    atomic_json(work / "copy.json", template)
    return {"state": "NEEDS_COPY", "folder": str(folder), "copy_file": str(folder / "copy.json"), "copy_template": template,
            "targets": job["targets"], "instructions": job["host_instructions"], "platform_writes": 0,
            "publishing": {name: {field: options[field] for field in ("visibility", "ai_generated", "made_for_kids", "schedule", "submission_mode") if field in options} for name, options in value["platforms"].items()},
            "next": f'Write the words into copy.json, then run: vpub go "{folder}"'}


def unwritten(words):
    if not isinstance(words, dict) or not isinstance(words.get("platforms"), dict):
        raise VpubError("invalid_metadata", "copy.json needs its platforms, as vpub prepare wrote it.")
    return [f"{platform}.{field}" for platform, text in words["platforms"].items() if isinstance(text, dict)
            for field in ("title", "category_id") if field in text and not str(text[field]).strip()]


def build(folder, words):
    """The folder's plan with its words in and its covers fitted, made again from publish.json and copy.json on every run.

    Where it goes depends only on those inputs, so the plan and its confirmation code stay the same from one run to the next.
    Nothing already there is trusted: changes belong in publish.json and copy.json, and a changed build file is set aside, never used.
    """
    manifest, source = folder / "publish.json", folder / "source.txt"
    covers = read_json(folder / "covers.json") if (folder / "covers.json").is_file() else {}
    place = folder / "build" / digest({"manifest": read_json(manifest), "copy": words["platforms"], "source": source.read_text(encoding="utf-8") if source.is_file() else "",
                                       "covers": {name: file_sha(path) for name, path in covers.items()}})[:12]
    place.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".words-", dir=place, ignore_cleanup_errors=True) as scratch:
        scratch = Path(scratch)
        step("metadata", "request", manifest, *(["--source", source] if source.is_file() else []), "--out", scratch / "request.json")
        atomic_json(scratch / "copy.json", {"request_id": read_json(scratch / "request.json")["request_id"], "platforms": words["platforms"]})
        step("metadata", "apply", manifest, "--request", scratch / "request.json", "--response", scratch / "copy.json", "--out", scratch / "localized.json")
        for name in ("request.json", "copy.json", "localized.json"):
            when_the_file_is_free(lambda name=name: os.replace(scratch / name, place / name))
    if not covers:
        return place / "localized.json"
    fit = ("covers", "prepare", place / "localized.json", "--source", covers["cover"], *(["--portrait", covers["portrait"]] if "portrait" in covers else []),
           "--out-dir", place / "covers", "--out", place / "ready.json")
    try:
        step(*fit)
    except VpubError as error:
        if error.code != "already_exists":
            raise
        # Fitted covers or ready.json that no longer match what made them, from a hand edit or a cut-off run. Kept for a look, then made again.
        aside = Path(tempfile.mkdtemp(prefix="changed-", dir=place))
        for name in ("covers", "ready.json"):
            if (place / name).exists():
                when_the_file_is_free(lambda name=name: os.replace(place / name, aside / name))
        step(*fit)
    return place / "ready.json"


def go(folder, confirm=None, dry_run=False):
    folder = Path(folder).resolve()
    if not (folder / "publish.json").is_file():
        raise VpubError("not_prepared", f"{folder} isn't a prepared folder.", "Start with vpub prepare VIDEO.")
    words = read_json(folder / "copy.json")
    blanks = unwritten(words)
    if blanks:
        raise VpubError("copy_not_written", "Nothing was published, because copy.json still has blanks: " + ", ".join(blanks) + ".",
                        f'Write them in "{folder / "copy.json"}" and run this again. request.json beside it has the limits and instructions.')
    ready = build(folder, words)
    if dry_run:
        from . import cli
        answer = step("publish", ready, "--dry-run")
        if "next" not in answer and cli.finished(answer):
            drafts = sorted(name for name, options in answer.get("platforms", {}).items() if options.get("submission_mode") == "draft_only")
            what = ("save it unpublished, as a draft or preview" if len(drafts) == len(answer.get("platforms", {}))
                    else f"publish it, with {', '.join(drafts)} saved only as a draft" if drafts else "publish it")
            answer["next"] = f'Nothing was uploaded. To {what}, run: vpub go "{folder}"'
        return answer
    plan = step("plan", ready)
    if plan.get("text_flags") and not confirm:
        return {"ready": False, "state": "NEEDS_A_PERSON", "text_flags": plan["text_flags"], "text_flags_note": plan["text_flags_note"],
                "confirmation": plan["confirmation"], "platform_writes": 0,
                "next": f'Tell the user what was flagged. If they approve it for the configured audience: vpub go "{folder}" --confirm {plan["confirmation"]}. Otherwise change copy.json and run vpub go again.'}
    return step("publish", ready, "--confirm", confirm or plan["confirmation"])
