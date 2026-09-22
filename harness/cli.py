"""`exeris-agent` — the first client of the harness, and in V0 the only one.

Three commands carry a run from end to end, and the identity each runs under is the point of the
split.

`open-run` binds a worktree to the execution identity and prints the two lines that hand the shell
over. `close-run` ends that run *as the identity*: it pushes the run's branch and opens its draft
pull request with the identity's own token, then writes the run record to the run's staging
directory. `flush` carries those records into the inbox **as the person**, under their own `gh` and
their own git, because the inbox is where the organisation's own pen writes and the hands are
deliberately not installed there. A person opening that pull request is a sign-off on a batch of
rows, not a fallback credential for the runs it describes — so everything a run bound to the shell
is taken out of the environment first, and a shell still bound to one is refused.

Between them sits a rule neither command breaks: a value the run could not establish is not
defaulted. `close-run` pushes and opens the pull request either way, and where the record cannot be
assembled it writes no row and names the reason, which is counted rather than repaired.
"""

import argparse
import datetime
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile

from . import (ROOT, VERSION, capture, config, oracle, providers, record, runstate, token, ulid,
               worktree)
# Bound here rather than used through the module, because this name is the seam: everything that
# reaches git or the forge is constructed from it, so substituting it substitutes both halves at
# once and a test can replace the forge while keeping git.
from .runner import CommandError, GhError, Runner

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
    # Two values and no third: a group either has a human arm, whose measurement every row of it
    # carries, or it has none and can never carry an economic claim. Anything else is a row the
    # contract refuses, and refusing it here is refusing it before the work is done.
    if args.baseline not in ("human", "none"):
        raise Refused(f"--baseline {args.baseline!r} is neither `human` nor `none`")
    return {"group_id": args.group, "arm": args.arm,
            "arms_planned": args.arms_planned, "baseline": args.baseline}


#: What a human baseline says about the work, as the row contract names it. Nothing about the
#: person: the moment this object needs a field about the human rather than about the work, it
#: stops being a field on a row and becomes a record that rows reference.
BASELINE_FILE = "baseline.json"
BASELINE_KIND = "baseline"


def _checked_baseline(value, where: str) -> dict:
    """A human baseline, in the shape every row of its group has to carry byte for byte.

    Checked here rather than trusted, because it is copied onto rows this producer writes: a
    baseline that does not survive the contract would be discovered at the inbox, one flush after
    the arms it was supposed to make interpretable had run.
    """
    if not isinstance(value, dict):
        raise Refused(f"{where} does not carry a human baseline object")
    out = {}
    wall = value.get("wall_time_ms")
    if not isinstance(wall, int) or isinstance(wall, bool) or wall < 0:
        raise Refused(f"{where}: the human baseline states no wall_time_ms")
    out["wall_time_ms"] = wall
    outcome = value.get("outcome")
    if outcome not in oracle.OUTCOMES:
        raise Refused(f"{where}: the human baseline's outcome is not one of "
                      f"{', '.join(oracle.OUTCOMES)}")
    out["outcome"] = outcome
    changes = value.get("changes")
    if changes is not None:
        if not isinstance(changes, dict):
            raise Refused(f"{where}: the human baseline's changes are not an object")
        counted = {name: changes[name] for name in ("files_changed", "insertions", "deletions")
                   if isinstance(changes.get(name), int) and not isinstance(changes[name], bool)}
        if counted:
            out["changes"] = counted
    return out


def _baseline_in_file(path: str) -> dict | None:
    """The group's human baseline as the group record carries it, or nothing where it carries none.

    The registry is another repository's, so the record reaches the harness as a file a person
    points at. Either shape is read: the group record itself, under its `human_baseline` key, and
    the measurement on its own, which is what `baseline --close` prints.
    """
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"--group-file {path}: {exc}") from None
    if isinstance(document, dict) and document.get("human_baseline") is not None:
        return _checked_baseline(document["human_baseline"], f"--group-file {path}")
    if isinstance(document, dict) and "wall_time_ms" in document:
        return _checked_baseline(document, f"--group-file {path}")
    return None


def _baseline_on_this_machine(group: str) -> dict | None:
    """The human baseline a `baseline` run of this group staged here, where one did.

    A producer knows about the arms it ran itself, so a group whose human arm was measured on this
    machine needs no file passed to it. Two baselines that do not agree are refused rather than
    chosen between: every row of a group carries this object identically, and a group with two of
    them is a group nobody can interpret.
    """
    found = {}
    for run_dir in runstate.all_runs():
        manifest = runstate.read_manifest(run_dir) or {}
        if manifest.get("kind") != BASELINE_KIND or manifest.get("group") != group:
            continue
        path = os.path.join(run_dir, "staging", BASELINE_FILE)
        try:
            with open(path, encoding="utf-8") as handle:
                found[json.dumps(json.load(handle), sort_keys=True)] = path
        except (OSError, json.JSONDecodeError):
            continue
    if not found:
        return None
    if len(found) > 1:
        raise Refused(f"group {group} has {len(found)} human baselines staged on this machine "
                      f"and they differ: {', '.join(sorted(found.values()))}")
    body, path = next(iter(found.items()))
    return _checked_baseline(json.loads(body), path)


def _human_baseline(args, pairing: dict) -> dict | None:
    """The human arm's measurement, where the group was planned with one.

    The human arm runs first, so that its measurement is on every model row of the group when that
    row is written and no row is rewritten afterwards — and so that the human has not seen a
    model's output. This is where that protocol is enforced: a model arm of a group declared with a
    human baseline does not open until the baseline exists.

    `--no-baseline-required` opens the arm anyway. It does not fabricate a baseline: the run
    happens, its work is pushed, and its row is refused with the reason that says the group's
    baseline could not be read.
    """
    if not pairing or pairing.get("baseline") != "human":
        return None
    required = True if args.baseline_required is None else bool(args.baseline_required)
    measured = (_baseline_in_file(args.group_file) if args.group_file
                else _baseline_on_this_machine(pairing["group_id"]))
    if measured is None and required:
        raise Refused(f"group {pairing['group_id']} is planned with a human arm and no human "
                      f"baseline exists yet: run `exeris-agent baseline --repo … --task "
                      f"{args.task} --group {pairing['group_id']}` first, or pass --group-file "
                      f"naming the group record that carries one")
    return measured


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


def _launcher(adapter: str) -> str:
    """The program `--launch` execs in the run's worktree.

    Named apart from the session loader below because the two are different halves of one adapter
    and take the same argument: one is a path handed to `execve`, the other a module the record is
    assembled from, and a name that answered for both would hand `execve` a module.
    """
    launcher = os.path.join(ROOT, "adapters", adapter, "launch.sh")
    if not os.path.isfile(launcher):
        raise Refused(f"no launcher for the {adapter} adapter at {launcher}")
    return launcher


def _provider(cfg, args):
    """The arm this run is, from the provider table `--provider` names."""
    try:
        return providers.resolve(cfg, args.provider, prompt_file=args.prompt_file)
    except providers.ProviderError as exc:
        raise Refused(str(exc)) from None


def _launch_values(arm, prompt_file: str | None) -> dict:
    """What the run's environment carries for the adapter, beside what it carries for git.

    The provider table's own variables, and the two the harness itself knows: which model the
    adapter is to launch, and where the task text is. Both are needed by a client that takes its
    task on the command line, and neither is a secret — the task's digest is on the row and its
    text is a file the person wrote.
    """
    values = dict(arm.env)
    if arm.model_id:
        values["EXERIS_MODEL_ID"] = arm.model_id
    if prompt_file:
        values["EXERIS_PROMPT_FILE"] = os.path.abspath(os.path.expanduser(prompt_file))
    return values


def cmd_open_run(args) -> int:
    cfg = config.load()

    # Everything that can be refused is refused before a directory or a token exists, so a bad
    # invocation leaves no run behind to be cleaned up or, worse, reported on.
    _task_ref(args.task, "0" * ulid.LENGTH)
    pairing = _pairing(args)
    arm = _provider(cfg, args)
    human_baseline = _human_baseline(args, pairing)
    prompt_sha256 = _prompt_digest(args.prompt_file)
    clone = worktree.resolve_clone(args.repo, cfg)
    slug = worktree.origin_slug(clone)
    scope = _scope(args, cfg.repo(slug.split("/", 1)[1]))
    base_branch = worktree.default_branch(clone)
    launcher = _launcher(arm.adapter) if args.launch else None

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
        # The arm's own variables, added after the run's: a provider table cannot name one of the
        # run's, which is checked where the table is read, so the two sets do not overlap and the
        # order below is a statement rather than a precedence.
        values.update(_launch_values(arm, args.prompt_file))
        runstate.write_env(run_dir, values)

        manifest = {
            "run_id": run_id,
            "task": task,
            "repo": slug,
            "provider": args.provider,
            # The arm, as the table declared it and as the row will carry it. Recorded when the run
            # opens because a table edited afterwards would otherwise rename what already ran; the
            # weights digest is here for the same reason, and because a file measured in gigabytes
            # is read once.
            "provider_table": arm.recorded(),
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
        if human_baseline:
            manifest["human_baseline"] = dict(human_baseline)
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


#: The agent file whose content is part of what a run was instructed by. It is read at the commit
#: the run started from, not from the tree as it stands: a run that edited it was subject to what
#: it found, and a hash taken afterwards would record what the run wrote rather than what it read.
AGENTS_FILE = "AGENTS.md"

#: The producer's own note beside a run's staged records. It is not a record and nothing reads it
#: as one; it is what makes closing a run once observable, so that a second close cannot push a
#: second time or ask for a second pull request.
CLOSED = "closed.json"

#: What the oracle found, gate by gate, staged with the run and carried into no inbox. The row says
#: which oracle judged the run and what it concluded; the gates behind that are this oracle's own
#: internals, and a run whose row reads `UNKNOWN` over passing gates is explained here rather than
#: in a column nobody could compare across oracles.
JUDGEMENT_FILE = "judgement.json"


def _judged(cfg, worktree_path: str, domain: str, run_id: str) -> dict:
    """Ask the seam, and say beside the run what it could not read.

    The judge is given paths and never the configuration, so that what an oracle is shown is
    visible at the call. Anything it could not read is printed rather than swallowed: `UNKNOWN`
    because no oracle was there and `UNKNOWN` because the gates found nothing to judge are the
    same word on the row, and the difference is a person's to act on.
    """
    judgement = oracle.judge(worktree_path, domain or "",
                             execution_repo=cfg.execution_repo_path,
                             index=cfg.docs_index_path)
    if judgement.get("reason"):
        print(f"exeris-agent: {run_id}: {judgement['reason']}", file=sys.stderr)
    return judgement


def _stage_judgement(run_dir: str, judgement: dict, *, run_id: str, domain: str) -> None:
    body = json.dumps(oracle.evidence(judgement, run_id=run_id, domain=domain or ""),
                      indent=2, sort_keys=True) + "\n"
    runstate.write_text(run_dir, os.path.join("staging", JUDGEMENT_FILE), body)


def _run_environment(run_dir: str, manifest: dict, cfg) -> dict:
    """The environment the run's own calls are made in — the one `open-run` printed.

    Reconstructed rather than remembered: the token is the file the mint wrote, and the rest is the
    same mapping the env file exports. What the person's shell carried is dropped first, because a
    variable the harness never sets is a variable the harness never closed.
    """
    try:
        with open(os.path.join(run_dir, "token"), encoding="utf-8") as handle:
            minted = handle.read().splitlines()[0].strip()
    except (OSError, IndexError):
        minted = ""
    if not minted:
        raise Refused(f"run {manifest['run_id']} has no token; it cannot act as the identity")
    environment = {key: value for key, value in os.environ.items() if key not in runstate.UNSET}
    environment.update(runstate.env_values(run_dir, run_id=manifest["run_id"], token=minted,
                                           user_name=cfg.bot_login,
                                           user_email=cfg.noreply_email))
    return environment


def _session_module(adapter: str):
    """The adapter's session half, or nothing where the checkout carries none."""
    try:
        return importlib.import_module(f"adapters.{adapter}.session")
    except ImportError:
        return None


def _at_commit(net, worktree_path: str, commit: str, path: str, *, reason: str) -> str | None:
    """A file as it stood at a commit, byte for byte, or nothing where that tree does not hold it.

    The two answers one failing `git show` cannot tell apart are separated here. A path the tree
    does not list is a state the record hashes: a checkout with no agent file was instructed by an
    empty one. A path the tree does list and the object store cannot produce, or a commit that does
    not resolve at all, is a component the producer cannot recover exactly — and a component
    recovered inexactly is a hash of something else, so it yields no row rather than an empty
    string.
    """
    try:
        net.git(worktree_path, "rev-parse", "--verify", f"{commit}^{{commit}}")
        listed = net.git(worktree_path, "ls-tree", "--name-only", commit, "--", path)
    except CommandError as exc:
        raise capture.NoRow(reason, str(exc)) from None
    if not listed.strip():
        return None
    try:
        return net.git_raw(worktree_path, "show", f"{commit}:{path}")
    except CommandError as exc:
        raise capture.NoRow(reason, str(exc)) from None


def _visibility(net, repo: str, environment: dict) -> str:
    """The repository's visibility in ADR-020's taxonomy, fail-closed.

    Only an answer that says public becomes `public`; everything else — private, internal, and a
    visibility the producer could not establish at all — becomes `enterprise-private`. The rule
    runs one way only, because a row published under the wrong visibility is published by the act
    of filing it and no later correction un-publishes it.
    """
    try:
        answer = net.api(f"repos/{repo}", env=environment)
    except (GhError, CommandError, OSError):
        return "enterprise-private"
    value = answer.get("visibility") if isinstance(answer, dict) else None
    return "public" if str(value).lower() == "public" else "enterprise-private"


def _title(net, worktree_path: str, commits: list, run_id: str) -> str:
    """The pull request's title: the run's first commit subject, which is already in the grammar."""
    if commits:
        subject = net.git(worktree_path, "log", "-1", "--format=%s", commits[0])
        if subject:
            return subject
    return f"chore(agent): run {ulid.short(run_id)}"


def _row(args, cfg, net, manifest, *, worktree_path, visibility, dirty, commits,
         ended_at, judgement) -> tuple[dict | None, str | None, str | None]:
    """`(row, session path, reason)` — the run record, or why there is not one.

    Every refusal reaches here as `NoRow` and leaves as a reason, because a run that cannot be
    recorded is not a run that failed: its commits are pushed and its pull request is open, and
    what is missing is the observation.
    """
    arm = manifest.get("provider_table") or {}
    adapter = arm.get("adapter") or manifest["provider"]
    try:
        module = _session_module(adapter)
        if module is None:
            raise capture.NoRow("adapter-identity-only", adapter)
        session = module.locate(manifest, override=args.session)
        # An adapter with no search to make answers the pair rather than raising, because it has
        # nothing to have failed at: there is no log it has ever read.
        if not isinstance(session, str):
            named = session[1] if isinstance(session, (tuple, list)) and len(session) > 1 else None
            raise capture.NoRow(named or "adapter-identity-only")
        facts = module.read(session)
        # What the adapter measured but the row has no field for. It is printed beside the run and
        # written nowhere: a shape nobody has measured is learned from a run that had one, and a
        # count whose meaning is unchecked does not belong in a column.
        for note in facts.get("notes") or ():
            print(f"exeris-agent: {manifest['run_id']}: {note}", file=sys.stderr)

        repo_config = cfg.repo(manifest["repo"].split("/", 1)[1])
        routine = ""
        if repo_config.routine:
            found = _at_commit(net, worktree_path, manifest["base_sha"], repo_config.routine,
                               reason="routine-unreadable")
            if found is None:
                raise capture.NoRow("routine-unreadable", repo_config.routine)
            routine = found
        # A checkout with no agent file is a checkout whose agent instructions are empty. That is a
        # state the hash records, not a component it could not recover.
        agents_file = _at_commit(net, worktree_path, manifest["base_sha"], AGENTS_FILE,
                                 reason="agents-file-unreadable") or ""

        bundle = record.bundle_version(
            _at_commit(net, worktree_path, manifest["base_sha"], record.BUNDLE_MANIFEST,
                       reason="bundle-manifest-unreadable"))
        date = record.date_of(manifest["started_at"])
        row = record.assemble(
            manifest=manifest,
            repo_config=repo_config,
            provider=arm,
            facts=facts,
            visibility=visibility,
            bundle=bundle,
            dirty=dirty,
            result_commits=commits,
            ended_at=ended_at,
            capture_version=record.capture_version(cfg.execution_repo_path or ""),
            # Resolved in the register the contract publishes, never minted from this run's own
            # date: an id no entry carries is a mark nobody can say they are on the far side of.
            # The producer is the harness and the adapter that read the run — never the model, and
            # never the arm's own name: two arms read by one adapter are two rows under one fence,
            # and what differs between them is on the rows themselves.
            fence=record.fence(cfg.execution_repo_path or "", adapter,
                               arm.get("harness_version") or facts["version"]),
            ref=record.stream_ref(cfg.org, os.path.basename(cfg.streams_repo_path or ""), date,
                                  manifest["repo"].split("/", 1)[1], manifest["run_id"]),
            # The prompt as it was passed, where the run was given one, and otherwise the prompt
            # the session opened with. Either way it is a digest by the time it reaches here: the
            # text was hashed where it was read.
            prompt_digest=manifest.get("prompt_sha256") or facts.get("system_prompt_sha256") or "",
            routine=routine,
            agents_file=agents_file,
            # Asked once, of the seam that owns the question, and asked before the branch was
            # pushed so that what was judged is what the run produced. A judgement and the state
            # of the suite behind it travel together, so that a label is never read apart from
            # what makes it admissible.
            judgement=judgement,
        )
        return row, session, None
    except capture.NoRow as refusal:
        return None, None, refusal.reason


def cmd_close_run(args) -> int:
    cfg = config.load()
    if not ulid.is_ulid(args.run):
        raise Refused(f"--run {args.run!r} is not a run id")
    run_dir = runstate.path(args.run)
    manifest = runstate.read_manifest(run_dir)
    if manifest is None:
        raise Refused(f"no run {args.run} under {runstate.runs_root()}")
    if manifest.get("kind") == BASELINE_KIND:
        raise Refused(f"{args.run} is a human baseline and not a model arm: it holds no identity "
                      f"to push as and produces no row — close it with `baseline --close`")
    if not manifest.get("provider_table"):
        # The arm is recorded when the run opens, so a manifest without one describes a run this
        # producer cannot say anything about: which vendor, which ledger, which snapshot. Refused
        # before anything is pushed, because acting as the identity for a run that can never be
        # recorded is the one thing this command must not do quietly.
        raise Refused(f"the manifest of {args.run} names no provider table; it was opened by a "
                      f"harness that did not record the arm, and nothing here can reconstruct it")
    staging = os.path.join(run_dir, "staging")
    if os.path.exists(os.path.join(staging, CLOSED)):
        raise Refused(f"{args.run} was closed already; what it staged is under {staging}")
    if not cfg.owner_login:
        raise Refused(f"{cfg.path} declares no owner_login; a pull request this run's work becomes "
                      f"carries exactly one `Owner:` and this is where it comes from")

    net = Runner()
    worktree_path = manifest["worktree"]
    if not os.path.isdir(worktree_path):
        raise Refused(f"the worktree of {args.run} is gone: {worktree_path}")
    environment = _run_environment(run_dir, manifest, cfg)

    ended_at = _now()
    manifest = dict(manifest, ended_at=ended_at)
    run_id, repo, branch = manifest["run_id"], manifest["repo"], manifest["branch"]

    dirty = bool(net.git(worktree_path, "status", "--porcelain"))
    commits = net.git_lines(worktree_path, "rev-list", "--reverse",
                            f"{manifest['base_sha']}..HEAD")
    # A commit the hook did not stamp is a commit the record cannot claim: the trailer is the only
    # link from the commit graph back to the run that produced it, so one without it is counted
    # rather than adopted.
    trailer = f"Exeris-Run: {run_id}"
    unattributed = [sha for sha in commits
                    if trailer not in net.git(worktree_path, "log", "-1", "--format=%B", sha)]
    if unattributed:
        print(f"exeris-agent: {len(unattributed)} unattributed-commit on {branch} — no "
              f"`{trailer}` trailer: {', '.join(sha[:8] for sha in unattributed)}", file=sys.stderr)

    # Judged at the head the run left, before anything of it has gone anywhere. The oracle reads a
    # tree, so the tree it reads has to be the one the commits above produced: a judgement taken
    # after a push would still be honest, and one taken after a merge or a rebase would not be, and
    # the order is what keeps that from ever being a question.
    domain = cfg.repo(repo.split("/", 1)[1]).domain
    judgement = _judged(cfg, worktree_path, domain, run_id)

    net.git(worktree_path, "push", "origin", f"HEAD:refs/heads/{branch}", env=environment)

    pull_request = None
    if not args.no_pr:
        answer = net.api(f"repos/{repo}/pulls", method="POST", env=environment, body={
            "title": args.title or _title(net, worktree_path, commits, run_id),
            "head": branch,
            "base": worktree.default_branch(worktree_path),
            "body": record.pull_request_body(cfg.owner_login, run_id),
            # A draft, always. A human marks it ready, and that is the moment a person takes on
            # what the run produced; opening it ready would make the identity's own push the
            # readiness event.
            "draft": True,
        })
        pull_request = (answer or {}).get("html_url") if isinstance(answer, dict) else None

    visibility = _visibility(net, repo, environment)
    row, session, reason = _row(args, cfg, net, manifest, worktree_path=worktree_path,
                                visibility=visibility, dirty=dirty, commits=commits,
                                ended_at=ended_at, judgement=judgement)

    date = record.date_of(manifest["started_at"])
    os.makedirs(staging, mode=runstate.DIR_MODE, exist_ok=True)
    # Staged whether or not there is a row. A run the record could not be assembled for was judged
    # all the same, and the gates are the one account of it that survives.
    _stage_judgement(run_dir, judgement, run_id=run_id, domain=domain)
    if row is not None:
        # The stream is copied rather than referenced: the client's own log is the person's and may
        # be rotated or removed, and the row's digest has to keep naming something that exists.
        stream = os.path.join(staging, "streams", date, repo.split("/", 1)[1], f"{run_id}.jsonl")
        os.makedirs(os.path.dirname(stream), exist_ok=True)
        shutil.copyfile(session, stream)
        record.write(os.path.join(staging, visibility, "inbox", date, "runs", f"{run_id}.json"),
                     row)
        print(f"exeris-agent: {run_id} staged as a {visibility} row")
    else:
        print(f"exeris-agent: no row for {run_id}: {reason}", file=sys.stderr)

    runstate.write_text(run_dir, os.path.join("staging", CLOSED), json.dumps({
        "run_id": run_id,
        "ended_at": ended_at,
        "visibility": visibility,
        "row": row is not None,
        "reason": reason,
        "pull_request": pull_request,
        "unattributed_commits": len(unattributed),
    }, indent=2, sort_keys=True) + "\n")

    if pull_request:
        print(pull_request)
    if args.remove_worktree:
        _remove_worktree(net, worktree_path, dirty)
    return EXIT_OK


def _remove_worktree(net, worktree_path: str, dirty: bool) -> None:
    """Give the run's tree back to the clone. Never while it holds uncommitted work."""
    if dirty:
        print(f"exeris-agent: {worktree_path} has uncommitted changes and is kept", file=sys.stderr)
        return
    common = net.git(worktree_path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    clone = os.path.dirname(common.rstrip("/"))
    net.git(clone, "worktree", "remove", worktree_path)


#: `git diff --shortstat`, as git writes it. A clause git omits is a count of zero and is read as
#: one: the diff was taken, and what it did not report it did not contain.
_SHORTSTAT = {
    "files_changed": re.compile(r"(\d+) files? changed"),
    "insertions": re.compile(r"(\d+) insertions?\(\+\)"),
    "deletions": re.compile(r"(\d+) deletions?\(-\)"),
}


def _changes(net, worktree_path: str, base: str) -> dict | None:
    """What the work changed, from the diff between the commit it started from and its head.

    Nothing where the diff could not be taken. A baseline whose changes are absent is still a
    baseline — wall time and outcome are what the field requires — and a zeroed count for a diff
    nobody read would be a measurement of nothing.
    """
    try:
        summary = net.git(worktree_path, "diff", "--shortstat", f"{base}..HEAD")
    except CommandError:
        return None
    counted = {}
    for name, pattern in _SHORTSTAT.items():
        found = pattern.search(summary or "")
        counted[name] = int(found.group(1)) if found else 0
    return counted


def cmd_baseline(args) -> int:
    """The human arm: a worktree the person works in as themselves, and what it measured.

    It is the other half of a paired comparison, and the half nothing else produces. A model arm's
    row can carry a cost and a count of turns and still say nothing about whether the work was
    worth doing at that price — that reading needs a human reference point, measured on the same
    task, under the same oracle, before any model arm ran.

    So this command opens a tree and binds almost nothing to it. No token is minted, no identity is
    written, no run environment is exported: the commits are the person's, authored as themselves,
    and what the harness adds is the hook that times them and the trailer that joins them to this
    measurement.
    """
    if args.close:
        return _close_baseline(args)
    return _open_baseline(args)


def _open_baseline(args) -> int:
    cfg = config.load()
    for flag, value in (("--repo", args.repo), ("--task", args.task), ("--group", args.group)):
        if not value:
            raise Refused(f"baseline requires {flag}")
    if args.task == "adhoc":
        # A baseline exists to be carried onto the rows of a group, and a group is a plan. An
        # unplanned task is an arm of nothing, so a baseline of one could be carried nowhere.
        raise Refused("baseline requires --task reg:<id>; a human arm measures a planned task")
    task = _task_ref(args.task, "0" * ulid.LENGTH)

    clone = worktree.resolve_clone(args.repo, cfg)
    slug = worktree.origin_slug(clone)
    repo_config = cfg.repo(slug.split("/", 1)[1])
    scope = _scope(args, repo_config)
    base_branch = worktree.default_branch(clone)

    run_id = ulid.new()
    run_dir = runstate.create(run_id)
    try:
        tree = os.path.join(run_dir, "wt")
        branch = f"baseline/{ulid.short(run_id)}"
        base_sha = worktree.add(clone, tree, branch, base_branch)
        worktree.bind_settings(clone, tree, runstate.baseline_settings(run_dir))
        runstate.write_hook(run_dir, run_id)
        runstate.write_timing_hook(run_dir)
        runstate.write_manifest(run_dir, {
            # What this run is, said in the manifest rather than inferred from what it lacks: a
            # baseline holds no token and writes no row, and a command that guessed at the kind
            # from a missing file would treat a half-opened model arm as a human one.
            "kind": BASELINE_KIND,
            "run_id": run_id,
            "task": task,
            "repo": slug,
            "group": args.group,
            "domain": repo_config.domain,
            "scope": scope,
            "started_at": _now(),
            "worktree": tree,
            "branch": branch,
            "base_sha": base_sha,
            "harness": {"client": "exeris-agent-harness", "version": VERSION},
        })
    except BaseException:
        runstate.discard(run_dir)
        worktree.prune(clone)
        raise

    print(f"cd {tree}")
    print(f"exeris-agent: {run_id} is a human baseline of group {args.group}; it is worked under "
          f"your own git identity and no run environment is sourced. When the work is done: "
          f"`exeris-agent baseline --close --run {run_id}`", file=sys.stderr)
    return EXIT_OK


def _close_baseline(args) -> int:
    if not args.run:
        raise Refused("baseline --close requires --run <ULID>")
    if not ulid.is_ulid(args.run):
        raise Refused(f"--run {args.run!r} is not a run id")
    run_dir = runstate.path(args.run)
    manifest = runstate.read_manifest(run_dir)
    if manifest is None:
        raise Refused(f"no run {args.run} under {runstate.runs_root()}")
    if manifest.get("kind") != BASELINE_KIND:
        raise Refused(f"{args.run} is a model arm, not a human baseline; close it with "
                      f"`close-run`")
    staged = os.path.join(run_dir, "staging", BASELINE_FILE)
    if os.path.exists(staged):
        # Closing twice would measure the time between the work and the second close, which is not
        # the time the work took.
        raise Refused(f"{args.run} was closed already; what it measured is in {staged}")

    worktree_path = manifest["worktree"]
    if not os.path.isdir(worktree_path):
        raise Refused(f"the worktree of {args.run} is gone: {worktree_path}")

    net = Runner()
    ended_at = _now()
    wall_time_ms = max(int((record.stamp(ended_at)
                            - record.stamp(manifest["started_at"])).total_seconds() * 1000), 0)
    # The same judge the run record's outcome comes from, asked the same way, over the same kind of
    # tree. A baseline judged by anything else is not a baseline: the comparison it exists for is a
    # comparison of outcomes, and two arms measured by two instruments compare the instruments.
    domain = manifest.get("domain") or ""
    judgement = _judged(config.load(), worktree_path, domain, args.run)
    _stage_judgement(run_dir, judgement, run_id=args.run, domain=domain)
    baseline = {"wall_time_ms": wall_time_ms, "outcome": oracle.outcome_of(judgement)}
    changes = _changes(net, worktree_path, manifest["base_sha"])
    if changes is None:
        print(f"exeris-agent: the diff of {args.run} could not be taken; the baseline states its "
              f"time and its outcome and no count of changes", file=sys.stderr)
    else:
        baseline["changes"] = changes

    body = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    runstate.write_text(run_dir, os.path.join("staging", BASELINE_FILE), body)
    # The registry is another repository's, so the group record is updated by the person. What is
    # printed is exactly what a model arm of this group will carry, because every row of a group
    # carries this object identically and a re-typed copy is a group that cannot be interpreted.
    print(body, end="")
    print(f"exeris-agent: paste this as `human_baseline` on the record of group "
          f"{manifest.get('group')}; a model arm opened on this machine reads it from "
          f"{staged} until then", file=sys.stderr)
    return EXIT_OK


def _staged(kind: str):
    """Every staged artefact of `kind` across the runs on this machine, oldest run first."""
    for run_dir in runstate.all_runs():
        base = os.path.join(run_dir, "staging", kind)
        for here, _dirs, names in sorted(os.walk(base)):
            for name in sorted(names):
                yield run_dir, os.path.join(here, name)


def _staged_rows() -> list[dict]:
    """The rows waiting in staging: where each is, whose inbox it belongs in, and its date."""
    out = []
    for visibility in ("public", "enterprise-private"):
        for run_dir, path in _staged(os.path.join(visibility, "inbox")):
            parts = path.split(os.sep)
            if len(parts) < 3 or parts[-2] != "runs" or not path.endswith(".json"):
                continue
            out.append({"run_dir": run_dir, "path": path, "visibility": visibility,
                        "date": parts[-3], "run_id": os.path.basename(path)[:-len(".json")]})
    return out


def _staged_streams() -> list[dict]:
    """The session streams waiting in staging, with the repository and date they file under."""
    out = []
    for run_dir, path in _staged("streams"):
        parts = path.split(os.sep)
        if len(parts) < 3 or not path.endswith(".jsonl"):
            continue
        out.append({"run_dir": run_dir, "path": path, "date": parts[-3], "repo": parts[-2],
                    "run_id": os.path.basename(path)[:-len(".jsonl")]})
    return out


def _repositories(rows: list) -> list[str]:
    """Which repositories a batch of rows came from, for the pull request that carries them."""
    out = []
    for row in rows:
        try:
            with open(row["path"], encoding="utf-8") as handle:
                found = (json.load(handle).get("repository_state") or {}).get("repository")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        if found:
            out.append(str(found))
    return out


def _clone(cfg, attribute: str, what: str) -> str:
    configured = getattr(cfg, attribute)
    if not configured:
        raise Refused(f"{cfg.path} declares no {attribute}; {what}")
    if not worktree.is_clone(configured):
        raise Refused(f"{configured} is not a git repository; {attribute} is a local clone")
    return configured


def _carry_streams(net, streams: str, rows: list, staged: list, env=None) -> str:
    """Copy the streams in, append the index, commit, push, and answer with the commit.

    A stream is content rather than a record: it goes to its own repository, on its main branch,
    because the row that references it is useless until the reference resolves and a pull request
    would leave it dangling until somebody merged. Nothing here is rewritten — a stream already
    carried is left where it is, and its index entry is not written twice.
    """
    known_rows = {}
    for row in rows:
        try:
            with open(row["path"], encoding="utf-8") as handle:
                known_rows[row["run_id"]] = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

    index_path = os.path.join(streams, "index.json")
    try:
        with open(index_path, encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, json.JSONDecodeError):
        index = []
    if not isinstance(index, list):
        raise Refused(f"{index_path} is not a list of entries")
    listed = {entry.get("run_id") for entry in index if isinstance(entry, dict)}

    carried = 0
    changed = False
    for item in staged:
        row = known_rows.get(item["run_id"])
        if row is None:
            continue
        carried += 1
        stream = (row.get("execution") or {}).get("event_stream") or {}
        target = os.path.join(streams, "streams", item["date"], item["repo"],
                              f"{item['run_id']}.jsonl")
        if not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(item["path"], target)
            changed = True
        if item["run_id"] not in listed:
            index.append({
                "repo": item["repo"],
                "run_id": item["run_id"],
                "path": f"streams/{item['date']}/{item['repo']}/{item['run_id']}.jsonl",
                "sha256": stream.get("sha256"),
                "event_count": stream.get("event_count"),
                # The session's own start, so that a stream carried in a later flush still files
                # under the day it was produced.
                "created_at": row.get("started_at"),
                "producer": "harness",
            })
            listed.add(item["run_id"])
            changed = True

    if changed:
        with open(index_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(index, indent=2, sort_keys=True) + "\n")
        branch = net.git(streams, "rev-parse", "--abbrev-ref", "HEAD", env=env)
        net.git(streams, "add", "-A", ".", env=env)
        net.git(streams, "commit", "-m",
                f"feat(streams): {carried} harness session stream(s)", env=env)
        net.git(streams, "push", "origin", f"HEAD:refs/heads/{branch}", env=env)
    return net.git(streams, "rev-parse", "HEAD", env=env)


def _stream_path(row: dict) -> str | None:
    """The path inside the streams repository a row's own reference names."""
    ref = ((row.get("execution") or {}).get("event_stream") or {}).get("ref")
    if not isinstance(ref, str) or "/streams/" not in ref:
        return None
    return "streams/" + ref.split("/streams/", 1)[1].split("@", 1)[0]


def _resolve_pending(rows: list, streams: str, sha: str) -> tuple[int, list]:
    """Resolve each row's pending mark to the commit its own stream is in; answer the rest.

    Staging is the producer's own, and the rule that a record is appended and never rewritten
    begins where records are kept. A row that already names a commit is left alone: it names the
    commit its own stream went in under, and a later flush's commit is not that one.

    The mark is a claim about one file, so it is resolved one row at a time. A row whose stream
    never reached the repository — removed from staging, or never copied there — keeps its mark and
    stays behind: a reference that resolves to a path the commit does not carry is worse than one
    that says it is still pending.
    """
    resolved, pending = 0, []
    for row in rows:
        try:
            with open(row["path"], encoding="utf-8") as handle:
                text = handle.read()
            body = json.loads(text)
        except (OSError, json.JSONDecodeError):
            pending.append(row)
            continue
        if "@pending" not in text:
            continue
        inside = _stream_path(body)
        if not inside or not os.path.exists(os.path.join(streams, inside)):
            pending.append(row)
            continue
        with open(row["path"], "w", encoding="utf-8") as handle:
            handle.write(text.replace("@pending", f"@{sha}"))
        resolved += 1
    return resolved, pending


def _validate(execution: str, rows: list) -> int:
    """Run the inbox's own validator over a temporary inbox holding exactly these rows.

    The validator is imported from the contract repository rather than copied here: a second copy
    is a second answer, and the one that matters is the one the inbox pull request will be judged
    by. Schema conformance is checked beside it where a validator is installed, and is checkable
    rather than checked where one is not — which is the validator's own stated split.
    """
    with tempfile.TemporaryDirectory(prefix="exeris-flush-") as root:
        shutil.copytree(os.path.join(execution, "schemas"), os.path.join(root, "schemas"))
        inbox = os.path.join(root, "inbox")
        os.makedirs(inbox)
        # The inbox's own identity, copied rather than written: rule 1 compares every row against
        # the visibility the inbox declares, and a producer that supplied that value would be
        # checking the rows against its own opinion and passing by construction.
        try:
            shutil.copyfile(os.path.join(execution, "inbox", "inbox.json"),
                            os.path.join(inbox, "inbox.json"))
        except OSError as exc:
            raise Refused(f"{execution} declares no inbox identity ({exc}); the validator has "
                          f"nothing to compare a row's visibility against") from None
        for row in rows:
            target = os.path.join(inbox, row["date"], "runs", f"{row['run_id']}.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(row["path"], target)

        tools = os.path.join(execution, "tools")
        sys.path.insert(0, tools)
        # Importing from a clone writes bytecode into it, and this clone is the one flush is about
        # to commit from: a producer that leaves a file behind in the tree it commits either
        # carries it in with the batch or refuses its own next run for a dirty tree.
        writes_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            sys.modules.pop("inbox_validate", None)
            inbox_validate = importlib.import_module("inbox_validate")
        except ImportError as exc:
            raise Refused(f"{tools} carries no inbox_validate: {exc}") from None
        finally:
            sys.dont_write_bytecode = writes_bytecode
            if sys.path and sys.path[0] == tools:
                sys.path.pop(0)

        report = inbox_validate.Report()
        notes = inbox_validate.check(root, report)
        report.emit(notes)
        bad = len(report.bad)
        bad += _schema_errors(os.path.join(root, "schemas", "run-record.schema.json"), rows)
        return bad


def _schema_errors(schema_path: str, rows: list) -> int:
    """Draft 2020-12 conformance, where a validator is installed."""
    try:
        import jsonschema
    except ImportError:
        print("exeris-agent: jsonschema is not installed; conformance is checkable, not checked",
              file=sys.stderr)
        return 0
    with open(schema_path, encoding="utf-8") as handle:
        validator = jsonschema.Draft202012Validator(json.load(handle))
    bad = 0
    for row in rows:
        with open(row["path"], encoding="utf-8") as handle:
            body = json.load(handle)
        for error in sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path)):
            where = "/".join(str(part) for part in error.absolute_path) or "<root>"
            print(f"::error file={row['path']}::{where}: {error.message}")
            bad += 1
    return bad


def _inbox_branch(net, execution: str, branch: str, env=None) -> str:
    """Check the inbox branch out, creating it or continuing the one already open.

    Fetch first and branch from the remote's own tip: a day's batch is appended to, and a branch
    cut from a stale local copy would silently drop the rows an earlier flush put there.
    """
    start = net.git(execution, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    net.git(execution, "fetch", "origin", env=env)
    if net.git(execution, "rev-parse", "--verify", "--quiet",
               f"refs/remotes/origin/{branch}", env=env, check=False):
        net.git(execution, "checkout", "-B", branch, f"origin/{branch}", env=env)
    else:
        net.git(execution, "checkout", "-B", branch,
                f"origin/{worktree.default_branch(execution)}", env=env)
    return start


#: The prefix every inbox batch this producer opens is branched under. It is what a standing batch
#: is recognised by, because the date after it is not what makes two branches one batch.
INBOX_BRANCH = "inbox/harness/"


def _today() -> str:
    """The UTC day a new batch is cut under, where there is no standing one to add to."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def _person_environment() -> dict:
    """The environment with everything a run bound to a shell taken out of it.

    `flush` is the one command that acts as the person, and the terminal it is normally typed in is
    the one the run was worked in — where `GH_TOKEN` is the identity's installation token and
    `GIT_AUTHOR_NAME` is the identity's own name. Inherited, they would commit the rows and open
    the batch's pull request as the identity whose runs the batch describes, which is the hands
    holding the pen: the pull request would carry an application as its author, and the `Owner:`
    line that arrangement requires is one this body is forbidden to carry.
    """
    return {key: value for key, value in os.environ.items()
            if key not in runstate.RUN_ENV_KEYS}


def _declared_visibility(execution: str) -> str:
    """Which inbox this clone is, from the inbox's own `inbox.json`.

    Declared by the inbox and never inferred from a directory name or a remote, because this is the
    value every row's `repository_state.visibility` has to equal and the one mistake the inbox
    says cannot be corrected afterwards is filing a row under the wrong one.
    """
    path = os.path.join(execution, "inbox", "inbox.json")
    try:
        with open(path, encoding="utf-8") as handle:
            declared = json.load(handle).get("visibility")
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise Refused(f"{path} does not say which inbox this is ({exc}); a batch is not filed in "
                      f"an inbox whose visibility the producer had to guess") from None
    if not isinstance(declared, str) or not declared:
        raise Refused(f"{path} declares no visibility; a batch is not filed in an inbox whose "
                      f"visibility the producer had to guess")
    return declared


def _standing_batch(net, slug: str, base: str, env=None) -> str | None:
    """The head branch of the inbox batch already open, where there is one.

    A batch is appended to rather than proposed twice, and the date in a branch name is not what
    makes two branches one batch: a flush the day after a batch was opened, while that pull request
    is still unmerged, would cut a fresh branch from the default, find the same rows missing there
    and propose them again. So the open pull requests are asked for first and a standing one is
    what the rows are added to.
    """
    answer = net.api(f"repos/{slug}/pulls?state=open&base={base}", env=env)
    for pull in answer if isinstance(answer, list) else []:
        ref = ((pull or {}).get("head") or {}).get("ref") if isinstance(pull, dict) else None
        if isinstance(ref, str) and ref.startswith(INBOX_BRANCH):
            return ref
    return None


def cmd_flush(args) -> int:
    cfg = config.load()
    net = Runner()
    # A shell still bound to a run is not the person's, whatever the person typed into it. The run
    # sets this variable and nothing else does, so it is the one part of the question that can be
    # answered out loud; the environment below answers the rest by construction.
    bound = os.environ.get("EXERIS_RUN")
    if bound:
        raise Refused(f"this shell is bound to run {bound}; flush is a person's sign-off on a "
                      f"batch of rows and a shell holding a run's identity is not one — open a "
                      f"shell that never sourced the run's env")
    person = _person_environment()

    execution = _clone(cfg, "execution_repo_path", "flush has no inbox to carry rows into")
    streams = _clone(cfg, "streams_repo_path", "flush has nowhere to carry session streams")
    for clone in (execution, streams):
        if net.git(clone, "status", "--porcelain", env=person):
            raise Refused(f"{clone} has uncommitted changes; flush commits into it and will not "
                          f"carry somebody else's work in with a batch of rows")

    declared = _declared_visibility(execution)

    rows = _staged_rows()
    if not rows:
        print(f"no rows staged under {runstate.runs_root()}")
        return EXIT_OK

    sha = _carry_streams(net, streams, rows, _staged_streams(), env=person)
    resolved, pending = _resolve_pending(rows, streams, sha)
    print(f"exeris-agent: {len(rows)} row(s) staged; {resolved} stream reference(s) resolved "
          f"to {sha[:8]}")
    if pending:
        # Counted and kept back rather than carried: the reference is what makes the row's counts
        # checkable against the stream they were taken from, and one that names nothing is a row
        # whose evidence cannot be found.
        print(f"exeris-agent: {len(pending)} row(s) whose stream is not in the streams repository "
              f"keep their pending reference and stay in staging", file=sys.stderr)
    unresolved = {row["path"] for row in pending}
    rows = [row for row in rows if row["path"] not in unresolved]

    # The rows that travel are the ones whose visibility the inbox declares; the rest stay where
    # they are, because filing one here would publish it by the act of filing.
    carried = [row for row in rows if row["visibility"] == declared]
    held = [row for row in rows if row["visibility"] != declared]
    if held:
        kinds = ", ".join(sorted({row["visibility"] for row in held}))
        print(f"exeris-agent: {len(held)} row(s) stay in staging; this inbox declares {declared} "
              f"and they are {kinds}")
    if not carried:
        return EXIT_OK

    if _validate(execution, carried):
        print(f"exeris-agent: the inbox validator is red over {len(carried)} staged row(s); "
              f"nothing was committed and no pull request was opened", file=sys.stderr)
        return EXIT_FAILED

    today = _today()
    slug = worktree.origin_slug(execution)
    base = worktree.default_branch(execution)
    standing = _standing_batch(net, slug, base, env=person)
    branch = standing or f"{INBOX_BRANCH}{today}"
    start = _inbox_branch(net, execution, branch, env=person)
    try:
        for row in carried:
            target = os.path.join(execution, "inbox", row["date"], "runs",
                                  f"{row['run_id']}.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(row["path"], target)
        net.git(execution, "add", "-A", "inbox", env=person)
        if net.git(execution, "diff", "--cached", "--name-only", env=person):
            net.git(execution, "commit", "-m",
                    f"feat(inbox): {len(carried)} harness rows on {today}", env=person)
            net.git(execution, "push", "origin", f"HEAD:refs/heads/{branch}", env=person)
        ahead = net.git_lines(execution, "rev-list", f"origin/{base}..HEAD", env=person)
        if not ahead:
            print(f"exeris-agent: {branch} carries nothing the default branch does not; no pull "
                  f"request was opened")
            return EXIT_OK
        if standing:
            print(f"exeris-agent: {branch} already has an open pull request; the rows were added "
                  f"to it")
            return EXIT_OK
        answer = net.api(f"repos/{slug}/pulls", method="POST", env=person, body={
            "title": f"feat(inbox): {len(carried)} harness rows on {today}",
            "head": branch,
            "base": base,
            "body": record.inbox_pull_request_body(len(carried), today, _repositories(carried)),
        })
        url = (answer or {}).get("html_url") if isinstance(answer, dict) else None
        if url:
            print(url)
    finally:
        net.git(execution, "checkout", start, env=person, check=False)
    return EXIT_OK


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
    open_run.add_argument("--provider", required=True, metavar="<name>",
                          help="the arm this run is: a [providers.<name>] table, or one of the "
                               "built-in defaults claude, codex, gemini")
    open_run.add_argument("--scope", metavar="<scope>",
                          help="the scope within the repository, checked against the vocabulary "
                               "the configuration declares for it")
    open_run.add_argument("--group", metavar="<id>",
                          help="the paired comparison this run is an arm of; requires reg:")
    open_run.add_argument("--arm", metavar="<name>", help="this run's arm of --group")
    open_run.add_argument("--arms-planned", type=int, metavar="<n>",
                          help="how many arms --group is planned to have")
    open_run.add_argument("--baseline", metavar="human|none",
                          help="whether --group was planned with a human arm")
    open_run.add_argument("--group-file", metavar="<path>",
                          help="the group's record, read for the human baseline every row of a "
                               "human-baselined group carries")
    open_run.add_argument("--baseline-required", action=argparse.BooleanOptionalAction,
                          default=None,
                          help="refuse a model arm of a human-baselined group until that "
                               "baseline exists; on by default for such a group")
    open_run.add_argument("--prompt-file", metavar="<path>",
                          help="the prompt as passed; hashed into the manifest, never stored")
    open_run.add_argument("--launch", action="store_true",
                          help="exec the provider's adapter in the worktree instead of printing")
    open_run.set_defaults(handler=cmd_open_run)

    baseline = subcommands.add_parser(
        "baseline", help="open the human arm of a group, and measure what it did",
        description="Open a worktree the person works in as themselves — no token, no run "
                    "environment, hooks that time the commits and stamp them — and, with "
                    "--close, write what the work took, what the oracle made of it and what it "
                    "changed.")
    baseline.add_argument("--repo", metavar="<owner/name|path>",
                          help="the repository the work is done in")
    baseline.add_argument("--task", metavar="reg:<id>",
                          help="the registry entry this baseline measures")
    baseline.add_argument("--group", metavar="<id>",
                          help="the paired comparison this is the human arm of")
    baseline.add_argument("--scope", metavar="<scope>",
                          help="the scope within the repository, checked against the vocabulary "
                               "the configuration declares for it")
    baseline.add_argument("--close", action="store_true",
                          help="measure the run named by --run and print what a group record "
                               "carries")
    baseline.add_argument("--run", metavar="<ULID>", help="the baseline to close")
    baseline.set_defaults(handler=cmd_baseline)

    status = subcommands.add_parser("status", help="list the runs on this machine",
                                    description="Every run directory under the state root, "
                                                "oldest first.")
    status.set_defaults(handler=cmd_status)

    close_run = subcommands.add_parser(
        "close-run", help="push, open the draft pull request, write the run record",
        description="End a run as the execution identity: push its branch, open its draft pull "
                    "request, and stage the run record beside the session stream it references.")
    close_run.add_argument("--run", required=True, metavar="<ULID>",
                           help="the run to close")
    close_run.add_argument("--no-pr", action="store_true",
                           help="push, but open no pull request")
    close_run.add_argument("--title", metavar="<title>",
                           help="the pull request's title; the run's first commit subject by "
                                "default")
    close_run.add_argument("--session", metavar="<path>",
                           help="the session log to read, where the adapter cannot find exactly "
                                "one")
    close_run.add_argument("--remove-worktree", action="store_true",
                           help="give the run's tree back to the clone once it is closed")
    close_run.set_defaults(handler=cmd_close_run)

    flush = subcommands.add_parser(
        "flush", help="carry staged rows into the inbox",
        description="Carry the streams and rows staged by `close-run` into their repositories, "
                    "under the person's own identity. The validator runs first; a red one commits "
                    "nothing.")
    flush.set_defaults(handler=cmd_flush)

    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except Refused as exc:
        print(f"exeris-agent: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (config.ConfigError, token.TokenError, runstate.StateError,
            worktree.WorktreeError, CommandError) as exc:
        print(f"exeris-agent: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
