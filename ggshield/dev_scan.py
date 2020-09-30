import concurrent.futures
import os
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Set

import click
from pygitguardian import GGClient

from ggshield.output import OutputHandler
from ggshield.output.text.message import build_commit_info
from ggshield.scan import Commit, ScanCollection

from .config import CPU_COUNT, Config
from .filter import path_filter_set
from .git_shell import GIT_PATH, check_git_dir, get_list_commit_SHA, is_git_dir, shell
from .path import get_files_from_paths
from .utils import REGEX_GIT_URL


@contextmanager
def cd(newdir: str) -> Iterator[None]:
    prevdir = os.getcwd()
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)


def scan_repo_path(
    client: GGClient, output_handler: OutputHandler, config: Config, repo_path: str
) -> int:  # pragma: no cover
    try:
        with cd(repo_path):
            if not is_git_dir():
                raise click.ClickException(f"{repo_path} is not a git repository")

            return scan_commit_range(
                client=client,
                commit_list=get_list_commit_SHA("--all"),
                output_handler=output_handler,
                verbose=config.verbose,
                filter_set=path_filter_set(Path(os.getcwd()), []),
                matches_ignore=config.matches_ignore,
                all_policies=config.all_policies,
            )
    except click.exceptions.Abort:
        return 0
    except Exception as error:
        if config.verbose:
            traceback.print_exc()
        raise click.ClickException(str(error))


@click.command()
@click.argument("repository", nargs=1, type=click.STRING, required=True)
@click.pass_context
def repo_cmd(ctx: click.Context, repository: str) -> int:  # pragma: no cover
    """
    scan a REPOSITORY at a given URL or path

    REPOSITORY is the clone URI or the path of the repository to scan.
    Examples:

    ggshield scan repo git@github.com:GitGuardian/gg-shield.git

    ggshield scan repo /repositories/gg-shield
    """
    config: Config = ctx.obj["config"]
    client: GGClient = ctx.obj["client"]
    if os.path.isdir(repository):
        return scan_repo_path(
            client=client,
            output_handler=ctx.obj["output_handler"],
            config=config,
            repo_path=repository,
        )

    if REGEX_GIT_URL.match(repository):
        with tempfile.TemporaryDirectory() as tmpdirname:
            shell([GIT_PATH, "clone", repository, tmpdirname])
            return scan_repo_path(
                client=client,
                output_handler=ctx.obj["output_handler"],
                config=config,
                repo_path=tmpdirname,
            )

    raise click.ClickException(f"{repository} is neither a valid path nor a git URL")


@click.command()
@click.argument("commit_range", nargs=1, type=click.STRING)
@click.pass_context
def range_cmd(ctx: click.Context, commit_range: str) -> int:  # pragma: no cover
    """
    scan a defined COMMIT_RANGE in git.

    git rev-list COMMIT_RANGE to list several commits to scan.
    example: ggshield scan commit-range HEAD~1...
    """
    config = ctx.obj["config"]
    try:
        commit_list = get_list_commit_SHA(commit_range)
        if not commit_list:
            raise click.ClickException("invalid commit range")
        if config.verbose:
            click.echo(f"Commits to scan: {len(commit_list)}")

        return scan_commit_range(
            client=ctx.obj["client"],
            commit_list=commit_list,
            output_handler=ctx.obj["output_handler"],
            verbose=config.verbose,
            filter_set=ctx.obj["filter_set"],
            matches_ignore=config.matches_ignore,
            all_policies=config.all_policies,
        )
    except click.exceptions.Abort:
        return 0
    except Exception as error:
        if config.verbose:
            traceback.print_exc()
        raise click.ClickException(str(error))


@click.command()
@click.argument("precommit_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def precommit_cmd(
    ctx: click.Context, precommit_args: List[str]
) -> int:  # pragma: no cover
    """
    scan as a pre-commit git hook.
    """
    config = ctx.obj["config"]
    output_handler: OutputHandler = ctx.obj["output_handler"]
    try:
        check_git_dir()
        results = Commit(filter_set=ctx.obj["filter_set"]).scan(
            client=ctx.obj["client"],
            matches_ignore=config.matches_ignore,
            all_policies=config.all_policies,
            verbose=config.verbose,
        )

        return output_handler.process_scan(
            ScanCollection(id="pre-commit", results=results)
        )[1]
    except click.exceptions.Abort:
        return 0
    except Exception as error:
        if config.verbose:
            traceback.print_exc()
        raise click.ClickException(str(error))


@click.command()
@click.argument(
    "paths", nargs=-1, type=click.Path(exists=True, resolve_path=True), required=True
)
@click.option("--recursive", "-r", is_flag=True, help="Scan directory recursively")
@click.option("--yes", "-y", is_flag=True, help="Confirm recursive scan")
@click.pass_context
def path_cmd(
    ctx: click.Context, paths: List[str], recursive: bool, yes: bool
) -> int:  # pragma: no cover
    """
    scan files and directories.
    """
    config = ctx.obj["config"]
    output_handler: OutputHandler = ctx.obj["output_handler"]
    try:
        files = get_files_from_paths(
            paths=paths,
            paths_ignore=config.paths_ignore,
            recursive=recursive,
            yes=yes,
            verbose=config.verbose,
        )
        results = files.scan(
            client=ctx.obj["client"],
            matches_ignore=config.matches_ignore,
            all_policies=config.all_policies,
            verbose=config.verbose,
        )
        scan = ScanCollection(id="path_scan", results=results)

        return output_handler.process_scan(scan)[1]
    except click.exceptions.Abort:
        return 0
    except Exception as error:
        if config.verbose:
            traceback.print_exc()
        raise click.ClickException(str(error))


def scan_commit(
    commit: Commit,
    client: GGClient,
    verbose: bool,
    matches_ignore: Iterable[str],
    all_policies: bool,
) -> ScanCollection:  # pragma: no cover
    results = commit.scan(
        client=client,
        matches_ignore=matches_ignore,
        all_policies=all_policies,
        verbose=verbose,
    )

    return ScanCollection(
        "commit", results=results, optional_info=build_commit_info(commit)
    )


def scan_commit_range(
    client: GGClient,
    commit_list: List[str],
    output_handler: OutputHandler,
    verbose: bool,
    filter_set: Set[str],
    matches_ignore: Iterable[str],
    all_policies: bool,
) -> int:  # pragma: no cover
    """
    Scan every commit in a range.

    :param client: Public Scanning API client
    :param commit_range: Range of commits to scan (A...B)
    :param verbose: Display successfull scan's message
    """
    return_code = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=CPU_COUNT) as executor:
        future_to_process = [
            executor.submit(
                scan_commit,
                Commit(sha, filter_set),
                client,
                verbose,
                matches_ignore,
                all_policies,
            )
            for sha in commit_list
        ]

        scans: List[ScanCollection] = []
        for future in concurrent.futures.as_completed(future_to_process):
            scans.append(future.result())

        return_code = output_handler.process_scan(
            ScanCollection(id="commit-range", scans=scans)
        )[1]
    return return_code
