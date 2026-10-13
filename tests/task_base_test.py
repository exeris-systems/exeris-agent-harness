"""A registered task's base: the commit every arm of it starts from.

A task is planned against a state of the repository. Once its work has landed on the default branch,
a run opened from that branch finds the task already done, so the task names the commit it was
planned against and every arm starts there. The cases pin what has to hold:

* *The base is the run's starting point and its recorded base.* The worktree's head and the
  manifest's `base_sha` are the commit the task names, not the default branch's tip.
* *A base outside the default branch's history is refused,* and so is one that is not a full commit
  id; either way no run is left behind.
* *A task without a base starts where it always did,* from the default branch.
"""

import contextlib
import io
import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from harness import registry  # noqa: E402


class TaskBaseTest(support.HarnessFixture):

    def setUp(self):
        super().setUp()
        self.planned = self._origin_head()
        self._advance_origin()

    def _origin_head(self):
        return self.git("rev-parse", "main", cwd=self.origin).stdout.strip()

    def _advance_origin(self):
        """A commit on the default branch after the one the task was planned against."""
        work = self.tmp / "advance"
        self.git("clone", str(self.origin), str(work))
        (work / "LANDED.md").write_text("the task's work, landed after it was planned\n")
        self.git("add", "-A", cwd=work)
        self.git("commit", "-m", "the task's work lands", cwd=work)
        self.git("push", "origin", "main", cwd=work)
        self.git("fetch", "origin", cwd=self.clone)

    def _task(self, **fields):
        return self.build_registry(body=json.dumps({"id": "T-0001",
                                                    "domain": support.DOCUMENTATION_DOMAIN,
                                                    **fields}))

    def _open(self):
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "claude",
                             "--task", support.TASK, "--scope", support.SCOPE])
        self.said = said.getvalue()
        return code

    def _runs(self):
        return sorted((self.state_root / "runs").glob("*/manifest.json")) \
            if (self.state_root / "runs").is_dir() else []

    def test_the_run_starts_from_the_commit_the_task_names(self):
        self._task(base=self.planned)
        self.assertEqual(0, self._open(), self.said)
        self.assertEqual(self.planned, self.manifest()["base_sha"])
        head = self.git("rev-parse", "HEAD", cwd=self.worktree()).stdout.strip()
        self.assertEqual(self.planned, head)
        self.assertFalse((self.worktree() / "LANDED.md").exists())

    def test_a_task_without_a_base_starts_from_the_default_branch(self):
        self._task()
        self.assertEqual(0, self._open(), self.said)
        tip = self.git("rev-parse", "origin/main", cwd=self.clone).stdout.strip()
        self.assertEqual(tip, self.manifest()["base_sha"])
        self.assertTrue((self.worktree() / "LANDED.md").exists())

    def test_a_base_outside_the_default_branch_s_history_is_refused(self):
        side = self.tmp / "side"
        self.git("clone", str(self.origin), str(side))
        self.git("checkout", "-b", "unmerged", cwd=side)
        (side / "SIDE.md").write_text("a commit nobody merged\n")
        self.git("add", "-A", cwd=side)
        self.git("commit", "-m", "unmerged", cwd=side)
        self.git("push", "origin", "unmerged", cwd=side)
        self.git("fetch", "origin", cwd=self.clone)
        unmerged = self.git("rev-parse", "HEAD", cwd=side).stdout.strip()
        self._task(base=unmerged)
        self.assertNotEqual(0, self._open())
        self.assertIn("not in the history of origin/main", self.said)
        self.assertEqual([], self._runs())

    def test_a_base_that_is_not_a_full_commit_id_is_refused(self):
        for base in (self.planned[:12], "main", "HEAD~1", 7):
            with self.subTest(base=base):
                self._task(base=base)
                self.assertNotEqual(0, self._open())
                self.assertIn("base is not a full commit id", self.said)
                self.assertEqual([], self._runs())

    def test_the_registry_reads_the_base_and_nothing_where_there_is_none(self):
        path = self._task(base=self.planned)
        root = str(path.parent.parent.parent)
        self.assertEqual(self.planned, registry.task_base(root, support.TASK))
        self._task()
        self.assertIsNone(registry.task_base(root, support.TASK))


if __name__ == "__main__":
    unittest.main()
