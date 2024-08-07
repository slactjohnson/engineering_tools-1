#!/usr/bin/python3
"""
ioc-deploy is a script for building and deploying ioc tags from github.
It will create a shallow clone of your IOC in the standard release area at the correct path and "make" it.
If the tag directory already exists, the script will exit.

Example command:
"ioc-deploy -n ioc-foo-bar -r R1.0.0"

This will clone the repository to the default ioc directory and run make
using the currently set EPICS environment variables.

With default settings this will clone

from https://github.com/pcdshub/ioc-foo-bar
to /cds/group/pcds/epics/ioc/foo/bar/R1.0.0

then cd and make.
"""

import argparse
import enum
import logging
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

EPICS_SITE_TOP_DEFAULT = "/cds/group/pcds/epics"
GITHUB_ORG_DEFAULT = "pcdshub"

logger = logging.getLogger("ioc-deploy")


if sys.version_info >= (3, 7, 0):
    import dataclasses

    @dataclasses.dataclass(frozen=True)
    class CliArgs:
        """
        Argparse argument types for type checking.
        """

        name: str = ""
        release: str = ""
        ioc_dir: str = ""
        github_org: str = ""
        auto_confirm: bool = False
        dry_run: bool = False
        verbose: bool = False
        version: bool = False
else:
    from types import SimpleNamespace as CliArgs


def get_parser() -> argparse.ArgumentParser:
    current_default_target = str(
        Path(os.environ.get("EPICS_SITE_TOP", EPICS_SITE_TOP_DEFAULT)) / "ioc"
    )
    current_default_org = os.environ.get("GITHUB_ORG", GITHUB_ORG_DEFAULT)
    parser = argparse.ArgumentParser(prog="ioc-deploy", description=__doc__)
    parser.add_argument(
        "--version", action="store_true", help="Show version number and exit."
    )
    parser.add_argument(
        "--name",
        "-n",
        action="store",
        default="",
        help="The name of the repository to deploy. This is a required argument. If it does not exist on github, we'll also try prepending with 'ioc-common-'.",
    )
    parser.add_argument(
        "--release",
        "-r",
        action="store",
        default="",
        help="The version of the IOC to deploy. This is a required argument.",
    )
    parser.add_argument(
        "--ioc-dir",
        "-i",
        action="store",
        default=current_default_target,
        help=f"The directory to deploy IOCs in. This defaults to $EPICS_SITE_TOP/ioc, or {EPICS_SITE_TOP_DEFAULT}/ioc if the environment variable is not set. With your current environment variables, this defaults to {current_default_target}.",
    )
    parser.add_argument(
        "--github_org",
        "--org",
        action="store",
        default=current_default_org,
        help=f"The github org to deploy IOCs from. This defaults to $GITHUB_ORG, or {GITHUB_ORG_DEFAULT} if the environment variable is not set. With your current environment variables, this defaults to {current_default_org}.",
    )
    parser.add_argument(
        "--auto-confirm",
        "--confirm",
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation promps, automatically saying yes to each one.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not deploy anything, just print what would have been done.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        "--debug",
        action="store_true",
        help="Display additional debug information.",
    )
    return parser


class ReturnCode(enum.IntEnum):
    SUCCESS = 0
    EXCEPTION = 1
    NO_CONFIRM = 2


def main(args: CliArgs) -> int:
    """
    All main steps of the script.

    Will either return an int return code or raise.
    """
    if not (args.name and args.release):
        raise ValueError(
            "Must provide both --name and --release. Check ioc-deploy --help for usage."
        )

    logger.info("Running ioc-deploy: checking inputs")
    upd_name = finalize_name(
        name=args.name, github_org=args.github_org, verbose=args.verbose
    )
    upd_rel = finalize_tag(
        name=upd_name,
        github_org=args.github_org,
        release=args.release,
        verbose=args.verbose,
    )
    deploy_dir = get_target_dir(name=upd_name, ioc_dir=args.ioc_dir, release=upd_rel)

    logger.info(f"Deploying {args.github_org}/{upd_name} at {upd_rel} to {deploy_dir}")
    if Path(deploy_dir).exists():
        raise RuntimeError(f"Deploy directory {deploy_dir} already exists! Aborting.")
    if not args.auto_confirm:
        user_text = input("Confirm release source and target? y/n\n")
        if not user_text.strip().lower().startswith("y"):
            return ReturnCode.NO_CONFIRM
    logger.info(f"Cloning IOC to {deploy_dir}")
    rval = clone_repo_tag(
        name=upd_name,
        github_org=args.github_org,
        release=upd_rel,
        deploy_dir=deploy_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    if rval != ReturnCode.SUCCESS:
        logger.error(f"Nonzero return value {rval} from git clone")
        return rval

    logger.info(f"Building IOC at {deploy_dir}")
    rval = make_in(deploy_dir=deploy_dir, dry_run=args.dry_run)
    if rval != ReturnCode.SUCCESS:
        logger.error(f"Nonzero return value {rval} from make")
        return rval
    return ReturnCode.SUCCESS


def finalize_name(name: str, github_org: str, verbose: bool) -> str:
    """
    Check if name is present in org and is well-formed.

    If the name is present, return it.
    If the name is not present and the correct name can be guessed, guess.
    If the name is not present and cannot be guessed, raise.

    A name is well-formed if it starts with "ioc", is hyphen-delimited,
    and has at least three sections.

    For example, "ioc-common-gigECam" is a well-formed name for the purposes
    of an IOC deployment. "ads-ioc" and "pcdsdevices" are not.

    However, "ads-ioc" will resolve to "ioc-common-ads-ioc".
    Only common IOCs will be automatically discovered using this method.
    """
    split_name = name.split("-")
    if len(split_name) < 3 or split_name[0] != "ioc":
        new_name = f"ioc-common-{name}"
        logger.warning(f"{name} is not an ioc name, trying {new_name}")
        name = new_name
    logger.debug(f"Checking for {name} in org {github_org}")
    with TemporaryDirectory() as tmpdir:
        try:
            _clone(
                name=name, github_org=github_org, working_dir=tmpdir, verbose=verbose
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f"Error cloning repo, make sure {name} exists in {github_org} and check your permissions!"
            ) from exc
    return name


def finalize_tag(name: str, github_org: str, release: str, verbose: bool) -> str:
    """
    Check if release is present in the org.

    We'll try a few prefix options in case the user has a typo.
    In order of priority with no repeats:
    - user's input
    - R1.0.0
    - v1.0.0
    - 1.0.0
    """
    try_release = [release]
    if release.startswith("R"):
        try_release.extend([f"v{release[1:]}", f"{release[1:]}"])
    elif release.startswith("v"):
        try_release.extend([f"R{release[1:]}", f"{release[1:]}"])
    elif release[0].isalpha():
        try_release.extend([f"R{release[1:]}", f"v{release[1:]}", f"{release[1:]}"])
    else:
        try_release.extend([f"R{release}", f"v{release}"])

    with TemporaryDirectory() as tmpdir:
        for rel in try_release:
            logger.debug(f"Checking for release {rel} in {github_org}/{name}")
            try:
                _clone(
                    name=name,
                    github_org=github_org,
                    release=rel,
                    working_dir=tmpdir,
                    target_dir=rel,
                    verbose=verbose,
                )
            except subprocess.CalledProcessError:
                logger.warning(f"Did not find release {rel} in {github_org}/{name}")
            else:
                logger.info(f"Release {rel} exists in {github_org}/{name}")
                return rel
    raise ValueError(f"Unable to find {release} in {github_org}/{name}")


def get_target_dir(name: str, ioc_dir: str, release: str) -> str:
    """
    Return the directory we'll deploy the IOC in.

    This will look something like:
    /cds/group/pcds/epics/ioc/common/gigECam/R1.0.0
    """
    split_name = name.split("-")
    return str(Path(ioc_dir) / split_name[1] / "-".join(split_name[2:]) / release)


def clone_repo_tag(
    name: str,
    github_org: str,
    release: str,
    deploy_dir: str,
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Create a shallow clone of the git repository in the correct location.
    """
    # Make sure the parent dir exists
    parent_dir = Path(deploy_dir).resolve().parent
    if dry_run:
        logger.info(f"Dry-run: make {parent_dir} if not existing.")
    else:
        logger.debug(f"Ensure {parent_dir} exists")
        parent_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        logger.debug("Dry-run: skip git clone")
        return ReturnCode.SUCCESS
    else:
        return _clone(
            name=name,
            github_org=github_org,
            release=release,
            target_dir=deploy_dir,
            verbose=verbose,
        ).returncode


def make_in(deploy_dir: str, dry_run: bool) -> int:
    """
    Shell out to make in the deploy dir
    """
    if dry_run:
        logger.info(f"Dry-run: skipping make in {deploy_dir}")
        return ReturnCode.SUCCESS
    else:
        return subprocess.run(["make"], cwd=deploy_dir).returncode


def get_version() -> str:
    """
    Determine what version of engineering_tools is being used
    """
    # Possibility 1: git clone (yours)
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent), "describe", "--tags"],
            universal_newlines=True,
        ).strip()
    except subprocess.CalledProcessError:
        ...
    # Possibility 2: release dir (someone elses)
    ver = str(Path(__file__).resolve().parent.parent.stem)
    if ver.startswith("R"):
        return ver
    else:
        # We tried our best
        return "unknown.dev"


def _clone(
    name: str,
    github_org: str,
    release: str = "",
    working_dir: str = "",
    target_dir: str = "",
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """
    Clone the repo or raise a subprocess.CalledProcessError
    """
    cmd = ["git", "clone", f"git@github.com:{github_org}/{name}", "--depth", "1"]
    if release:
        cmd.extend(["-b", release])
    if target_dir:
        cmd.append(target_dir)
    kwds = {"check": True}
    if working_dir:
        kwds["cwd"] = working_dir
    if not verbose:
        kwds["stdout"] = subprocess.PIPE
        kwds["stderr"] = subprocess.PIPE
    logger.debug(f"Calling '{' '.join(cmd)}' with kwargs {kwds}")
    return subprocess.run(cmd, **kwds)


def _main() -> int:
    """
    Thin wrapper of main() for log setup, args handling, and high-level error handling.
    """
    args = CliArgs(**vars(get_parser().parse_args()))
    if args.verbose:
        level = logging.DEBUG
        fmt = "%(levelname)-8s %(name)s:%(lineno)d: %(message)s"
    else:
        level = logging.INFO
        fmt = "%(levelname)-8s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)
    logger.debug(f"args are {args}")
    try:
        if args.version:
            print(get_version())
            return ReturnCode.SUCCESS
        return main(args)
    except Exception as exc:
        logger.error(exc)
        logger.debug("Traceback", exc_info=True)
        return ReturnCode.EXCEPTION


if __name__ == "__main__":
    exit(_main())
