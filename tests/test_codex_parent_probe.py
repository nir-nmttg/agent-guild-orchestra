import importlib.util
import json
import os
from pathlib import Path
import selectors
from types import SimpleNamespace
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_codex_parent.py"
SPEC = importlib.util.spec_from_file_location("check_codex_parent", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeHelpersTests(unittest.TestCase):
    def test_timeout_leaves_unobserved_roles_unknown(self) -> None:
        rpc = SimpleNamespace(
            messages=[],
            call=lambda *args, **kwargs: {"result": {"turn": {"id": "turn"}}},
            wait_turn=lambda *args: "timeout",
        )
        native, permission = probe.live_probe(rpc, "parent", Path("parent"), Path("child"), 1)
        self.assertEqual(native["status"], "unknown")
        self.assertEqual({item["status"] for item in native["evidence"]["expected"].values()}, {"unknown"})
        self.assertEqual(native["evidence"]["requestedRoles"], list(probe.EXPECTED))
        self.assertEqual(permission["reason"], "no live child thread was observed")

    def test_role_selection_limits_live_probe_without_changing_static_roles(self) -> None:
        options = probe.parse_args(["--live", "--role", "scholar", "--role", "sentinel"])
        self.assertEqual(options.live_roles, ("scholar", "sentinel"))
        self.assertEqual(probe.parse_args(["--roles", "verifier,sentinel,verifier"]).live_roles, ("verifier", "sentinel"))
        defaults = probe.parse_args([])
        self.assertEqual(defaults.live_roles, tuple(probe.EXPECTED))
        self.assertEqual(defaults.live_timeout, 45.0)

    def test_empty_role_selection_is_rejected_instead_of_running_all_roles(self) -> None:
        for value in ("", ",,"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "requires at least one role"):
                    probe.resolve_roles(None, [value])

    def test_rpc_reads_multiple_buffered_json_lines(self) -> None:
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        os.write(write_fd, b'{"id": 1}\n{"id": 2}\n')
        os.close(write_fd)
        rpc = probe.Rpc("codex", Path("."), Path("."))
        rpc.proc = SimpleNamespace(stdout=reader)
        rpc.sel = selectors.DefaultSelector()
        rpc.sel.register(reader.fileno(), selectors.EVENT_READ)
        try:
            self.assertEqual(rpc._read(time.monotonic() + 1)["id"], 1)
            self.assertEqual(rpc._read(time.monotonic() + 1)["id"], 2)
            rpc.messages.append({"method": "turn/completed", "params": {"threadId": "parent"}})
            self.assertEqual(rpc.wait_turn("parent", 1), "completed")
        finally:
            rpc.sel.close()
            reader.close()

    def test_skill_rows_are_cwd_scoped_and_reject_leaks_disabled_and_errors(self) -> None:
        parent = Path("/tmp/probe-parent")
        child = parent / "repositories" / "app"
        parent_skill = parent / ".agents/skills/parent/SKILL.md"
        child_skill = child / ".agents/skills/child-collision/SKILL.md"
        value = {
            "data": [
                {
                    "cwd": str(parent),
                    "skills": [
                        {"name": "parent", "path": str(parent_skill), "enabled": True},
                        {"name": "child-collision", "path": str(child_skill), "enabled": True},
                    ],
                    "errors": [],
                },
                {
                    "cwd": str(child),
                    "skills": [
                        {"name": "child-collision", "path": str(child_skill), "enabled": False},
                        {"name": "parent", "path": str(parent_skill), "enabled": True},
                    ],
                    "errors": ["parse-error"],
                },
            ]
        }
        parent_view = probe.project_skill_view(value, parent, child)
        child_view = probe.project_skill_view(value, child, parent)
        self.assertEqual(parent_view["names"], ["parent"])
        self.assertEqual(parent_view["leakedNames"], ["child-collision"])
        self.assertTrue(parent_view["enabled"])
        self.assertEqual(child_view["names"], ["child-collision"])
        self.assertEqual(child_view["leakedNames"], ["parent"])
        self.assertFalse(child_view["enabled"])
        self.assertEqual(child_view["errors"], ["parse-error"])

    def test_collab_event_keeps_status_but_drops_agent_messages(self) -> None:
        rpc = SimpleNamespace(
            messages=[
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "parent",
                        "item": {
                            "type": "collabAgentToolCall",
                            "tool": "spawnAgent",
                            "model": "gpt-6-luna",
                            "reasoningEffort": "max",
                            "receiverThreadIds": ["child"],
                            "agentsStates": {"child": {"status": "completed", "message": "model prose"}},
                            "status": "completed",
                        },
                    },
                }
            ]
        )
        event = probe.collab_items(rpc, "parent")[0]
        self.assertEqual(event["agentStatuses"], {"child": "completed"})
        self.assertNotIn("agentsStates", event)
        self.assertNotIn("message", json.dumps(event))

    def test_live_requires_parent_thread_read_and_marks_child_permission_unknown(self) -> None:
        class FakeRpc:
            def __init__(self, roles=None) -> None:
                self.messages = []
                self.current_effort = None
                self.spawn_count = 0
                self.roles = list(roles or probe.EXPECTED)
                self.metadata_roles = {}
                self.metadata_overrides = {}

            def call(self, method, params, timeout=20):
                if method == "turn/start":
                    self.current_effort = params["effort"]
                    return {"result": {"turn": {"id": f"turn-{self.current_effort}"}}}
                if method == "thread/read":
                    thread_id = params["threadId"]
                    if thread_id == "parent":
                        return {"result": {"thread": {"model": "gpt-6-astra", "reasoningEffort": self.current_effort, "cwd": "parent"}}}
                    agent = thread_id.removeprefix("child-")
                    want = probe.EXPECTED[agent]
                    role = self.metadata_roles.get(agent, agent)
                    metadata = {"id": thread_id, "parentThreadId": "parent", "agentNickname": agent, "agentRole": role, "model": want["model"], "reasoningEffort": want["effort"], "cwd": "child"}
                    metadata.update(self.metadata_overrides.get(agent, {}))
                    return {"result": {"thread": metadata}}
                raise AssertionError(method)

            def wait_turn(self, thread_id, timeout):
                agent = self.roles[self.spawn_count]
                want = probe.EXPECTED[agent]
                child_id = f"child-{agent}"
                self.messages.append({"method": "item/completed", "params": {"threadId": "parent", "item": {"type": "collabAgentToolCall", "tool": "spawnAgent", "model": want["model"], "reasoningEffort": want["effort"], "receiverThreadIds": [child_id], "agentsStates": {}, "status": "completed"}}})
                self.spawn_count += 1
                return "completed"

        native, permission = probe.live_probe(FakeRpc(), "parent", Path("parent"), Path("child"), 1)
        self.assertEqual(native["status"], "observed")
        self.assertEqual([item["actual"]["reasoningEffort"] for item in native["evidence"]["rootThreadReads"]], ["low", "xhigh", "xhigh", "xhigh", "xhigh"])
        for role in probe.EXPECTED:
            evidence = native["evidence"]["expected"][role]
            self.assertEqual(evidence["status"], "observed")
            self.assertEqual(evidence["declaration"], probe.EXPECTED[role])
            self.assertEqual(evidence["observation"]["spawnEventCount"], 1)
            self.assertEqual(evidence["observation"]["childMetadataMatches"], 1)
        sentinel = native["evidence"]["expected"]["sentinel"]
        self.assertEqual(sentinel["declaration"], {"model": "gpt-6-sol", "effort": "xhigh", "sandbox": "read-only"})
        self.assertEqual(sentinel["observation"]["spawnEventCount"], 1)
        self.assertEqual(sentinel["observation"]["childMetadataMatches"], 1)
        inquisitor = native["evidence"]["expected"]["inquisitor"]
        self.assertEqual(inquisitor["declaration"], {"model": "gpt-6-astra", "effort": "max", "sandbox": "read-only"})
        self.assertEqual(permission["status"], "unknown")

        options = probe.parse_args(["--live", "--role", "scholar", "--role", "sentinel"])
        rpc = FakeRpc(("scholar", "sentinel"))
        native, permission = probe.live_probe(
            rpc, "parent", Path("parent"), Path("child"), 1, options.live_roles
        )
        self.assertEqual(native["status"], "observed")
        self.assertEqual(native["evidence"]["requestedRoles"], ["scholar", "sentinel"])
        self.assertEqual(set(native["evidence"]["expected"]), {"scholar", "sentinel"})
        self.assertEqual(permission["status"], "unknown")

        # Two Luna spawns cannot prove Scholar was used when both children
        # report Adventurer, even if a child happens to be nicknamed Scholar.
        rpc = FakeRpc()
        rpc.metadata_roles["scholar"] = "adventurer"
        native, _ = probe.live_probe(rpc, "parent", Path("parent"), Path("child"), 1)
        self.assertEqual(native["status"], "failed")
        self.assertEqual(native["evidence"]["expected"]["scholar"]["status"], "failed")

        # A parent still using xhigh must not hide a stale child definition.
        rpc = FakeRpc(("inquisitor",))
        rpc.metadata_overrides["inquisitor"] = {"reasoningEffort": "xhigh"}
        native, _ = probe.live_probe(rpc, "parent", Path("parent"), Path("child"), 1, ("inquisitor",))
        self.assertEqual(native["status"], "failed")
        self.assertEqual(native["evidence"]["expected"]["inquisitor"]["status"], "failed")

    def test_sentinel_live_observation_requires_its_name_model_and_effort(self) -> None:
        class FakeRpc:
            def __init__(self, metadata_role="sentinel", overrides=None):
                self.messages = []
                self.metadata_role = metadata_role
                self.overrides = overrides or {}

            def call(self, method, params, timeout=20):
                if method == "turn/start":
                    return {"result": {"turn": {"id": "sentinel-turn"}}}
                if method == "thread/read":
                    thread_id = params["threadId"]
                    if thread_id == "parent":
                        return {"result": {"thread": {"model": "gpt-6-astra", "reasoningEffort": "xhigh", "cwd": "parent"}}}
                    metadata = {
                        "id": thread_id,
                        "parentThreadId": "parent",
                        "agentNickname": "sentinel",
                        "agentRole": self.metadata_role,
                        "model": "gpt-6-sol",
                        "reasoningEffort": "xhigh",
                        "cwd": "child",
                    }
                    metadata.update(self.overrides)
                    return {"result": {"thread": metadata}}
                raise AssertionError(method)

            def wait_turn(self, thread_id, timeout):
                self.messages.append({"method": "item/completed", "params": {"threadId": "parent", "item": {"type": "collabAgentToolCall", "tool": "spawnAgent", "model": "gpt-6-sol", "reasoningEffort": "xhigh", "receiverThreadIds": ["child-sentinel"], "agentsStates": {}, "status": "completed"}}})
                return "completed"

        cases = (
            ("inquisitor", {}, "role"),
            ("sentinel", {"model": "gpt-6-astra"}, "model"),
            ("sentinel", {"reasoningEffort": "max"}, "effort"),
        )
        for role, overrides, mismatch in cases:
            with self.subTest(mismatch=mismatch):
                native, _ = probe.live_probe(
                    FakeRpc(metadata_role=role, overrides=overrides),
                    "parent", Path("parent"), Path("child"), 1, ("sentinel",),
                )
                self.assertEqual(native["status"], "failed")
                self.assertEqual(native["evidence"]["expected"]["sentinel"]["status"], "failed")

    def test_config_view_captures_default_subagent_and_compaction_settings(self) -> None:
        actual = probe.config_view({"config": {
            "model": "gpt-6-astra",
            "model_context_window": 1_000_000,
            "model_auto_compact_token_limit": 900_000,
            "agents": {
                "enabled": True,
                "max_concurrent_threads_per_session": 8,
                "default_subagent_model": "gpt-6-luna",
                "default_subagent_reasoning_effort": "max",
            },
            "features": {"multi_agent": True},
        }})
        self.assertEqual(actual["autoCompactTokenLimit"], 900_000)
        self.assertEqual(actual["defaultSubagentModel"], "gpt-6-luna")
        self.assertEqual(actual["defaultSubagentReasoningEffort"], "max")
