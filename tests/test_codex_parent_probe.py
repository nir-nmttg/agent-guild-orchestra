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
        self.assertEqual(permission["reason"], "no live child thread was observed")

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
                            "model": "gpt-5.6-luna",
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
            def __init__(self) -> None:
                self.messages = []
                self.current_effort = None
                self.spawn_count = 0

            def call(self, method, params, timeout=20):
                if method == "turn/start":
                    self.current_effort = params["effort"]
                    return {"result": {"turn": {"id": f"turn-{self.current_effort}"}}}
                if method == "thread/read":
                    thread_id = params["threadId"]
                    if thread_id == "parent":
                        return {"result": {"thread": {"model": "gpt-6-astra", "reasoningEffort": self.current_effort, "cwd": "parent"}}}
                    agent = "adventurer" if thread_id == "child-adventurer" else "inquisitor"
                    want = probe.EXPECTED[agent]
                    return {"result": {"thread": {"id": thread_id, "parentThreadId": "parent", "agentNickname": agent, "agentRole": agent, "model": want["model"], "reasoningEffort": want["effort"], "cwd": "child"}}}
                raise AssertionError(method)

            def wait_turn(self, thread_id, timeout):
                agent = "adventurer" if self.spawn_count == 0 else "inquisitor"
                want = probe.EXPECTED[agent]
                child_id = f"child-{agent}"
                self.messages.append({"method": "item/completed", "params": {"threadId": "parent", "item": {"type": "collabAgentToolCall", "tool": "spawnAgent", "model": want["model"], "reasoningEffort": want["effort"], "receiverThreadIds": [child_id], "agentsStates": {}, "status": "completed"}}})
                self.spawn_count += 1
                return "completed"

        native, permission = probe.live_probe(FakeRpc(), "parent", Path("parent"), Path("child"), 1)
        self.assertEqual(native["status"], "observed")
        self.assertEqual([item["actual"]["reasoningEffort"] for item in native["evidence"]["rootThreadReads"]], ["low", "xhigh"])
        self.assertEqual(permission["status"], "unknown")
