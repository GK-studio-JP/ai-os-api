import copy
import hashlib
import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
