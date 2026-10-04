"""Tests for the cgroup memory clamp in ``probe_hardware`` (no torch needed).

Every case runs over fake ``/proc/self/cgroup``, ``/proc/self/mountinfo``
and cgroup directories under ``tmp_path``. cgroup v1 is covered only here:
the container measurement behind the clamp ran on cgroup v2 (WSL2, Docker
29), with no v1 host available.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tempest_fastapi_sdk.genai.hardware as hardware_module
from tempest_fastapi_sdk.genai import ModelDtype, can_run, probe_hardware, recommend

MIB: int = 1024 * 1024
"""Bytes per MiB."""

HOST_TOTAL: int = 67_430_916_096
"""The 62 GB WSL2 host's ``MemTotal`` in bytes, from the #396 measurement."""

HOST_AVAILABLE: int = 53_000_000_000
"""A plausible host ``MemAvailable`` for the same machine."""

LIMIT_512M: int = 536_870_912
"""``memory.max`` under ``docker run --memory=512m``, as measured."""

V1_UNLIMITED_4K: int = 9_223_372_036_854_771_712
"""v1 ``memory.limit_in_bytes`` with no limit and 4 KiB pages
(``PAGE_COUNTER_MAX * PAGE_SIZE``)."""

V1_UNLIMITED_64K: int = 9_223_372_036_854_710_272
"""v1 ``memory.limit_in_bytes`` with no limit and 64 KiB pages."""


class FakeCgroup:
    """A fake process cgroup layout rooted at ``tmp_path``.

    Attributes:
        proc_cgroup (Path): The fake ``/proc/self/cgroup``.
        mountinfo (Path): The fake ``/proc/self/mountinfo``.
        mount (Path): The fake cgroup mount point.
    """

    def __init__(self, tmp_path: Path) -> None:
        """Create the empty layout.

        Args:
            tmp_path (Path): The test's temporary directory.
        """
        self.proc_cgroup: Path = tmp_path / "proc_self_cgroup"
        self.mountinfo: Path = tmp_path / "proc_self_mountinfo"
        self.mount: Path = tmp_path / "sys_fs_cgroup"
        self.mount.mkdir()

    def v2(self, cgroup_path: str = "/") -> FakeCgroup:
        """Declare a cgroup v2 membership mounted at :attr:`mount`.

        Args:
            cgroup_path (str): The process's cgroup path.

        Returns:
            FakeCgroup: ``self``, for chaining.
        """
        self.proc_cgroup.write_text(f"0::{cgroup_path}\n", encoding="ascii")
        self.mountinfo.write_text(
            "24 1 8:48 / / rw,relatime - ext4 /dev/sdd rw\n"
            f"30 24 0:26 / {self.mount} rw,nosuid,nodev shared:4 - cgroup2 "
            "cgroup2 rw,nsdelegate\n",
            encoding="ascii",
        )
        return self

    def v1(self, cgroup_path: str = "/", mount_root: str = "/") -> FakeCgroup:
        """Declare a cgroup v1 memory membership mounted at :attr:`mount`.

        Args:
            cgroup_path (str): The process's memory cgroup path.
            mount_root (str): The mount's ``root`` field in ``mountinfo``.

        Returns:
            FakeCgroup: ``self``, for chaining.
        """
        self.proc_cgroup.write_text(
            f"5:cpu,cpuacct:{cgroup_path}\n4:memory:{cgroup_path}\n"
            f"1:name=systemd:{cgroup_path}\n",
            encoding="ascii",
        )
        self.mountinfo.write_text(
            "24 1 8:48 / / rw,relatime - ext4 /dev/sdd rw\n"
            f"41 24 0:35 {mount_root} {self.mount} rw,nosuid shared:9 - cgroup "
            "cgroup rw,memory\n",
            encoding="ascii",
        )
        return self

    def write(self, relative: str, **files: str) -> Path:
        """Write cgroup files into a directory under :attr:`mount`.

        Args:
            relative (str): The directory relative to the mount (``""`` for
                the mount itself).
            **files (str): File name (dots spelled as ``__``) to contents.

        Returns:
            Path: The directory.
        """
        directory = self.mount / relative if relative else self.mount
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name.replace("__", ".")).write_text(
                content + "\n", encoding="ascii"
            )
        return directory

    def read(self) -> tuple[int, int] | None:
        """Run the reader over this layout.

        Returns:
            tuple[int, int] | None: What ``_read_cgroup_memory`` returns.
        """
        return hardware_module._read_cgroup_memory(
            str(self.proc_cgroup), str(self.mountinfo)
        )


def _stat_v2(file: int = 0, shmem: int = 0) -> str:
    """Build a v2 ``memory.stat`` body.

    Args:
        file (int): The ``file`` bytes.
        shmem (int): The ``shmem`` bytes.

    Returns:
        str: The file body.
    """
    return f"anon 286720\nfile {file}\nshmem {shmem}\ninactive_file {file}"


def _stat_v1(cache: int = 0, shmem: int = 0) -> str:
    """Build a v1 ``memory.stat`` body with local and ``total_`` keys.

    Args:
        cache (int): The ``total_cache`` bytes.
        shmem (int): The ``total_shmem`` bytes.

    Returns:
        str: The file body.
    """
    return (
        f"cache 0\nrss 0\nshmem 0\ntotal_cache {cache}\ntotal_rss 0\n"
        f"total_shmem {shmem}"
    )


class TestCgroupV2:
    def test_limit_with_page_cache_counted_as_reclaimable(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write(
            "",
            memory__max=str(LIMIT_512M),
            memory__current=str(345_178_112),
            memory__stat=_stat_v2(file=314_609_664),
        )

        assert cg.read() == (LIMIT_512M, LIMIT_512M - 345_178_112 + 314_609_664)

    def test_shared_memory_is_not_reclaimable(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write(
            "",
            memory__max=str(LIMIT_512M),
            memory__current=str(336_699_392),
            memory__stat=_stat_v2(file=314_605_568, shmem=314_572_800),
        )

        assert cg.read() == (
            LIMIT_512M,
            LIMIT_512M - 336_699_392 + 314_605_568 - 314_572_800,
        )

    def test_max_is_no_limit(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write("", memory__max="max", memory__current="1232896")

        assert cg.read() is None

    def test_nested_path_takes_the_tightest_ancestor(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2("/system.slice/app.service")
        cg.write(
            "system.slice/app.service",
            memory__max="max",
            memory__current=str(100 * MIB),
            memory__stat=_stat_v2(),
        )
        cg.write(
            "system.slice",
            memory__max=str(1024 * MIB),
            memory__current=str(300 * MIB),
            memory__stat=_stat_v2(),
        )

        assert cg.read() == (1024 * MIB, 724 * MIB)

    def test_nested_leaf_limit_below_the_parent_wins(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2("/kubepods/pod1/ctr")
        cg.write("kubepods/pod1/ctr", memory__max=str(256 * MIB), memory__current="0")
        cg.write("kubepods/pod1", memory__max=str(2048 * MIB), memory__current="0")
        cg.write("kubepods", memory__max="max")

        assert cg.read() == (256 * MIB, 256 * MIB)

    def test_usage_above_limit_clamps_available_to_zero(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write(
            "",
            memory__max=str(LIMIT_512M),
            memory__current=str(LIMIT_512M + MIB),
            memory__stat=_stat_v2(),
        )

        assert cg.read() == (LIMIT_512M, 0)


class TestCgroupV1:
    def test_limit_with_page_cache_counted_as_reclaimable(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v1()
        cg.write(
            "",
            memory__limit_in_bytes=str(LIMIT_512M),
            memory__usage_in_bytes=str(200 * MIB),
            memory__stat=_stat_v1(cache=80 * MIB, shmem=10 * MIB),
        )

        assert cg.read() == (LIMIT_512M, LIMIT_512M - 200 * MIB + 70 * MIB)

    @pytest.mark.parametrize("sentinel", [V1_UNLIMITED_4K, V1_UNLIMITED_64K])
    def test_sentinel_is_no_limit(self, tmp_path: Path, sentinel: int) -> None:
        cg = FakeCgroup(tmp_path).v1()
        cg.write(
            "",
            memory__limit_in_bytes=str(sentinel),
            memory__usage_in_bytes=str(200 * MIB),
        )

        assert cg.read() is None

    def test_sentinels_match_the_kernel_formula(self) -> None:
        long_max = 2**63 - 1
        for page_size in (4096, 65536):
            sentinel = (long_max // page_size) * page_size
            assert sentinel >= hardware_module._CGROUP_V1_UNLIMITED_MIN
        assert (long_max // 4096) * 4096 == V1_UNLIMITED_4K
        assert (long_max // 65536) * 65536 == V1_UNLIMITED_64K

    def test_mount_root_without_cgroup_namespace(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v1("/docker/abc123", mount_root="/docker/abc123")
        cg.write(
            "",
            memory__limit_in_bytes=str(LIMIT_512M),
            memory__usage_in_bytes="0",
        )

        assert cg.read() == (LIMIT_512M, LIMIT_512M)

    def test_path_outside_the_mount_root_is_not_read(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v1("/other", mount_root="/docker/abc123")
        cg.write("", memory__limit_in_bytes=str(LIMIT_512M))

        assert cg.read() is None

    def test_v1_memory_wins_on_a_hybrid_host(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path)
        unified = tmp_path / "unified"
        unified.mkdir()
        (unified / "memory.max").write_text("1048576\n", encoding="ascii")
        cg.proc_cgroup.write_text("4:memory:/\n0::/\n", encoding="ascii")
        cg.mountinfo.write_text(
            f"30 24 0:26 / {unified} rw - cgroup2 cgroup2 rw\n"
            f"41 24 0:35 / {cg.mount} rw - cgroup cgroup rw,memory\n",
            encoding="ascii",
        )
        cg.write(
            "",
            memory__limit_in_bytes=str(LIMIT_512M),
            memory__usage_in_bytes="0",
        )

        assert cg.read() == (LIMIT_512M, LIMIT_512M)


class TestMissingFiles:
    def test_no_proc_cgroup(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.proc_cgroup.unlink()

        assert cg.read() is None

    def test_no_mountinfo(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.mountinfo.unlink()

        assert cg.read() is None

    def test_no_cgroup_mount(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.mountinfo.write_text(
            "24 1 8:48 / / rw,relatime - ext4 /dev/sdd rw\n", encoding="ascii"
        )

        assert cg.read() is None

    def test_no_memory_max_file(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write("", memory__current="1000")

        assert cg.read() is None

    def test_unreadable_usage_reports_the_whole_limit(self, tmp_path: Path) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write("", memory__max=str(LIMIT_512M))

        assert cg.read() == (LIMIT_512M, LIMIT_512M)

    def test_unreadable_stat_counts_nothing_as_reclaimable(
        self, tmp_path: Path
    ) -> None:
        cg = FakeCgroup(tmp_path).v2()
        cg.write("", memory__max=str(LIMIT_512M), memory__current=str(100 * MIB))

        assert cg.read() == (LIMIT_512M, LIMIT_512M - 100 * MIB)


def _point_probe_at(monkeypatch: pytest.MonkeyPatch, cg: FakeCgroup) -> None:
    """Make ``probe_hardware`` read ``cg`` as the process's cgroup.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
        cg (FakeCgroup): The fake layout.
    """
    monkeypatch.setattr(hardware_module, "_PROC_SELF_CGROUP_PATH", str(cg.proc_cgroup))
    monkeypatch.setattr(hardware_module, "_PROC_SELF_MOUNTINFO_PATH", str(cg.mountinfo))
    monkeypatch.setitem(sys.modules, "torch", None)


def _fake_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a ``psutil`` that reports the 62 GB host.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
    """
    memory = SimpleNamespace(total=HOST_TOTAL, available=HOST_AVAILABLE)
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(virtual_memory=lambda: memory)
    )


def _fake_meminfo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide ``psutil`` and serve the 62 GB host from a fake ``/proc/meminfo``.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
        tmp_path (Path): Where to write the file.
    """
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"MemTotal: {HOST_TOTAL // 1024} kB\nMemFree: 1000 kB\n"
        f"MemAvailable: {HOST_AVAILABLE // 1024} kB\n",
        encoding="ascii",
    )
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(hardware_module, "_PROC_MEMINFO_PATH", str(meminfo))


def _container_512m(tmp_path: Path) -> FakeCgroup:
    """Lay out the cgroup measured under ``docker run --memory=512m``.

    Args:
        tmp_path (Path): The test's temporary directory.

    Returns:
        FakeCgroup: The layout.
    """
    cg = FakeCgroup(tmp_path).v2()
    cg.write(
        "",
        memory__max=str(LIMIT_512M),
        memory__current="1908736",
        memory__stat=_stat_v2(file=561_152),
    )
    return cg


class TestProbeClamp:
    @pytest.mark.parametrize("source", ["psutil", "meminfo"])
    def test_container_limit_replaces_the_host_reading(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
    ) -> None:
        if source == "psutil":
            _fake_psutil(monkeypatch)
        else:
            _fake_meminfo(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        _point_probe_at(monkeypatch, _container_512m(tmp_path))

        info = probe_hardware()

        assert info.ram_cgroup_limited is True
        assert info.ram_total_bytes == LIMIT_512M
        assert info.ram_available_bytes == LIMIT_512M - 1908736 + 561_152

    @pytest.mark.parametrize("source", ["psutil", "meminfo"])
    def test_a_2_5_gb_load_does_not_fit_512_mib_and_int4_does(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
    ) -> None:
        if source == "psutil":
            _fake_psutil(monkeypatch)
        else:
            _fake_meminfo(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        _point_probe_at(monkeypatch, _container_512m(tmp_path))

        native = can_run(num_params=500_000_000)
        recommended = recommend(num_params=500_000_000)

        assert native.fits is False
        assert native.dtype == ModelDtype.FLOAT32
        assert native.estimated_bytes == 2_500_000_000
        assert "limited by the container's cgroup to 512 MiB" in native.reason
        assert recommended.fits is True
        assert recommended.dtype == ModelDtype.INT4
        assert "limited by the container's cgroup to 512 MiB" in recommended.reason

    def test_no_limit_keeps_the_host_reading(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fake_psutil(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        cg = FakeCgroup(tmp_path).v2()
        cg.write("", memory__max="max", memory__current="1232896")
        _point_probe_at(monkeypatch, cg)

        info = probe_hardware()
        report = recommend(num_params=500_000_000)

        assert info.ram_cgroup_limited is False
        assert (info.ram_total_bytes, info.ram_available_bytes) == (
            HOST_TOTAL,
            HOST_AVAILABLE,
        )
        assert report.fits is True
        assert "cgroup" not in report.reason

    def test_limit_above_the_host_uses_the_host(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fake_psutil(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        cg = FakeCgroup(tmp_path).v2()
        cg.write(
            "",
            memory__max=str(HOST_TOTAL * 2),
            memory__current="0",
            memory__stat=_stat_v2(),
        )
        _point_probe_at(monkeypatch, cg)

        info = probe_hardware()

        assert info.ram_cgroup_limited is False
        assert info.ram_total_bytes == HOST_TOTAL
        assert info.ram_available_bytes == HOST_AVAILABLE

    def test_host_available_below_the_cgroup_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        memory = SimpleNamespace(total=HOST_TOTAL, available=100 * MIB)
        monkeypatch.setitem(
            sys.modules, "psutil", SimpleNamespace(virtual_memory=lambda: memory)
        )
        monkeypatch.setattr(sys, "platform", "linux")
        _point_probe_at(monkeypatch, _container_512m(tmp_path))

        info = probe_hardware()

        assert info.ram_cgroup_limited is True
        assert info.ram_total_bytes == LIMIT_512M
        assert info.ram_available_bytes == 100 * MIB

    def test_other_platforms_never_read_cgroups(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fake_psutil(monkeypatch)
        monkeypatch.setattr(sys, "platform", "darwin")
        _point_probe_at(monkeypatch, _container_512m(tmp_path))

        info = probe_hardware()

        assert info.ram_cgroup_limited is False
        assert info.ram_total_bytes == HOST_TOTAL
