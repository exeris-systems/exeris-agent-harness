"""What a registered task says about how its work is judged.

A task in the private registry is planned before any arm runs, and part of the plan is what the
oracle is to be told beyond the tree: `oracle_inputs`. For the documentation oracle that is
`preserve`, the files whose bodies the task must leave as they were. It is a property of the task,
not of the arm and not of the repository, so every arm of a group — and the human arm — is judged
with the same inputs, read from the same file.

The file is read when a run opens and its content is recorded then: the task file's own digest, the
`preserve` patterns, and their digest. A registry edited after the run opened would otherwise
change what the run is judged against in the middle of it.

`preserve_sha256` is the SHA-256 of the patterns as compact JSON in the order the task lists them —
`json.dumps(patterns, separators=(",", ":"), ensure_ascii=False)`, UTF-8 — so two runs recorded
with the same digest were judged under the same list.
"""

import hashlib
import json
import os
import re

#: Where a task's record lives inside the registry's clone.
TASKS = ("registry", "tasks")

#: A task id as the registry names its files. A `reg:` reference of any other shape names no file.
_TASK_ID = re.compile(r"T-[0-9]{1,8}")

#: The reference a registered run carries.
PREFIX = "reg:"


class RegistryError(Exception):
    """A registered task whose record cannot be read. Messages name paths, never task text."""


def digest(patterns) -> str:
    """The digest of a `preserve` list, as the module docstring states it."""
    body = json.dumps(list(patterns), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _task_file(root: str, task_id: str) -> str:
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, *TASKS, f"{task_id}.json"))
    if os.path.commonprefix((path, base)) != base or not path.startswith(base + os.sep):
        raise RegistryError(f"the record of {task_id} resolves outside {base}")
    return path


def _preserve(document: dict, path: str) -> list[str]:
    inputs = document.get("oracle_inputs")
    if inputs is None:
        return []
    if not isinstance(inputs, dict):
        raise RegistryError(f"{path}: oracle_inputs is not an object")
    patterns = inputs.get("preserve", [])
    if not isinstance(patterns, list) or not all(
            isinstance(entry, str) and entry for entry in patterns):
        raise RegistryError(f"{path}: oracle_inputs.preserve is not a list of patterns")
    return list(patterns)


def oracle_inputs(root: str, task: str) -> dict:
    """`{"task_sha256", "preserve", "preserve_sha256"}` for a `reg:` task, or `RegistryError`.

    The task's record has to name itself by the id the run was opened with: a file that answers to
    another id is another task's plan.
    """
    task_id = task[len(PREFIX):] if task.startswith(PREFIX) else ""
    if not _TASK_ID.fullmatch(task_id):
        raise RegistryError(f"--task {task!r} names no task file the registry keeps")
    path = _task_file(root, task_id)
    try:
        with open(path, "rb") as handle:
            body = handle.read()
        document = json.loads(body)
    except (OSError, ValueError) as exc:
        raise RegistryError(f"the record of {task_id} could not be read: {exc}") from None
    if not isinstance(document, dict) or document.get("id") != task_id:
        raise RegistryError(f"{path} is not the record of {task_id}")
    patterns = _preserve(document, path)
    return {"task_sha256": hashlib.sha256(body).hexdigest(),
            "preserve": patterns,
            "preserve_sha256": digest(patterns)}
