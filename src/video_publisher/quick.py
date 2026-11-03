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
    from .preferences import platform_list, read as preferences
    choices = preferences()
    platforms = platform_list(args.platforms) if args.platforms is not None else choices['preferred_platforms']
    if not platforms:
        raise VpubError('platforms_required', 'Choose where this video goes; no platforms have been saved.',
                        'Ask the user which platforms they use, then pass --platforms PLATFORM_LIST or save them with vpub preferences --platforms PLATFORM_LIST. The CLI never prompts.')
    args.platforms = ','.join(platforms)
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
        answer = fill(work, args, video, folder, choices['show_first'])
        when_the_file_is_free(lambda: os.replace(work, folder))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return answer


def fill(work, args, video, folder, show_first=None):
    from .covers import check_supplied
    manifest = work / "publish.json"
    words = ["init", "--video", video, "--out", manifest, f"--language={args.language}", f"--platforms={args.platforms}", f"--ai-generated={args.ai_generated}"]
    step(*words, *([f"--visibility={args.visibility}"] if args.visibility else []), *(["--draft-only"] if args.draft_only else []))
    value = read_json(manifest)
    if show_first is True:
        value['review_before_publish'] = True
        atomic_json(manifest, value)
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
    from .rehearsal import require_local_prerequisites
    require_local_prerequisites({**value, "cover": args.cover})
    source = "\n\n".join([*([args.about] if args.about else []), *([Path(args.source).read_text(encoding="utf-8-sig")] if args.source else [])])
    if source:
        (work / "source.txt").write_text(source, encoding="utf-8")
    job = step("metadata", "request", manifest, *(["--source", work / "source.txt"] if source else []), "--out", work / "request.json")
    template = {"platforms": job["response_template"]["platforms"]}
    atomic_json(work / "copy.json", template)
    atomic_json(work / "review.json", {'schema_version': 1, 'show_first': show_first})
    instructions = [line.replace('Return request_id plus platforms with', 'Keep only the top-level platforms object in copy.json; go adds request_id. Each platform entry needs')
                    for line in job['host_instructions']]
    publishing = {}
    for name, options in value['platforms'].items():
        publishing[name] = {field: options[field] for field in ('visibility', 'ai_generated', 'made_for_kids', 'schedule', 'submission_mode') if field in options}
        if options['visibility'] == 'account':
            publishing[name]['visibility_note'] = 'Follows the account audience; public if the account is public. --visibility does not make this platform private.'
        if name == 'instagram':
            publishing[name]['cover_note'] = 'Set the cover in Instagram on your phone after posting; CrossCast does not apply it automatically.'
        if name == 'youtube':
            publishing[name]['audience_note'] = 'made_for_kids defaults to false. If the video is made for kids, set platforms.youtube.made_for_kids to true in publish.json before go.'
    from .publication_review import requirements
    review = requirements(value)
    return {"state": "NEEDS_COPY", "folder": str(folder), "copy_file": str(folder / "copy.json"), "copy_template": template,
            "targets": job["targets"], "instructions": instructions, "platform_writes": 0,
            "publishing": publishing, **review,
            'review_note': 'First publication on each platform and saved show-first require review. Stop, show the plan and wait for the person to say yes, then use --confirm CODE --person-approved. This is caller attestation, not independent proof of consent; preferences never authorize publishing.',
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


def review_required(folder):
    from .preferences import read as preferences
    from .publication_review import folder_choice
    # Turning a preference off later cannot waive a review already requested for this folder.
    return folder_choice(folder) or preferences()['show_first'] is True


def go(folder, confirm=None, dry_run=False, workers=1, *, person_approved=False):
    folder = Path(folder).resolve()
    if not (folder / "publish.json").is_file():
        raise VpubError("not_prepared", f"{folder} isn't a prepared folder.", "Start with vpub prepare VIDEO.")
    words = read_json(folder / "copy.json")
    blanks = unwritten(words)
    if blanks:
        raise VpubError("copy_not_written", "Nothing was published, because copy.json still has blanks: " + ", ".join(blanks) + ".",
                        f'Write them in "{folder / "copy.json"}" and run this again. request.json beside it has the limits and instructions.')
    ready = build(folder, words)
    show_first = review_required(folder)
    if dry_run:
        from . import cli
        answer = step("publish", ready, "--dry-run")
        show_first = show_first or answer.get('review_required', False)
        answer['review_required'] = show_first
        answer['confirmation_note'] = ('This is the current plan code, not publishing authorization. Stop, show the plan and wait for the person to say yes. For a first publication on a platform, show-first plan or flagged text, use --confirm CODE with --person-approved only after that yes. This is caller attestation; the CLI cannot verify who supplied it. Returning customers without those requirements use ordinary go. Changed plans need a new code.')
        if "next" not in answer and cli.finished(answer):
            drafts = sorted(name for name, options in answer.get("platforms", {}).items() if options.get("submission_mode") == "draft_only")
            what = ("save it unpublished, as a draft or preview" if len(drafts) == len(answer.get("platforms", {}))
                    else f"publish it, with {', '.join(drafts)} saved only as a draft" if drafts else "publish it")
            if show_first or answer.get('text_flags'):
                answer['next'] = f'Nothing was uploaded. Stop, show this plan and any flagged text to the user, and wait for their yes. Only after approval for its configured audience, to {what}, run: vpub go "{folder}" --confirm {answer["confirmation"]} --person-approved'
            else:
                answer["next"] = f'Nothing was uploaded. To {what}, run: vpub go "{folder}"'
        return answer
    plan = step("plan", ready)
    show_first = show_first or plan.get('review_required', False)
    if confirm is not None and confirm != plan['confirmation']:
        raise VpubError('confirmation_required', 'The confirmation hash must match the current plan.',
                        'Show the changed plan to the user and wait for approval before continuing.')
    if (show_first or plan.get("text_flags") or person_approved) and (not confirm or person_approved is not True):
        return {"ready": False, "state": "NEEDS_A_PERSON", "text_flags": plan.get("text_flags", {}), "text_flags_note": plan.get("text_flags_note"),
                'review_required': show_first, 'review_reasons': plan.get('review_reasons', []),
                'first_publication_platforms': plan.get('first_publication_platforms', []), 'review': plan,
                "confirmation": plan["confirmation"], "platform_writes": 0,
                "next": f'Stop and show the user this plan, including its text, audience, AI labels, covers and any flagged text. Wait for their yes. Only then: vpub go "{folder}" --confirm {plan["confirmation"]} --person-approved. This attests their approval; the CLI cannot verify who supplied it. Otherwise change copy.json or publish.json and run vpub go again.'}
    answer = step("publish", ready, "--confirm", confirm or plan["confirmation"], "--workers", workers,
                  *(['--person-approved'] if person_approved is True else []))
    if answer.get('state') == 'NEEDS_A_PERSON':
        answer['next'] = f'Stop and show this exact plan to the user. Wait for their yes. Only then: vpub go "{folder}" --confirm {answer["confirmation"]} --person-approved. This is caller attestation, not independent proof of consent.'
    return answer
