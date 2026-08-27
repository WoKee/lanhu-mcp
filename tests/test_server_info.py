"""Targeted protocol tests for Lanhu MCP server identity."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import lanhu_mcp_server as server
from design_cache import CACHE_SCHEMA_VERSION
from lanhu_version import __version__


class ServerInfoProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_tool_report_the_application_version(self):
        async with Client(server.mcp) as client:
            self.assertEqual(client.initialize_result.serverInfo.name, server.SERVER_NAME)
            self.assertEqual(client.initialize_result.serverInfo.version, __version__)
            self.assertEqual(server.SERVER_VERSION, __version__)

            tools = await client.list_tools()
            tool = next(item for item in tools if item.name == "lanhu_get_server_info")
            expected_fields = {
                "server_version",
                "git_commit",
                "dirty",
                "cache_schema_version",
                "features",
            }
            self.assertEqual(set(tool.outputSchema["properties"]), expected_fields)
            self.assertEqual(set(tool.outputSchema["required"]), expected_fields)
            self.assertFalse(tool.outputSchema["additionalProperties"])

            result = await client.call_tool("lanhu_get_server_info", {})
            self.assertFalse(result.is_error)
            payload = result.structured_content
            self.assertEqual(set(payload), expected_fields)
            self.assertEqual(payload["server_version"], __version__)
            self.assertEqual(payload["cache_schema_version"], CACHE_SCHEMA_VERSION)
            self.assertEqual(payload["features"], list(server.SERVER_FEATURES))
            self.assertEqual(len(payload["features"]), len(set(payload["features"])))
            self.assertTrue(all(isinstance(item, str) for item in payload["features"]))
            if payload["git_commit"] is not None:
                self.assertRegex(payload["git_commit"], r"^[0-9a-f]{7,64}$")
            self.assertIn(payload["dirty"], {True, False, None})

            serialized = json.dumps(payload, ensure_ascii=True).lower()
            self.assertNotIn("lanhu_cookie", serialized)
            self.assertNotIn("authorization", serialized)
            self.assertNotIn("design_cache_scope", serialized)
            self.assertNotIn(str(PROJECT_ROOT).lower(), serialized)

    def test_git_commit_prefers_valid_environment_override(self):
        with patch.dict(os.environ, {"LANHU_GIT_COMMIT": "ABCDEF1234567"}):
            with patch.object(server.subprocess, "run") as run:
                self.assertEqual(server._resolve_git_commit(), "abcdef1234567")
        run.assert_not_called()

    def test_invalid_commit_override_does_not_escape_validation(self):
        root_result = server.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{PROJECT_ROOT}\n",
            stderr="ignored-sensitive-error",
        )
        commit_result = server.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="0123456789abcdef\n",
            stderr="ignored-sensitive-error",
        )
        with patch.dict(os.environ, {"LANHU_GIT_COMMIT": "not-a-commit"}):
            with patch.object(
                server.subprocess,
                "run",
                side_effect=[root_result, commit_result],
            ):
                self.assertEqual(server._resolve_git_commit(), "0123456789abcdef")

    def test_git_metadata_is_unknown_when_git_is_unavailable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANHU_GIT_COMMIT", None)
            with patch.object(server.subprocess, "run", side_effect=OSError("missing")):
                self.assertIsNone(server._resolve_git_commit())
                self.assertIsNone(server._resolve_git_dirty())

    def test_git_metadata_does_not_probe_an_enclosing_host_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            host_root = Path(temp_dir)
            (host_root / ".git").mkdir()
            site_packages = host_root / ".venv" / "Lib" / "site-packages"
            site_packages.mkdir(parents=True)
            installed_module = site_packages / "lanhu_mcp_server.py"

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LANHU_GIT_COMMIT", None)
                with patch.object(server, "__file__", str(installed_module)):
                    with patch.object(server.subprocess, "run") as run:
                        self.assertIsNone(server._resolve_git_commit())
                        self.assertIsNone(server._resolve_git_dirty())

            run.assert_not_called()

    def test_git_metadata_rejects_a_mismatched_checkout_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "package"
            source_dir.mkdir()
            (source_dir / ".git").touch()
            installed_module = source_dir / "lanhu_mcp_server.py"
            root_result = server.subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{Path(temp_dir)}\n",
                stderr="ignored-sensitive-error",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LANHU_GIT_COMMIT", None)
                with patch.object(server, "__file__", str(installed_module)):
                    with patch.object(
                        server.subprocess,
                        "run",
                        return_value=root_result,
                    ) as run:
                        self.assertIsNone(server._resolve_git_commit())

            run.assert_called_once()

    def test_git_metadata_rejects_an_empty_checkout_root(self):
        empty_root_result = server.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="ignored-sensitive-error",
        )
        with patch.object(
            server.subprocess,
            "run",
            return_value=empty_root_result,
        ) as run:
            self.assertIsNone(server._resolve_source_checkout_root())
        run.assert_called_once()


class VersionMetadataTests(unittest.TestCase):
    def test_console_entrypoint_runs_stdio_transport(self):
        with patch.object(server.mcp, "run") as run:
            self.assertIsNone(server.main())
        run.assert_called_once_with(transport="stdio")

    def test_pyproject_reads_the_same_dynamic_version(self):
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('version = {attr = "lanhu_version.__version__"}', pyproject)
        self.assertIn('"design_cache"', pyproject)
        self.assertIn('"lanhu_version"', pyproject)
        self.assertIn('"fastmcp>=2.0.0"', pyproject)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
