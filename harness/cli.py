"""`exeris-agent` — the first client of the harness, and in V0 the only one.

The command a person types is `open-run`; everything else in V0 exists so that what `open-run`
produced can be found again. The two lines it prints are the whole handover: source the run's
environment, change into the run's worktree, and the shell — and anything launched from it — is the
execution identity's, inside the tree that is the run's attribution boundary.

`close-run` and `flush` are registered here and refuse. A subcommand that exists and says it is not
in this version is honest about the shape of the contract; one that is missing makes a person guess
whether they mistyped it.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

from . import ROOT, VERSION, config, runstate, token, ulid, worktree

PROVIDERS = ("claude", "codex", "gemini")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

#: Run records classify by declared principal, never by a login that looks bot-shaped, so the kind
#: is written down beside the login rather than inferred from it later.
PRINCIPAL_KIND = "app"


class Refused(Exception):
    """An invocation the harness will not act on. Exits 2; nothing has been created."""


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_ref(task: str, run_id: str) -> str:
    """`reg:<id>` as given, or the non-joinable `adhoc:<ULID>`.

    An adhoc run carries no task text anywhere: the escape makes an unplanned run capturable, and
    the marker is what keeps the validator from ever joining it to a planned one.
    """
    if task == "adhoc":
        return f"adhoc:{run_id}"
    if task.startswith("reg:") and len(task) > len("reg:"):
        return task
    raise Refused(f"--task {task!r} is neither `reg:<id>` nor `adhoc`")


def _pairing(args) -> dict:
    """The arms of a paired comparison, or nothing.

    Pairing joins a run to the other arms of its group, and a run with no registry entry has
    nothing to be joined to — so `--group` on an adhoc run is refused rather than recorded as a
    group of one. The four names are the run record's own, and all four are required together,
    because a group declared without the plan it is an arm of is a group recovered afterwards by
    query, which is the thing preregistration exists to prevent.
    """
    dependent = {"--arm": args.arm, "--arms-planned": args.arms_planned,
                 "--baseline": args.baseline}
    named = [flag for flag, value in dependent.items() if value is not None]
    if not args.group:
        if named:
            raise Refused(f"{', '.join(named)} without --group")
        return {}
    if args.task == "adhoc":
        raise Refused("--group requires --task reg:<id>; an adhoc run is never paired")
    missing = [flag for flag, value in dependent.items() if value is None]
    if missing:
        raise Refused(f"--group without {', '.join(missing)}; a pairing is all four or none")
    return {"group_id": args.group, "arm": args.arm,
            "arms_planned": args.arms_planned, "baseline": args.baseline}


def _prompt_digest(path: str | None) -> str | None:
    """The hash of the prompt as passed. The text is read to be hashed and never written down."""
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        raise Refused(f"--prompt-file {path}: {exc}") from None


def _scope(args, repo_config) -> str | None:
    """`--scope`, checked against the repository's vocabulary where it declares one."""
    if args.scope is None:
        return None
    if repo_config.scope and args.scope not in repo_config.scope:
        raise Refused(f"--scope {args.scope!r} is not in the vocabulary configured for "
                      f"{repo_config.name}: {', '.join(repo_config.scope)}")
    return args.scope


def _adapter(provider: str) -> str:
    launcher = os.path.join(ROOT, "adapters", provider, "launch.sh")
    if not os.path.isfile(launcher):
        raise Refused(f"no adapter for {provider} at {launcher}")
    return launcher


def cmd_open_run(args) -> int:
    cfg = config.load()

    # Everything that can be refused is refused before a directory or a token exists, so a bad
    # invocation leaves no run behind to be cleaned up or, worse, reported on.
    _task_ref(args.task, "0" * ulid.LENGTH)
    pairing = _pairing(args)
    prompt_sha256 = _prompt_digest(args.prompt_file)
    clone = worktree.resolve_clone(args.repo, cfg)
    slug = worktree.origin_slug(clone)
    scope = _scope(args, cfg.repo(slug.split("/", 1)[1]))
    base_branch = worktree.default_branch(clone)
    launcher = _adapter(args.provider) if args.launch else None

    # A pull request this run's work becomes carries exactly one `Owner:`, naming the member
    # accountable for it. The manifest is where that line will be built from, so a configuration
    # that names nobody is refused at the start of the run rather than at the end of its work.
    if not cfg.owner_login:
        raise Refused(f"{cfg.path} declares no owner_login; a run has to name the organisation "
                      f"member accountable for what it produces")

    # A clone that installs its own hooks loses them inside the run, whose hooks path is the one
    # that stamps every commit with the run id. Said out loud, because the alternative is a person
    # discovering it from a hook that quietly did not fire.
    installed_hooks = worktree.local_hooks_path(clone)
    if installed_hooks:
        print(f"exeris-agent: {clone} sets core.hooksPath to {installed_hooks}; inside this run "
              f"the run's own hooks path is in force and that one does not run", file=sys.stderr)

    run_id = ulid.new()
    run_dir = runstate.create(run_id)
    task = _task_ref(args.task, run_id)
    minted = None

    # Past this point a failure has something to undo. A half-opened run is a directory holding a
    # credential valid for the rest of the hour, belonging to a run that does not exist: `status`
    # would list it, nothing would revoke it. So the run is removed and the token given back.
    try:
        minted = token.mint(cfg)
        token.write(run_dir, minted)

        runstate.write_gitconfig(run_dir, user_name=cfg.bot_login, user_email=cfg.noreply_email,
                                 org=cfg.org)

        tree = os.path.join(run_dir, "wt")
        branch = f"agent/{ulid.short(run_id)}"
        base_sha = worktree.add(clone, tree, branch, base_branch)
        worktree.bind_identity(clone, tree, user_name=cfg.bot_login,
                               user_email=cfg.noreply_email,
                               settings=runstate.worktree_settings(run_dir, cfg.org))

        runstate.write_hook(run_dir, run_id)

        values = runstate.env_values(run_dir, run_id=run_id, token=minted["token"],
                                     user_name=cfg.bot_login, user_email=cfg.noreply_email)
        runstate.write_env(run_dir, values)

        manifest = {
            "run_id": run_id,
            "task": task,
            "repo": slug,
            "provider": args.provider,
            "started_at": _now(),
            "worktree": tree,
            "branch": branch,
            "base_sha": base_sha,
            "owner_login": cfg.owner_login,
            "principal": {"kind": PRINCIPAL_KIND, "login": cfg.bot_login},
            "scope": scope,
            # The adapter locates a vendor session log by the path the client writes it under, and
            # the client derives that from the working directory; recording the slug here means
            # capture does not have to reconstruct the rule from a path that may since have been
            # removed.
            "cwd_slug": tree.replace("/", "-").replace(".", "-"),
            "harness": {"client": "exeris-agent-harness", "version": VERSION},
        }
        if pairing:
            manifest["pairing"] = pairing
        if prompt_sha256:
            manifest["prompt_sha256"] = prompt_sha256
        runstate.write_manifest(run_dir, manifest)
    except BaseException:
        runstate.discard(run_dir)
        worktree.prune(clone)
        if minted and not token.revoke(minted):
            print(f"exeris-agent: the token minted for {run_id} could not be revoked and stays "
                  f"valid until {minted.get('expires_at') or 'its hour is up'}", file=sys.stderr)
        raise

    print(f"source {os.path.join(run_dir, 'env')}")
    print(f"cd {tree}")

    if launcher:
        environment = {key: value for key, value in os.environ.items()
                       if key not in runstate.UNSET}
        environment.update(values)
        sys.stdout.flush()
        os.chdir(tree)
        os.execve(launcher, [launcher], environment)
    return EXIT_OK


def _expiry(run_dir: str) -> str:
    """The run token's expiry as the listing shows it, `expired` once the hour has passed.

    An installation token lasts an hour and an agent session routinely lasts longer, so the end of
    a long run is otherwise a push refused with a message about authentication rather than about
    time. V0 does not refresh a token; it says when one is spent.
    """
    try:
        with open(os.path.join(run_dir, "token"), encoding="utf-8") as handle:
            stamp = handle.read().splitlines()[1].strip()
    except (OSError, IndexError):
        return "—"
    if not stamp:
        return "—"
    try:
        when = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    return f"{stamp} (expired)" if when <= datetime.datetime.now(datetime.UTC) else stamp


HEADINGS = ("RUN", "REPOSITORY", "BRANCH", "STARTED", "EXPIRES")


def cmd_status(args) -> int:
    rows = []
    for run_dir in runstate.all_runs():
        manifest = runstate.read_manifest(run_dir) or {}
        rows.append((manifest.get("run_id") or os.path.basename(run_dir),
                     manifest.get("repo") or "—",
                     manifest.get("branch") or "—",
                     manifest.get("started_at") or "—",
                     _expiry(run_dir)))
    if not rows:
        print(f"no runs under {runstate.runs_root()}")
        return EXIT_OK
    widths = [max(len(row[column]) for row in [HEADINGS] + rows)
              for column in range(len(HEADINGS))]
    for row in [HEADINGS] + rows:
        print("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)).rstrip())
    return EXIT_OK


def cmd_not_in_this_version(args) -> int:
    print(f"{args.command}: not in this version", file=sys.stderr)
    return EXIT_REFUSED


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="exeris-agent",
        description="Open and inspect runs of the organisation's execution identity.")
    root.add_argument("--version", action="version", version=f"exeris-agent-harness {VERSION}")
    subcommands = root.add_subparsers(dest="command", required=True, metavar="<command>")

    open_run = subcommands.add_parser(
        "open-run", help="bind a worktree to the execution identity and print how to enter it",
        description="Mint the identity's own installation token, create the run's worktree and "
                    "bind git inside it to the identity, then print the two lines that hand the "
                    "shell over.")
    open_run.add_argument("--repo", required=True, metavar="<owner/name|path>",
                          help="the repository to open the run against")
    open_run.add_argument("--task", required=True, metavar="reg:<id>|adhoc",
                          help="the registry entry this run executes, or `adhoc` for an "
                               "unplanned run, which is capturable but never paired")
    open_run.add_argument("--provider", required=True, choices=PROVIDERS,
                          help="which adapter --launch uses")
    open_run.add_argument("--scope", metavar="<scope>",
                          help="the scope within the repository, checked against the vocabulary "
                               "the configuration declares for it")
    open_run.add_argument("--group", metavar="<id>",
                          help="the paired comparison this run is an arm of; requires reg:")
    open_run.add_argument("--arm", metavar="<name>", help="this run's arm of --group")
    open_run.add_argument("--arms-planned", type=int, metavar="<n>",
                          help="how many arms --group is planned to have")
    open_run.add_argument("--baseline", metavar="<ref>|none",
                          help="the baseline this arm is measured against")
    open_run.add_argument("--prompt-file", metavar="<path>",
                          help="the prompt as passed; hashed into the manifest, never stored")
    open_run.add_argument("--launch", action="store_true",
                          help="exec the provider's adapter in the worktree instead of printing")
    open_run.set_defaults(handler=cmd_open_run)

    status = subcommands.add_parser("status", help="list the runs on this machine",
                                    description="Every run directory under the state root, "
                                                "oldest first.")
    status.set_defaults(handler=cmd_status)

    for name, summary in (("close-run", "push, open the draft pull request, write the run record"),
                          ("flush", "carry staged rows into the inbox")):
        later = subcommands.add_parser(name, help=f"{summary} (not in this version)",
                                       description=f"{summary}. Not in this version.")
        later.set_defaults(handler=cmd_not_in_this_version)

    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"exeris-agent: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (config.ConfigError, token.TokenError, runstate.StateError,
            worktree.WorktreeError) as exc:
        print(f"exeris-agent: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
