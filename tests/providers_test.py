"""Provider tables: what an arm declares, and what of it a run is allowed to carry.

An arm is a model behind a client, under a ledger, launched by an adapter. The cases here are the
ones that decide whether a row assembled from such a table says something true.

* *The vendor and the ledger are the arm's, not the repository's.* One repository is worked by a
  vendor arm and a local arm on the same day; a class read from the repository would report both
  under whichever was configured there.
* *A weights digest and a local ledger are one fact stated twice.* The row contract admits a digest
  only under `local`, so a table that states one half is refused where it is written rather than at
  the far end, one flush later.
* *A table cannot reach into the run's boundary.* The launch environment adds the arm's own
  variables and can set none of the names the run's identity is made of.
* *A run that could never be recorded does not open.* An unknown table, an adapter this checkout
  does not carry, weights that are not there: each is refused before a directory or a token exists.
"""

import hashlib
import json
import os
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from harness import config, providers  # noqa: E402


class ProvidersTest(support.HarnessFixture):
    def setUp(self):
        super().setUp()
        #: The configuration the fixture wrote, before a case adds a table of its own. Written back
        #: each time rather than appended to, so that one case's table is not in force in the next.
        self.base_config = self.config_path.read_text()

    def _config(self, table: str, name="local-arm"):
        """The fixture's configuration with one more provider table in it."""
        self.config_path.write_text(f"{self.base_config}\n[providers.{name}]\n{table}")
        return config.load(str(self.config_path))

    def _weights(self, body=b"placeholder weights"):
        path = self.tmp / "weights.gguf"
        path.write_bytes(body)
        return path

    # ---- what a table declares ---------------------------------------------------

    def test_the_builtin_names_the_vendor_and_the_adapter_and_no_ledger(self):
        # A vendor CLI's client and vendor are properties of the client; which ledger it bills to
        # is a property of the login it runs under, which no default can know.
        cfg = config.load(str(self.config_path))
        gemini = providers.resolve(cfg, "gemini")
        self.assertEqual("google", gemini.provider)
        self.assertEqual("gemini", gemini.adapter)
        self.assertIsNone(gemini.credential)

    def test_a_table_completes_the_builtin_of_the_same_name(self):
        # The fixture declares `[providers.claude]` with a ledger and nothing else; the vendor and
        # the adapter come from the default, and the run is opened under both halves.
        cfg = config.load(str(self.config_path))
        claude = providers.resolve(cfg, "claude")
        self.assertEqual(support.PROVIDER, claude.provider)
        self.assertEqual("claude", claude.adapter)
        self.assertEqual(support.CREDENTIAL, claude.credential)

    def test_weights_become_the_snapshot_and_are_digested_when_the_run_opens(self):
        weights = self._weights()
        cfg = self._config(f'provider = "local"\ncredential = "local"\nadapter = "claude"\n'
                           f'model_id = "placeholder-local-model"\nweights = "{weights}"\n')
        arm = providers.resolve(cfg, "local-arm")
        self.assertEqual(f"sha256:{hashlib.sha256(weights.read_bytes()).hexdigest()}",
                         arm.model_snapshot)

    def test_weights_outside_the_local_ledger_are_refused(self):
        weights = self._weights()
        cfg = self._config(f'provider = "local"\ncredential = "subscription"\n'
                           f'adapter = "claude"\nweights = "{weights}"\n')
        with self.assertRaises(providers.ProviderError) as refusal:
            providers.resolve(cfg, "local-arm")
        self.assertIn("local", str(refusal.exception))

    def test_a_launch_value_is_read_from_the_file_it_names(self):
        # A value that has to be a secret is a path, and the file is read when the run opens. A
        # configuration file that says it holds no secret has to go on being true.
        read_at_open = self.tmp / "proxy-token"
        read_at_open.write_text("placeholder-proxy-token\n")
        cfg = self._config('provider = "local"\ncredential = "local"\nadapter = "claude"\n'
                           '[providers.local-arm.env]\n'
                           'ANTHROPIC_BASE_URL = "http://127.0.0.1:8080"\n'
                           f'ANTHROPIC_AUTH_TOKEN = "file:{read_at_open}"\n')
        arm = providers.resolve(cfg, "local-arm")
        self.assertEqual("placeholder-proxy-token", arm.env["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual("http://127.0.0.1:8080", arm.env["ANTHROPIC_BASE_URL"])

    def test_a_launch_value_cannot_be_one_of_the_run_s_own(self):
        for name in ("GH_TOKEN", "GIT_CONFIG_GLOBAL", "SSH_AUTH_SOCK"):
            with self.subTest(variable=name):
                cfg = self._config('provider = "local"\ncredential = "local"\n'
                                   'adapter = "claude"\n'
                                   f'[providers.local-arm.env]\n{name} = "anything"\n')
                with self.assertRaises(providers.ProviderError) as refusal:
                    providers.resolve(cfg, "local-arm")
                self.assertIn(name, str(refusal.exception))

    def test_a_misspelled_key_is_refused_rather_than_ignored(self):
        cfg = self._config('provider = "local"\ncredentials = "local"\nadapter = "claude"\n')
        with self.assertRaises(providers.ProviderError) as refusal:
            providers.resolve(cfg, "local-arm")
        self.assertIn("credentials", str(refusal.exception))

    # ---- what the run records of it ----------------------------------------------

    def test_the_manifest_records_the_arm_and_the_names_of_its_variables_only(self):
        read_at_open = self.tmp / "proxy-token"
        read_at_open.write_text("placeholder-proxy-token\n")
        weights = self._weights()
        self.config_path.write_text(self.base_config + (
            "\n[providers.local-claude]\n"
            'provider = "local"\n'
            'credential = "local"\n'
            'adapter = "claude"\n'
            'model_id = "placeholder-local-model"\n'
            f'weights = "{weights}"\n'
            "[providers.local-claude.env]\n"
            'ANTHROPIC_BASE_URL = "http://127.0.0.1:8080"\n'
            f'ANTHROPIC_AUTH_TOKEN = "file:{read_at_open}"\n'))

        self.assertEqual(0, self.cli(["open-run", "--repo", str(self.clone),
                                      "--provider", "local-claude", "--task", support.TASK,
                                      "--scope", support.SCOPE]))
        arm = self.manifest()["provider_table"]
        self.assertEqual("local", arm["provider"])
        self.assertEqual("local", arm["credential"])
        self.assertTrue(arm["model_snapshot"].startswith("sha256:"), arm["model_snapshot"])
        # The names of the variables the adapter exports, and none of their values: a token read
        # out of a file to be exported is a credential, and the record of a run is not where a
        # credential is kept.
        self.assertEqual(["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"], arm["env_keys"])
        self.assertNotIn("placeholder-proxy-token", json.dumps(arm))

        # The environment the run is entered with carries them, because that is the file the run's
        # own token is already in, at that file's mode.
        env = (self.run_dir() / "env").read_text()
        self.assertIn("ANTHROPIC_BASE_URL='http://127.0.0.1:8080'", env)
        self.assertIn("ANTHROPIC_AUTH_TOKEN='placeholder-proxy-token'", env)
        self.assertEqual(0o600, os.stat(self.run_dir() / "env").st_mode & 0o777)

    def test_a_local_arm_s_rows_are_not_under_the_vendor_arm_s_fence(self):
        # The two arms reach one client at one version and differ in the weights: without the
        # digest in the id they resolve the same entry, and rows whose model reference differs
        # would be summarised in one figure — which is what a fence exists to prevent. Swapping
        # the weights moves the rows again, for the same reason.
        weights = self._weights(support.LOCAL_WEIGHTS)
        self.config_path.write_text(self.base_config + (
            "\n[providers.local-claude]\n"
            'provider = "local"\n'
            'credential = "local"\n'
            'adapter = "claude"\n'
            'model_id = "placeholder-local-model"\n'
            f'weights = "{weights}"\n'))

        self.assertEqual(0, self.cli(["open-run", "--repo", str(self.clone),
                                      "--provider", "local-claude", "--task", support.TASK,
                                      "--scope", support.SCOPE]))
        self.commit()
        self.place_session()
        self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))

        row = self.staged_row()
        self.assertEqual(f"sha256:{support.LOCAL_SNAPSHOT}", row["agent"]["model_snapshot"])
        self.assertEqual("local", row["accounting"]["mode"])
        self.assertEqual(support.LOCAL_FENCE, row["instrument"]["fence"])
        self.assertNotEqual(support.FENCE, row["instrument"]["fence"])

    def test_a_builtin_opens_a_run_with_no_table_of_its_own(self):
        # The three vendor CLIs go on working under their own names. What such a run cannot say is
        # which ledger it was billed to, so it opens and its row is refused rather than guessed at.
        self.assertEqual(0, self.cli(["open-run", "--repo", str(self.clone), "--provider", "codex",
                                      "--task", support.TASK, "--scope", support.SCOPE]))
        arm = self.manifest()["provider_table"]
        self.assertEqual("openai", arm["provider"])
        self.assertEqual("codex", arm["adapter"])
        self.assertIsNone(arm["credential"])

    def test_a_run_whose_weights_are_absent_does_not_open(self):
        self.config_path.write_text(self.base_config + (
            "\n[providers.local-claude]\n"
            'provider = "local"\n'
            'credential = "local"\n'
            'adapter = "claude"\n'
            f'weights = "{self.tmp / "no-such-weights.gguf"}"\n'))
        code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "local-claude",
                         "--task", support.TASK, "--scope", support.SCOPE])
        self.assertEqual(2, code, "a run opened under weights nobody could digest")
        self.assertEqual([], list(self.state_root.rglob("manifest.json")),
                         "a refused run left state behind")

    def test_a_provider_no_table_names_does_not_open(self):
        code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "not-configured",
                         "--task", support.TASK, "--scope", support.SCOPE])
        self.assertEqual(2, code)
        self.assertEqual([], list(self.state_root.rglob("manifest.json")))

    def test_an_adapter_that_takes_the_task_on_its_command_line_needs_the_task(self):
        # The stream this client writes carries no prompt text, so a run opened without a prompt
        # file has nothing to hash and could never be recorded.
        code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "antigravity",
                         "--task", support.TASK, "--scope", support.SCOPE])
        self.assertEqual(2, code)
        self.assertEqual([], list(self.state_root.rglob("manifest.json")))


if __name__ == "__main__":
    unittest.main()
