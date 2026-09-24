"""The Exeris MCP server for the arms: the same pinned server, given the way each client takes it.

An arm working in a repository whose table says `mcp = true` is given the server `[oracle] bridge`
pins. The cases pin what has to hold however the clients change:

* *Claude Code is given it per invocation, and nothing else.* `open-run` writes a one-server
  configuration into the run's directory; every driven pass names it with `--mcp-config` and drops
  the person's own servers with `--strict-mcp-config`, and the server's read-only documentation
  tools are allowed by the name the client gives an MCP tool.
* *Antigravity is checked, not given.* It reads its own user-level configuration, so the run opens
  only where that configuration enables exactly the pinned server — and, for an arm not given the
  server, only where it enables none of the bridge — because the arms of a group read through the
  same context tools.
* *The manifest says what the arm had, and the row's fence says it too.*

`agy mcp list` is answered by the stand-in the fixture puts on the path.
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
    """The client that reads its servers from its own configuration."""

    def pinned_line(self, name="exeris", status="enabled"):
        return f"{name}  stdio  {status}  node {self.bridge.resolve()}"

    def open_agy(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "antigravity",
                             "--task", support.TASK, "--scope", support.SCOPE,
                             "--prompt-file", str(self.task)])
        self.assertEqual(0, code)
        return self.manifest()

    def test_a_configured_server_that_is_the_pinned_one_is_accepted_and_recorded(self):
        self.give_mcp()
        self.agy_servers("other  stdio  enabled  node /opt/placeholder/server.js",
                         self.pinned_line())
        manifest = self.open_agy()
        self.assertEqual({"server": "exeris",
                          "bridge": {"version": support.BRIDGE_VERSION,
                                     "commit": self.commit_of_bridge},
                          "tools": list(agy_drive.MCP_TOOLS),
                          "observed": " ".join(self.pinned_line().split())}, manifest["mcp"])
        # Nothing is written for a client that takes no configuration per invocation.
        self.assertFalse((self.run_dir() / cli.MCP_FILE).exists())

    def test_a_configured_server_other_than_the_pinned_one_is_refused(self):
        self.give_mcp()
        for listing in (("exeris  stdio  enabled  node /opt/elsewhere/dist/server.js",),
                        (self.pinned_line(status="disabled"),),
                        ()):
            with self.subTest(listing=listing):
                self.agy_servers(*listing)
                said = self.open_refused("antigravity")
                self.assertIn("agy mcp add", said)
                self.assertIn(f"node {self.bridge.resolve()}", said)

    def test_an_arm_not_given_the_server_is_refused_where_its_client_enables_the_bridge(self):
        self.give_mcp("false")
        for line in (self.pinned_line(name="anything"),
                     "exeris-ai-bridge  stdio  enabled  node /opt/exeris-ai-bridge/dist/server.js"):
            with self.subTest(line=line):
                self.agy_servers(line)
                self.assertIn("agy mcp disable", self.open_refused("antigravity"))

    def test_an_arm_not_given_the_server_opens_where_the_bridge_is_disabled(self):
        self.agy_servers(self.pinned_line(status="disabled"),
                         "other  stdio  enabled  node /opt/placeholder/server.js")
        self.assertIsNone(self.open_agy()["mcp"])

    def test_a_configuration_changed_after_the_run_opened_is_not_driven(self):
        self.give_mcp()
        self.agy_servers(self.pinned_line())
        self.conversation = "00000000-0000-4000-8000-00000000000a"
        self.script = [_judgement("TRUE_DONE")]
        self.open_agy()
        self.agy_servers(self.pinned_line(), self.pinned_line(name="second"))
        self.assertEqual(2, self.drive(0))
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
