"""The Exeris MCP server for the arms: the same pinned server, given the way each client takes it.

An arm working in a repository whose table says `mcp = true` is given the server `[oracle] bridge`
pins. The cases pin what has to hold however the clients change:

* *Claude Code is given it per invocation, and nothing else.* `open-run` writes a one-server
  configuration into the run's directory; every driven pass names it with `--mcp-config` and drops
  the person's own servers with `--strict-mcp-config`, and the server's read-only documentation
  tools are allowed by the name the client gives an MCP tool.
* *Antigravity is given a home of the run's own.* It reads its permissions and its servers from
  files under `$HOME`, so `open-run` writes `<run>/agy-home` with the fixed allow-list and the
  pinned server or none, and the person's own configuration decides nothing. What the client lists
  under that home is checked — exactly the pinned server, or none — because a listing that differs
  is a client reading its servers from somewhere the harness did not write.
* *The manifest says what the arm had and was allowed, and the row's fence says it too.*

`agy mcp list` is answered by the stand-in the fixture puts on the path, from the home it runs
under.
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
from drive_test import DriveFixture, _judgement  # noqa: E402

from adapters.antigravity import drive as agy_drive  # noqa: E402
from adapters.claude import drive as claude_drive  # noqa: E402
from harness import bridge, cli, config  # noqa: E402

#: The three tools the claude arm is allowed, by the name Claude Code gives an MCP tool.
EXPECTED_TOOLS = ["mcp__exeris__docs-list_adrs", "mcp__exeris__docs-get_adr",
                  "mcp__exeris__docs-search"]

#: What the Antigravity arm's home allows, in that client's grammar: Claude Code's commands, and
#: the same three server tools where the arm is given the server.
AGY_COMMANDS = ["command(git add)", "command(git commit)", "command(git status)",
                "command(git diff)", "command(git log)", "command(ls)", "command(pwd)",
                "command(cat)", "command(head)", "command(tail)", "command(find)", "command(grep)",
                "command(wc)", "command(git show)", "command(git rev-parse)",
                "command(git ls-files)", "command(which)"]
AGY_MCP = ["mcp(exeris/docs-list_adrs)", "mcp(exeris/docs-get_adr)", "mcp(exeris/docs-search)"]

MCP_FENCE = support.FENCE.replace("harness-claude-", "harness-claude-mcp-")


class McpFixture(DriveFixture):
    """A pinned server, a readable directory publishing the ADR registry, and a scripted oracle."""

    def setUp(self):
        super().setUp()
        self.commit_of_bridge = self.build_bridge()
        self.docs = self.tmp / "exeris-docs"
        self.docs.mkdir()
        (self.docs / bridge.DOCS_INDEX).write_text("| ADR | placeholder |\n", encoding="utf-8")
        self.standards = self.tmp / "standards"
        self.standards.mkdir()

    def give_mcp(self, value="true"):
        self.configure_repo(f"mcp = {value}",
                            f'readable = ["{self.standards}", "{self.docs}"]')

    def open_refused(self, provider="claude"):
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", provider,
                             "--task", support.TASK, "--scope", support.SCOPE,
                             "--prompt-file", str(self.task)])
        self.assertEqual(2, code, said.getvalue())
        self.assertEqual([], list(self.state_root.rglob("manifest.json")))
        return said.getvalue()


class ClaudeMcpTest(McpFixture):
    """The client that takes its servers on its command line."""

    def test_the_run_writes_a_one_server_configuration_for_the_pinned_bridge(self):
        self.give_mcp()
        self.open_driven()
        written = self.run_dir() / cli.MCP_FILE
        self.assertEqual({"mcpServers": {"exeris": {
            "command": "node", "args": [str(self.bridge.resolve())],
            # The first readable directory that publishes the registry, not merely the first one.
            "env": {"EXERIS_DOCS_ROOT": str(self.docs), "EXERIS_BRIDGE_MODE": "contributor"},
        }}}, json.loads(written.read_text()))
        self.assertEqual(0o600, written.stat().st_mode & 0o777)
        self.assertEqual({"server": "exeris",
                          "bridge": {"version": support.BRIDGE_VERSION,
                                     "commit": self.commit_of_bridge},
                          "tools": list(claude_drive.MCP_TOOLS)}, self.manifest()["mcp"])

    def test_every_pass_is_given_the_configuration_strictly_and_the_three_tools(self):
        self.give_mcp()
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)
        self.assertEqual(2, len(self.passes))
        for made in self.passes:
            argv = made["argv"]
            self.assertEqual(str(self.run_dir() / cli.MCP_FILE),
                             argv[argv.index("--mcp-config") + 1])
            self.assertIn("--strict-mcp-config", argv)
            allowed = argv[argv.index("--allowedTools") + 1].split(",")
            self.assertEqual(EXPECTED_TOOLS, [tool for tool in allowed
                                              if tool.startswith("mcp__")])
            self.assertTrue(argv[argv.index("--allowedTools") + 1].startswith(
                claude_drive.ALLOWED_TOOLS))

    def test_an_arm_given_the_server_sits_on_a_fence_of_its_own(self):
        self.give_mcp()
        self.register_fence(MCP_FENCE)
        self.script = [_judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self._close()
        self.assertEqual(MCP_FENCE, self.staged_row()["instrument"]["fence"])

    def test_an_arm_not_given_the_server_is_given_no_configuration(self):
        self.script = [_judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        argv = self.passes[0]["argv"]
        self.assertNotIn("--mcp-config", argv)
        self.assertEqual(claude_drive.ALLOWED_TOOLS, argv[argv.index("--allowedTools") + 1])
        self.assertFalse((self.run_dir() / cli.MCP_FILE).exists())
        self.assertIsNone(self.manifest()["mcp"])
        self._close()
        self.assertEqual(support.FENCE, self.staged_row()["instrument"]["fence"])

    def test_a_server_that_is_not_pinned_is_given_to_no_arm(self):
        self.config_path.write_text(self.config_path.read_text().replace(
            f'bridge = "{self.bridge}"\n', ""))
        self.give_mcp()
        self.assertIn("[oracle] bridge names no server", self.open_refused())

    def test_a_server_with_no_registry_to_serve_is_given_to_no_arm(self):
        (self.docs / bridge.DOCS_INDEX).unlink()
        self.give_mcp()
        self.assertIn(bridge.DOCS_INDEX, self.open_refused())

    def test_a_flag_that_is_not_true_or_false_is_not_read_as_either(self):
        self.give_mcp('"yes"')
        with self.assertRaises(config.ConfigError):
            config.load(str(self.config_path))

    def test_the_tool_names_are_the_ones_claude_code_gives_an_mcp_tool(self):
        self.assertEqual(EXPECTED_TOOLS, claude_drive.mcp_tools())


class AntigravityMcpTest(McpFixture):
    """The client that reads its permissions and its servers from a home of the run's own."""

    def pinned_line(self, name="exeris", status="enabled"):
        return f"{name}  stdio  {status}  node {self.bridge.resolve()}"

    def open_agy(self):
        said = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(said):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "antigravity",
                             "--task", support.TASK, "--scope", support.SCOPE,
                             "--prompt-file", str(self.task)])
        self.assertEqual(0, code, said.getvalue())
        return self.manifest()

    def agy_home(self):
        return self.run_dir() / agy_drive.HOME

    def read_home(self, *parts):
        return json.loads((self.agy_home() / pathlib.Path(*parts)).read_text(encoding="utf-8"))

    def assert_home(self, allow, servers):
        """The run's home holds the three files the client reads, as the harness wrote them, and
        nothing else — no credential, no file carried in from the person's home."""
        home = self.agy_home()
        self.assertEqual({"permissions": {"allow": allow}, "allowNonWorkspaceAccess": False,
                          "trustedWorkspaces": [str(self.worktree())]},
                         self.read_home(".gemini", "antigravity-cli", "settings.json"))
        self.assertEqual({"userSettings": {"globalPermissionGrants": {"allow": []},
                                           "permissionGrantsV2Migrated": True}},
                         self.read_home(".gemini", "config", "config.json"))
        self.assertEqual({"mcpServers": servers},
                         self.read_home(".gemini", "config", "mcp_config.json"))
        files = sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())
        self.assertEqual([".gemini/antigravity-cli/settings.json", ".gemini/config/config.json",
                          ".gemini/config/mcp_config.json"], files)
        for path in [home, *home.rglob("*")]:
            with self.subTest(path=str(path)):
                self.assertEqual(0o700 if path.is_dir() else 0o600, path.stat().st_mode & 0o777)

    def test_an_arm_given_the_server_gets_a_home_granting_the_commands_and_its_three_tools(self):
        self.give_mcp()
        manifest = self.open_agy()
        self.assert_home(AGY_COMMANDS + AGY_MCP, {"exeris": {
            "command": "node", "args": [str(self.bridge.resolve())],
            # The first readable directory that publishes the registry, as for the other client.
            "env": {"EXERIS_DOCS_ROOT": str(self.docs), "EXERIS_BRIDGE_MODE": "contributor"},
            "disabled": False}})
        self.assertEqual({"server": "exeris",
                          "bridge": {"version": support.BRIDGE_VERSION,
                                     "commit": self.commit_of_bridge},
                          "tools": list(claude_drive.MCP_TOOLS),
                          "observed": " ".join(self.pinned_line().split())}, manifest["mcp"])
        self.assertEqual(AGY_COMMANDS + AGY_MCP, manifest["permissions"])
        # The configuration file is the other client's; this one reads its home.
        self.assertFalse((self.run_dir() / cli.MCP_FILE).exists())

    def test_an_arm_not_given_the_server_gets_a_home_granting_the_commands_and_no_server(self):
        manifest = self.open_agy()
        self.assert_home(AGY_COMMANDS, {})
        self.assertIsNone(manifest["mcp"])
        self.assertEqual(AGY_COMMANDS, manifest["permissions"])

    def test_the_allow_list_is_the_other_arm_s_commands_and_the_reading_stand_ins(self):
        self.assertEqual(AGY_COMMANDS + AGY_MCP, agy_drive.permissions(mcp=True))
        self.assertEqual(list(claude_drive.MCP_TOOLS), list(agy_drive.MCP_TOOLS))
        tools = claude_drive.ALLOWED_TOOLS.split(",")
        commands = [entry[len("Bash("):-len(":*)")] for entry in tools if entry.startswith("Bash(")]
        granted = agy_drive.permissions(mcp=False)
        # The other arm's commands, by name and in its order, come first.
        self.assertEqual([f"command({command})" for command in commands],
                         granted[:len(commands)])
        # The rest stand in for the reading tools the other arm is given as tools: commands that
        # read. Neither `git config` nor `git branch` is among them: the first can unset the hook
        # that stamps the run's commits, the second can delete branches in the shared clone.
        self.assertTrue({"Read", "Glob", "Grep"} <= set(tools), tools)
        self.assertFalse({"command(git config)", "command(git branch)"} & set(granted), granted)
        self.assertEqual(["command(pwd)", "command(cat)", "command(head)", "command(tail)",
                          "command(find)", "command(grep)", "command(wc)",
                          "command(git show)", "command(git rev-parse)", "command(git ls-files)",
                          "command(which)"],
                         granted[len(commands):])

    def test_the_person_s_own_configuration_decides_nothing(self):
        # Servers and grants a person configured by hand, the bridge among them: none of them
        # reaches the run's home, and none of them refuses a run.
        self.person_agy_config({"exeris": {"command": "node", "args": [str(self.bridge.resolve())]},
                                "other": {"command": "node", "args": ["/opt/placeholder/s.js"]}},
                               allow=["command(rm -rf)", "command(git push)"])
        original = self.config_path.read_text()
        for flag, allow, servers in (("false", AGY_COMMANDS, 0),
                                     ("true", AGY_COMMANDS + AGY_MCP, 1)):
            with self.subTest(mcp=flag):
                self.config_path.write_text(original)
                self.give_mcp(flag)
                manifest = self.open_agy()
                self.assertEqual(allow, manifest["permissions"])
                self.assertEqual(allow, self.read_home(".gemini", "antigravity-cli",
                                                       "settings.json")["permissions"]["allow"])
                self.assertEqual(servers, len(self.read_home(".gemini", "config",
                                                             "mcp_config.json")["mcpServers"]))

    def test_a_server_the_client_finds_outside_the_run_s_home_is_refused(self):
        original = self.config_path.read_text()
        for flag in ("true", "false"):
            for listing in (("other  stdio  enabled  node /opt/placeholder/server.js",),
                            (self.pinned_line(name="second"),)):
                with self.subTest(mcp=flag, listing=listing):
                    self.config_path.write_text(original)
                    self.give_mcp(flag)
                    self.agy_servers(*listing)
                    said = self.open_refused("antigravity")
                    self.assertIn("under the run's own home", said)
        # A refused run leaves no home behind: the run's directory goes with it.
        self.assertEqual([], list(self.state_root.rglob(agy_drive.HOME)))

    def test_the_driven_passes_read_the_run_s_home(self):
        self.give_mcp()
        self.person_agy_config({"other": {"command": "node", "args": ["/opt/placeholder/s.js"]}})
        self.conversation = "00000000-0000-4000-8000-00000000000a"
        self.script = [_judgement("TRUE_DONE")]
        self.open_agy()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertEqual(1, len(self.passes))

    def test_a_home_whose_servers_changed_after_the_run_opened_is_not_driven(self):
        self.give_mcp()
        self.conversation = "00000000-0000-4000-8000-00000000000a"
        self.script = [_judgement("TRUE_DONE")]
        self.open_agy()
        servers = self.agy_home() / ".gemini" / "config" / "mcp_config.json"
        written = json.loads(servers.read_text(encoding="utf-8"))
        written["mcpServers"]["second"] = written["mcpServers"]["exeris"]
        servers.write_text(json.dumps(written), encoding="utf-8")
        self.assertEqual(2, self.drive(0))
        self.assertEqual([], self.passes)

    def test_a_home_whose_grants_changed_after_the_run_opened_is_not_driven(self):
        self.conversation = "00000000-0000-4000-8000-00000000000a"
        self.script = [_judgement("TRUE_DONE")]
        self.open_agy()
        settings = self.agy_home() / ".gemini" / "antigravity-cli" / "settings.json"
        written = json.loads(settings.read_text(encoding="utf-8"))
        written["permissions"]["allow"].append("command(git push)")
        settings.write_text(json.dumps(written), encoding="utf-8")
        self.assertEqual(2, self.drive(0))
        self.assertIn("no longer grants", self.said)
        self.assertEqual([], self.passes)

    def test_the_listing_is_read_as_the_client_prints_it(self):
        listed = agy_drive.mcp_servers(
            "NAME              TYPE   STATUS   COMMAND/URL\n"
            "exeris-ai-bridge  stdio  enabled  node /opt/a b/dist/server.js\n")
        self.assertEqual([{"name": "exeris-ai-bridge", "type": "stdio", "status": "enabled",
                           "command": "node /opt/a b/dist/server.js",
                           "line": "exeris-ai-bridge stdio enabled node /opt/a b/dist/server.js"}],
                         listed)


if __name__ == "__main__":
    unittest.main()
