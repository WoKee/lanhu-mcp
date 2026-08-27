"""Targeted tests for the persistent UI design cache."""

from __future__ import annotations

import asyncio
import copy
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import lanhu_mcp_server as server
from design_cache import DesignCache


TEAM_ID = "team-1"
PROJECT_ID = "project-1"
IMAGE_ID = "image-1"


class FakeResponse:
    def __init__(
        self,
        *,
        json_data=None,
        content: bytes = b"",
        status_code: int = 200,
        headers=None,
    ):
        self._json_data = json_data
        self.content = content
        self.status_code = status_code
        self.headers = dict(headers or {})

    def json(self):
        return copy.deepcopy(self._json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeLanhuBackend:
    def __init__(self, version_id: str = "version-1", delay_seconds: float = 0.03):
        self.version_id = version_id
        self.delay_seconds = delay_seconds
        self.metadata_generation = 1
        self.info_requests = 0
        self.json_requests = 0
        self.screenshot_requests = 0
        self.fail_requests = False
        self.screenshot_content = b"\x89PNG\r\n\x1a\nvalid-test-image"
        self.screenshot_content_type = "image/png"

    def _latest_version(self) -> dict:
        return {
            "id": self.version_id,
            "version_info": self.version_id,
            "json_url": (
                f"https://example.test/metadata-{self.metadata_generation}/"
                f"{self.version_id}.json"
            ),
            "url": (
                f"https://example.test/metadata-{self.metadata_generation}/"
                f"{self.version_id}.png"
            ),
        }

    async def get(self, url: str, params=None):
        if self.fail_requests:
            raise AssertionError(f"Unexpected network request: {url}")

        if url.endswith("/api/project/image"):
            self.info_requests += 1
            await asyncio.sleep(self.delay_seconds)
            latest_version = self._latest_version()
            return FakeResponse(
                json_data={
                    "code": "00000",
                    "result": {
                        "id": IMAGE_ID,
                        "name": "Test design",
                        "width": 750,
                        "height": 1334,
                        "url": latest_version["url"],
                        "versions": [latest_version],
                    },
                }
            )

        if url.endswith(".json"):
            self.json_requests += 1
            await asyncio.sleep(self.delay_seconds)
            return FakeResponse(
                json_data={
                    "version": self.version_id,
                    "artboard": {"frame": {"width": 750, "height": 1334}},
                    "layers": [],
                }
            )

        if url.endswith(".png"):
            self.screenshot_requests += 1
            await asyncio.sleep(self.delay_seconds)
            return FakeResponse(
                content=self.screenshot_content,
                headers={"Content-Type": self.screenshot_content_type},
            )

        raise AssertionError(f"Unexpected URL: {url}")

    async def aclose(self):
        return None


def make_extractor(backend: FakeLanhuBackend) -> server.LanhuExtractor:
    extractor = server.LanhuExtractor.__new__(server.LanhuExtractor)
    extractor.client = backend
    return extractor


class DesignPayloadCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_root = Path(self.temp_dir.name) / "cache"
        self.cache = DesignCache(self.cache_root, lock_timeout_seconds=5)
        self.cache_patch = patch.object(server, "DESIGN_CACHE", self.cache)
        self.ttl_patch = patch.object(server, "DESIGN_CACHE_TTL_SECONDS", 300.0)
        self.scope_patch = patch.object(
            server,
            "DESIGN_CACHE_SCOPE",
            "credential-scope-default",
            create=True,
        )
        self.cache_patch.start()
        self.ttl_patch.start()
        self.scope_patch.start()

    def tearDown(self):
        self.scope_patch.stop()
        self.ttl_patch.stop()
        self.cache_patch.stop()
        self.temp_dir.cleanup()

    async def _get_payload(self, backend: FakeLanhuBackend, cache_policy: str = "use"):
        return await make_extractor(backend).get_design_payload_cached(
            IMAGE_ID,
            TEAM_ID,
            PROJECT_ID,
            cache_policy=cache_policy,
        )

    async def test_concurrent_cold_requests_are_coalesced(self):
        backend = FakeLanhuBackend(delay_seconds=0.08)

        first, second = await asyncio.gather(
            self._get_payload(backend),
            self._get_payload(backend),
        )

        self.assertEqual(backend.info_requests, 1)
        self.assertEqual(backend.json_requests, 1)
        self.assertEqual(first["latest_version"]["id"], "version-1")
        self.assertEqual(second["latest_version"]["id"], "version-1")
        cache_sections = [first["_cache"], second["_cache"]]
        self.assertTrue(
            any(
                section["design_info"]["waited_for_inflight"]
                or section["design_payload"]["waited_for_inflight"]
                for section in cache_sections
            )
        )

    async def test_disk_cache_is_shared_by_new_cache_instance(self):
        backend = FakeLanhuBackend()
        await self._get_payload(backend)
        self.assertEqual((backend.info_requests, backend.json_requests), (1, 1))

        replacement_cache = DesignCache(self.cache_root, lock_timeout_seconds=5)
        backend.fail_requests = True
        with patch.object(server, "DESIGN_CACHE", replacement_cache):
            cached = await self._get_payload(backend)

        self.assertEqual(cached["_cache"]["design_info"]["state"], "hit")
        self.assertEqual(cached["_cache"]["design_payload"]["state"], "hit")

    async def test_refresh_same_version_only_checks_metadata(self):
        backend = FakeLanhuBackend()
        await self._get_payload(backend)

        refreshed = await self._get_payload(backend, cache_policy="refresh")

        self.assertEqual(backend.info_requests, 2)
        self.assertEqual(backend.json_requests, 1)
        self.assertEqual(refreshed["_cache"]["design_info"]["state"], "revalidated")
        self.assertEqual(refreshed["_cache"]["design_payload"]["state"], "hit")

    async def test_refresh_same_version_updates_metadata_urls_without_redownloading_payload(self):
        backend = FakeLanhuBackend()
        initial = await self._get_payload(backend)
        initial_json_url = initial["latest_version"]["json_url"]
        initial_screenshot_url = initial["latest_version"]["url"]
        backend.metadata_generation = 2

        refreshed = await self._get_payload(backend, cache_policy="refresh")

        self.assertEqual(backend.info_requests, 2)
        self.assertEqual(backend.json_requests, 1)
        self.assertNotEqual(refreshed["latest_version"]["json_url"], initial_json_url)
        self.assertNotEqual(refreshed["latest_version"]["url"], initial_screenshot_url)
        self.assertIn("/metadata-2/", refreshed["latest_version"]["json_url"])
        self.assertIn("/metadata-2/", refreshed["latest_version"]["url"])

        identity = make_extractor(backend)._design_cache_identity(
            IMAGE_ID,
            TEAM_ID,
            PROJECT_ID,
        )
        _, cached_info = self.cache.load_json("design_info", identity)
        self.assertEqual(
            cached_info["latest_version"]["json_url"],
            refreshed["latest_version"]["json_url"],
        )

    async def test_concurrent_refreshes_only_check_remote_version_once(self):
        backend = FakeLanhuBackend(delay_seconds=0.08)
        await self._get_payload(backend)

        first, second = await asyncio.gather(
            self._get_payload(backend, cache_policy="refresh"),
            self._get_payload(backend, cache_policy="refresh"),
        )

        self.assertEqual(backend.info_requests, 2)
        self.assertEqual(backend.json_requests, 1)
        self.assertEqual(
            {first["_cache"]["design_info"]["state"], second["_cache"]["design_info"]["state"]},
            {"hit", "revalidated"},
        )
        self.assertTrue(
            first["_cache"]["design_info"]["waited_for_inflight"]
            or second["_cache"]["design_info"]["waited_for_inflight"]
        )

    async def test_cache_identity_is_isolated_by_credential_scope(self):
        backend = FakeLanhuBackend()

        with patch.object(server, "DESIGN_CACHE_SCOPE", "credential-scope-a"):
            first = await self._get_payload(backend)
            first_identity = make_extractor(backend)._design_cache_identity(
                IMAGE_ID,
                TEAM_ID,
                PROJECT_ID,
            )

        with patch.object(server, "DESIGN_CACHE_SCOPE", "credential-scope-b"):
            second = await self._get_payload(backend)
            second_identity = make_extractor(backend)._design_cache_identity(
                IMAGE_ID,
                TEAM_ID,
                PROJECT_ID,
            )

        self.assertEqual(first_identity["cache_scope"], "credential-scope-a")
        self.assertEqual(second_identity["cache_scope"], "credential-scope-b")
        self.assertNotEqual(first_identity, second_identity)
        self.assertNotEqual(
            self.cache.make_key("design_info", first_identity),
            self.cache.make_key("design_info", second_identity),
        )
        self.assertEqual(backend.info_requests, 2)
        self.assertEqual(backend.json_requests, 2)
        self.assertEqual(first["_cache"]["design_info"]["state"], "miss")
        self.assertEqual(second["_cache"]["design_info"]["state"], "miss")

    async def test_refresh_changed_version_downloads_new_payload(self):
        backend = FakeLanhuBackend()
        await self._get_payload(backend)
        backend.version_id = "version-2"

        refreshed = await self._get_payload(backend, cache_policy="refresh")

        self.assertEqual(backend.info_requests, 2)
        self.assertEqual(backend.json_requests, 2)
        self.assertEqual(refreshed["latest_version"]["id"], "version-2")
        self.assertEqual(refreshed["_cache"]["design_info"]["state"], "updated")
        self.assertEqual(
            refreshed["_cache"]["design_info"]["previous_version_id"],
            "version-1",
        )

    async def test_corrupt_payload_is_refetched_atomically(self):
        backend = FakeLanhuBackend()
        await self._get_payload(backend)
        identity = {
            **make_extractor(backend)._design_cache_identity(
                IMAGE_ID,
                TEAM_ID,
                PROJECT_ID,
            ),
            "version_id": "version-1",
        }
        record = self.cache.get_record("design_payload", identity)
        self.assertIsNotNone(record)
        record.payload_path.write_text("{broken", encoding="utf-8")

        recovered = await self._get_payload(backend)

        self.assertEqual(backend.info_requests, 1)
        self.assertEqual(backend.json_requests, 2)
        self.assertEqual(recovered["sketch_data"]["version"], "version-1")

    async def test_skill_chain_warms_screenshot_and_payload_once(self):
        backend = FakeLanhuBackend()
        extractor = make_extractor(backend)
        params = {"team_id": TEAM_ID, "project_id": PROJECT_ID}
        design = {"id": IMAGE_ID, "image_id": IMAGE_ID}
        screenshots_dir = Path(self.temp_dir.name) / "screenshots"

        first_screenshot = await server._get_design_screenshot_cached(
            extractor,
            design,
            params,
            screenshots_dir,
        )
        first_payload = await extractor.get_design_payload_cached(
            IMAGE_ID,
            TEAM_ID,
            PROJECT_ID,
        )
        second_payload = await extractor.get_design_payload_cached(
            IMAGE_ID,
            TEAM_ID,
            PROJECT_ID,
        )
        second_screenshot = await server._get_design_screenshot_cached(
            extractor,
            design,
            params,
            screenshots_dir,
        )

        self.assertEqual(backend.info_requests, 1)
        self.assertEqual(backend.json_requests, 1)
        self.assertEqual(backend.screenshot_requests, 1)
        self.assertEqual(first_screenshot["cache"]["screenshot"]["state"], "miss")
        self.assertEqual(first_payload["_cache"]["design_payload"]["state"], "miss")
        self.assertEqual(second_payload["_cache"]["design_payload"]["state"], "hit")
        self.assertEqual(second_screenshot["cache"]["screenshot"]["state"], "hit")

    async def test_non_image_screenshot_is_not_written_or_cached(self):
        backend = FakeLanhuBackend()
        backend.screenshot_content = b"<html><body>not an image</body></html>"
        backend.screenshot_content_type = "text/html; charset=utf-8"
        extractor = make_extractor(backend)
        params = {"team_id": TEAM_ID, "project_id": PROJECT_ID}
        design = {"id": IMAGE_ID, "image_id": IMAGE_ID}
        screenshots_dir = Path(self.temp_dir.name) / "invalid-screenshots"

        with self.assertRaisesRegex(Exception, "(?i)(image|screenshot)"):
            await server._get_design_screenshot_cached(
                extractor,
                design,
                params,
                screenshots_dir,
            )

        identity = {
            **extractor._design_cache_identity(IMAGE_ID, TEAM_ID, PROJECT_ID),
            "version_id": "version-1",
        }
        self.assertIsNone(self.cache.get_record("design_screenshot", identity))
        self.assertEqual(list(screenshots_dir.glob("*")), [])

    async def test_corrupt_nonempty_screenshot_is_refetched_and_repaired(self):
        backend = FakeLanhuBackend()
        extractor = make_extractor(backend)
        params = {"team_id": TEAM_ID, "project_id": PROJECT_ID}
        design = {"id": IMAGE_ID, "image_id": IMAGE_ID}
        screenshots_dir = Path(self.temp_dir.name) / "corrupt-screenshots"

        first = await server._get_design_screenshot_cached(
            extractor,
            design,
            params,
            screenshots_dir,
        )
        screenshot_path = Path(first["screenshot_path"])
        screenshot_path.write_bytes(b"<html>cached error</html>")

        repaired = await server._get_design_screenshot_cached(
            extractor,
            design,
            params,
            screenshots_dir,
        )

        self.assertEqual(backend.screenshot_requests, 2)
        self.assertEqual(repaired["cache"]["screenshot"]["state"], "miss")
        self.assertEqual(repaired["cache"]["screenshot"]["source"], "network")
        self.assertTrue(
            server._has_supported_image_magic(screenshot_path.read_bytes()[:12])
        )

    async def test_detail_url_uses_image_id_fast_path(self):
        extractor = server.LanhuExtractor.__new__(server.LanhuExtractor)
        url = (
            "https://lanhuapp.com/web/#/item/project/detailDetach"
            f"?pid={PROJECT_ID}&image_id={IMAGE_ID}&tid={TEAM_ID}"
        )

        with patch.object(
            server,
            "_get_designs_internal",
            side_effect=AssertionError("project index must not be requested"),
        ):
            resolved = await server._resolve_target_designs(extractor, url, IMAGE_ID)

        self.assertEqual(resolved["status"], "success")
        self.assertTrue(resolved["target_designs"][0]["detail_direct"])


class DesignCachePathTests(unittest.TestCase):
    def test_content_addressed_path_stays_within_windows_legacy_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            padding_length = max(1, 130 - len(str(base_path)) - 1)
            cache_root = base_path / ("x" * padding_length)
            cache = DesignCache(cache_root, lock_timeout_seconds=5)
            identity = {
                "cache_scope": "scope",
                "team_id": TEAM_ID,
                "project_id": PROJECT_ID,
                "image_id": IMAGE_ID,
                "version_id": "version-1",
            }
            payload = {"content": "value"}

            record = cache.store_json(
                "design_payload",
                identity,
                payload,
                version_id="version-1",
                metadata={"image_id": IMAGE_ID},
            )
            loaded_record, loaded_payload = cache.load_json(
                "design_payload",
                identity,
            )

            self.assertLess(len(str(record.payload_path)), 260)
            self.assertEqual(loaded_record, record)
            self.assertEqual(loaded_payload, payload)

    def test_screenshot_filename_is_bounded_and_version_unique(self):
        design_name = "very-long-design-name-" * 30
        image_id = "image-id-" * 30
        first_version = "version-one-" * 30
        second_version = "version-two-" * 30

        first = server._safe_versioned_design_filename(
            design_name,
            image_id,
            first_version,
        )
        repeated = server._safe_versioned_design_filename(
            design_name,
            image_id,
            first_version,
        )
        second = server._safe_versioned_design_filename(
            design_name,
            image_id,
            second_version,
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 102)
        self.assertLessEqual(len(first.encode("utf-16-le")) // 2, 255)

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            padding_length = max(1, 145 - len(str(base_path)) - 1)
            output_dir = base_path / ("x" * padding_length)
            screenshot_path = output_dir / first
            DesignCache.atomic_write_bytes(screenshot_path, b"screenshot")
            self.assertLess(len(str(screenshot_path)), 260)
            self.assertEqual(screenshot_path.read_bytes(), b"screenshot")


class DesignCacheAtomicWriteTests(unittest.TestCase):
    def test_identical_content_reuses_existing_object_without_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = DesignCache(Path(temp_dir) / "cache", lock_timeout_seconds=5)
            identity = {"image_id": IMAGE_ID, "version_id": "version-1"}
            first = cache.store_json(
                "design_payload",
                identity,
                {"content": "value"},
                version_id="version-1",
            )

            with first.payload_path.open("rb"):
                with patch.object(
                    os,
                    "replace",
                    side_effect=AssertionError("identical object must not be replaced"),
                ):
                    second = cache.store_json(
                        "design_payload",
                        identity,
                        {"content": "value"},
                        version_id="version-1",
                    )

            self.assertEqual(second.payload_path, first.payload_path)
            self.assertEqual(second.payload_path.read_text(encoding="utf-8"), '{"content":"value"}')

    def test_atomic_replace_retries_windows_sharing_violation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "payload.json"
            target_path.write_bytes(b"old")
            real_replace = os.replace
            attempts = 0

            def replace_after_sharing_violation(source, target):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    error = PermissionError("file is being used")
                    error.winerror = 32
                    raise error
                real_replace(source, target)

            with patch.object(os, "replace", side_effect=replace_after_sharing_violation):
                DesignCache.atomic_write_bytes(target_path, b"new")

            self.assertEqual(attempts, 2)
            self.assertEqual(target_path.read_bytes(), b"new")


def _cross_process_lock_worker(cache_root: str, start_event, result_queue):
    async def run():
        cache = DesignCache(Path(cache_root), lock_timeout_seconds=5)
        start_event.wait(5)
        async with cache.lock("cross_process", {"image_id": IMAGE_ID}) as stats:
            acquired_at = time.monotonic()
            await asyncio.sleep(0.2)
            released_at = time.monotonic()
            result_queue.put(
                (acquired_at, released_at, stats.waited_for_inflight)
            )

    asyncio.run(run())


class CrossProcessLockTests(unittest.TestCase):
    def test_same_key_is_serialized_across_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = multiprocessing.get_context("spawn")
            start_event = context.Event()
            result_queue = context.Queue()
            processes = [
                context.Process(
                    target=_cross_process_lock_worker,
                    args=(temp_dir, start_event, result_queue),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [result_queue.get(timeout=8) for _ in processes]
            for process in processes:
                process.join(timeout=8)
                self.assertEqual(process.exitcode, 0)

        first, second = sorted(results, key=lambda item: item[0])
        self.assertGreaterEqual(second[0], first[1] - 0.02)
        self.assertTrue(first[2] or second[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
