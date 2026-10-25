"""Point the Hugging Face Space at one image (docs/day7-design.md section 2.3).

Writes the Space's README.md (its YAML header: Docker SDK, port 8000) and a one-line Dockerfile
`FROM <image>@sha256:<digest>`, so the Space runs exactly the image CI built and pushed (a digest,
never a tag the Space could have cached), then waits until the Space is running. Nothing secret
goes to the Space. Needs HF_TOKEN with write access to the Space; huggingface_hub is installed by
the workflow (`uv run --with huggingface_hub==2.2.0`).

    python tools/ops/deploy_space.py --space owner/name \
        --image ghcr.io/owner/pundit-copilot-space@sha256:<digest> --repo-url https://github.com/...
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "deploy" / "space" / "hf" / "README.md"
IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
SPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
# PAUSED does not end by waiting: the owner paused the Space, or the account has no quota for
# its hardware (a new account can get `limit=0` for the free cpu-basic)
FAILED = {
    "BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE", "DELETING", "STOPPED", "PAUSED",
}  # fmt: skip
POLL_S = 10.0
FIRST_POLL_S = 20.0  # the Space takes a few seconds to notice a new commit
PRIVATE_HINT = (
    "on a first deploy, the GHCR packages are private and the Space cannot pull them: make "
    "pundit-copilot-space and pundit-copilot public, then run space.yml again "
    "(docs/day7-design.md section 3.3, step 6)"
)
QUOTA_HINT = (
    "the Hugging Face account may not run this hardware: see Settings -> Billing of the account; "
    "the image is in GHCR and a new run of space.yml deploys it once the Space can start"
)


class DeployError(Exception):
    pass


def check_image(image: str) -> str:
    if not IMAGE.match(image):
        raise DeployError(
            f"--image must be pinned by digest (registry/name@sha256:...), got {image!r}"
        )
    return image


def check_space(space: str) -> str:
    if not SPACE.match(space):
        raise DeployError(f"--space must be owner/name, got {space!r}")
    return space


def space_files(image: str, repo_url: str) -> dict[str, bytes]:
    """The two files of the Space repository, built from the template in deploy/space/hf."""
    readme = README.read_text(encoding="utf-8")
    readme = readme.replace("{repo_url}", repo_url or "(not published yet)").replace(
        "{image}", image
    )
    dockerfile = (
        "# Written by tools/ops/deploy_space.py: the image CI built, pinned by digest.\n"
        f"FROM {check_image(image)}\n"
    )
    return {"README.md": readme.encode("utf-8"), "Dockerfile": dockerfile.encode("utf-8")}


def stage_verdict(stage: str) -> str:
    """'done', 'wait' or 'failed' for a stage of the Space runtime."""
    if stage == "RUNNING":
        return "done"
    if stage in FAILED:
        return "failed"
    return "wait"  # BUILDING, APP_STARTING, RUNNING_BUILDING, RUNNING_APP_STARTING


def deploy(space: str, image: str, repo_url: str, timeout_s: float) -> dict[str, Any]:
    from huggingface_hub import CommitOperationAdd, HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise DeployError("HF_TOKEN is not set")
    api = HfApi(token=token)
    before = api.space_info(space).sha
    files = space_files(image, repo_url)
    operations = [CommitOperationAdd(path, data) for path, data in files.items()]
    commit = api.create_commit(
        space,
        operations,
        repo_type="space",
        commit_message=f"Deploy {image.rsplit('@', 1)[1][:19]}",
    )
    info = api.space_info(space)
    if info.sdk != "docker":
        raise DeployError(
            f"the Space's SDK is {info.sdk!r}, not docker: create it again as Docker -> Blank "
            "(docs/day7-design.md section 3.3)"
        )
    changed = info.sha != before
    changes = "new image" if changed else "image unchanged"
    print(f"commit {commit.oid} on {space} ({changes})", flush=True)
    deadline = time.monotonic() + timeout_s
    time.sleep(FIRST_POLL_S if changed else 0)
    while True:
        runtime = api.get_space_runtime(space)
        stage = str(getattr(runtime.stage, "value", runtime.stage))  # a str, or a SpaceStage
        verdict = stage_verdict(stage)
        print(f"stage {stage}", flush=True)  # in order with the error on stderr
        if verdict == "done":
            return {"space": space, "image": image, "commit": commit.oid, "stage": stage}
        if verdict == "failed":
            message = runtime.raw.get("errorMessage") or "see the Logs tab of the Space"
            if stage == "BUILD_ERROR":
                message += f" ({PRIVATE_HINT})"
            elif "quota" in message.lower():
                message += f" ({QUOTA_HINT})"
            raise DeployError(f"the Space is {stage}: {message}")
        if time.monotonic() > deadline:
            raise DeployError(f"the Space is still {stage} after {timeout_s:.0f} s")
        time.sleep(POLL_S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--space", required=True, help="owner/name of the Space")
    parser.add_argument("--image", required=True, help="registry/name@sha256:<digest>")
    parser.add_argument("--repo-url", default="", help="link to the source on the Space's page")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait")
    args = parser.parse_args(argv)
    try:
        result = deploy(
            check_space(args.space), check_image(args.image), args.repo_url, args.timeout
        )
    except DeployError as exc:
        print(f"deploy_space: {exc}", file=sys.stderr)
        return 1
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(
                f"Space `{result['space']}` runs `{result['image']}` ({result['stage']}).\n\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
