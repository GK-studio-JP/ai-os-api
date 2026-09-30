import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import orchestrator


class MemoryIntegrationTests(unittest.TestCase):
    def test_attach_global_memory_rehashes_invocation(self):
        invocation = {
            "schema": "ai-os-worker-invocation:v1",
            "input": {"dispatch": {"task": "#1"}, "capsule": {"schema": "ai-os-context-capsule:v1"}},
            "instructions": [],
            "fingerprint": "sha256:old",
        }
        memory = {
            "schema": "ai-os-memory-search:v1",
            "available": True,
            "count": 1,
            "results": [{"chunk_id": "tool.vercel#001", "score": 0.9}],
        }

        updated = orchestrator._attach_global_memory(invocation, memory)

        self.assertNotEqual(updated["fingerprint"], "sha256:old")
        self.assertIn("global_memory", updated["input"])
        self.assertNotIn("global_memory", invocation["input"])

        material = copy.deepcopy(updated)
        fingerprint = material.pop("fingerprint")
        expected = "sha256:" + hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(fingerprint, expected)

    def test_unavailable_memory_keeps_invocation_unchanged(self):
        invocation = {
            "schema": "ai-os-worker-invocation:v1",
            "input": {},
            "instructions": [],
            "fingerprint": "sha256:original",
        }
        result = orchestrator._attach_global_memory(
            invocation,
            {"available": False, "results": []},
        )
        self.assertIs(result, invocation)

    def test_empty_memory_keeps_invocation_unchanged(self):
        invocation = {
            "schema": "ai-os-worker-invocation:v1",
            "input": {},
            "instructions": [],
            "fingerprint": "sha256:original",
        }
        result = orchestrator._attach_global_memory(
            invocation,
            {"available": True, "results": []},
        )
        self.assertIs(result, invocation)


class TargetRepositoryTests(unittest.TestCase):
    def _capture_project_task(self, payload):
        captured = {}

        def fake_service_post(name, path, service_payload):
            if name == "projects" and path == "/api/projects/task":
                captured.update(service_payload)
                raise RuntimeError("stop after project task request")
            raise AssertionError(f"unexpected service call: {name} {path}")

        with patch.object(orchestrator, "_service_post", side_effect=fake_service_post):
            with self.assertRaisesRegex(RuntimeError, "stop after project task request"):
                orchestrator.start_run(payload)
        return captured

    def test_start_run_forwards_target_repository(self):
        captured = self._capture_project_task(
            {
                "project_id": "aios-v1-hardening",
                "objective": "Propagate target repository",
                "worker_id": "worker:test",
                "target_repository": "GK-studio-JP/ai-os-api",
            }
        )
        self.assertEqual(captured["target_repository"], "GK-studio-JP/ai-os-api")

    def test_start_run_omits_target_repository_when_unspecified(self):
        captured = self._capture_project_task(
            {
                "project_id": "aios-v1-hardening",
                "objective": "Use canonical repository",
                "worker_id": "worker:test",
            }
        )
        self.assertNotIn("target_repository", captured)


if __name__ == "__main__":
    unittest.main()
