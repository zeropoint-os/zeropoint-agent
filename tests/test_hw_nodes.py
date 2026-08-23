"""Hardware-provisioning node behaviour: Disk -> Partition -> Format ->
Mount -> MountDir.

Plain asserts and a __main__ runner so this works today with no test
dependency, and drops into pytest unchanged once one is added.

    python3 tests/test_hw_nodes.py

No real block device is ever touched: the MOCK chain runs through the DAG
in mock mode, and the LIVE paths run against a fake `_ops` that simulates
sfdisk/mkfs/mount against an in-memory "system".
"""

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zeropoint_agent.dag import DAG  # noqa: E402
from zeropoint_agent.inode import ResolveMode, NodeStatus  # noqa: E402
from zeropoint_agent.nodes.hw import _ops as ops_mod  # noqa: E402
from zeropoint_agent.nodes.hw.disk import Disk, DiskResult  # noqa: E402
from zeropoint_agent.nodes.hw.partition import Partition, PartitionResult  # noqa: E402
from zeropoint_agent.nodes.hw.format import Format, FormatResult  # noqa: E402
from zeropoint_agent.nodes.hw.mount import Mount, MountResult  # noqa: E402
from zeropoint_agent.nodes.hw.mountdir import MountDir  # noqa: E402
from zeropoint_agent.nodes._systemd import AGENT_BIN  # noqa: E402


# --- fakes ---------------------------------------------------------------

class FakeSystem:
    """An in-memory stand-in for the block layer the hw nodes drive."""

    def __init__(self):
        self.partitions = {}        # stable_id -> device_path
        self.fs = {}                # device -> filesystem type
        self.uuids = {}             # device -> uuid
        self.mounts = set()         # mounted mountpoints
        self.fstab = []             # (uuid, mountpoint, fstype, options)
        self.appear_on_settle = []  # [(stable_id, device)] created but not yet visible
        self.log = []               # ordered record of destructive calls

    # probes
    def device_for(self, stable_id):
        return self.partitions.get(stable_id)

    def fs_type(self, device):
        return self.fs.get(device)

    def fs_uuid(self, device):
        return self.uuids.get(device)

    def is_mounted(self, mountpoint):
        return mountpoint in self.mounts

    # destructive
    def create_partition(self, disk_device, size_mb, part_type, label=None):
        self.log.append(("sfdisk", disk_device, size_mb, part_type))
        return ops_mod.RunResult(0, "", "")

    def settle(self):
        # udev catches up: queued partitions become visible.
        for sid, dev in self.appear_on_settle:
            self.partitions[sid] = dev
        self.appear_on_settle = []

    def make_filesystem(self, device, filesystem, label=None):
        self.log.append(("mkfs", device, filesystem))
        self.fs[device] = filesystem
        self.uuids[device] = f"uuid-{filesystem}"
        return ops_mod.RunResult(0, "", "")

    def mount(self, device, mountpoint, options="defaults"):
        self.log.append(("mount", device, mountpoint))
        self.mounts.add(mountpoint)
        return ops_mod.RunResult(0, "", "")

    def umount(self, mountpoint):
        self.log.append(("umount", mountpoint))
        self.mounts.discard(mountpoint)
        return ops_mod.RunResult(0, "", "")

    def ensure_fstab(self, uuid, mountpoint, fstype, options="defaults"):
        self.log.append(("fstab+", mountpoint))
        self.fstab = [e for e in self.fstab if e[1] != mountpoint]
        self.fstab.append((uuid, mountpoint, fstype, options))

    def remove_fstab(self, mountpoint):
        self.log.append(("fstab-", mountpoint))
        self.fstab = [e for e in self.fstab if e[1] != mountpoint]


_PATCHED = [
    "device_for", "fs_type", "fs_uuid", "is_mounted", "create_partition",
    "settle", "make_filesystem", "mount", "umount", "ensure_fstab",
    "remove_fstab",
]


@contextmanager
def fake_ops(system):
    saved = {name: getattr(ops_mod, name) for name in _PATCHED}
    for name in _PATCHED:
        setattr(ops_mod, name, getattr(system, name))
    try:
        yield system
    finally:
        for name, fn in saved.items():
            setattr(ops_mod, name, fn)


class _Entry:
    def __init__(self, output):
        self.output = output


class _FakeDag:
    """Duck-types the surface own_output() touches: .nodes[id].output."""

    def __init__(self, node_id, output):
        self.nodes = {node_id: _Entry(output)}


def _with_own_output(node, node_id, output):
    node.id = node_id
    node.dag = _FakeDag(node_id, output)
    return node


# --- MOCK chain ----------------------------------------------------------

def test_mock_chain_resolves_and_propagates():
    """The whole stack converges in mock mode with typed edges intact."""
    dag = DAG(store=None)
    dag.add("disk", Disk(stable_id="disk1"))
    dag.add("part", Partition(number=1, size_mb=100), parents=["disk"])
    dag.add("fmt", Format(filesystem="ext4"), parents=["part"])
    dag.add("mnt", Mount(mountpoint="/mnt/data"), parents=["fmt"])
    dag.add("dir", MountDir(path="/mnt/data/sub"), parents=["mnt"])

    results = dag.resolve(mode=ResolveMode.MOCK)
    assert all(s == NodeStatus.SUCCESS for s in results.values()), results

    part_out = dag.get("part").output
    assert part_out.stable_id == "disk1-part1", part_out
    assert part_out.device_path == "/dev/disk/by-id/disk1-part1", part_out
    fmt_out = dag.get("fmt").output
    assert fmt_out.uuid == "mock-uuid-1234", fmt_out


def test_mock_does_not_touch_ops():
    """Mock mode must never call into the block layer."""
    system = FakeSystem()
    with fake_ops(system):
        Partition(number=1, size_mb=100).resolve(
            DiskResult(stable_id="d", device_path="/dev/sda"), ResolveMode.MOCK)
        Format(filesystem="ext4").resolve(
            PartitionResult(number=1, size_mb=1, device_path="/dev/sda1"),
            ResolveMode.MOCK)
        Mount(mountpoint="/mnt/x").resolve(
            FormatResult(device_path="/dev/sda1", filesystem="ext4"),
            ResolveMode.MOCK)
    assert system.log == [], system.log


# --- LIVE partition ------------------------------------------------------

def test_partition_live_creates_and_settles():
    system = FakeSystem()
    system.appear_on_settle = [("disk1-part1", "/dev/sdz1")]
    with fake_ops(system):
        r = Partition(number=1, size_mb=100).resolve(
            DiskResult(stable_id="disk1", device_path="/dev/sdz"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert r.output.device_path == "/dev/sdz1", r.output
    assert ("sfdisk", "/dev/sdz", 100, "83") in system.log, system.log


def test_partition_live_is_idempotent():
    """An already-present partition is not repartitioned."""
    system = FakeSystem()
    system.partitions["disk1-part1"] = "/dev/sdz1"
    with fake_ops(system):
        r = Partition(number=1, size_mb=100).resolve(
            DiskResult(stable_id="disk1", device_path="/dev/sdz"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert r.output.device_path == "/dev/sdz1", r.output
    assert system.log == [], system.log  # no sfdisk


def test_partition_live_defers_to_reboot_when_device_absent():
    """Table written but the device node never appeared -> reboot re-entry."""
    system = FakeSystem()  # nothing queued to appear
    with fake_ops(system):
        r = Partition(number=1, size_mb=100).resolve(
            DiskResult(stable_id="disk1", device_path="/dev/sdz"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.PENDING_REBOOT, r
    assert len(r.systemd_units) == 1, r.systemd_units
    unit = r.systemd_units[0]
    assert unit.exec_start.endswith("verify-node part-1"), unit.exec_start
    assert "TODO" not in unit.exec_start


def test_partition_live_fails_without_disk():
    system = FakeSystem()
    with fake_ops(system):
        r = Partition(number=1, size_mb=100).resolve(
            DiskResult(stable_id="disk1", device_path=""), ResolveMode.LIVE)
    assert r.status == NodeStatus.ERROR, r


def test_partition_verify_uses_own_output():
    system = FakeSystem()
    system.partitions["disk1-part1"] = "/dev/sdz1"
    with fake_ops(system):
        node = _with_own_output(
            Partition(number=1, size_mb=100), "part",
            PartitionResult(number=1, size_mb=100, stable_id="disk1-part1"))
        ok = node.verify(ResolveMode.LIVE)
        assert ok.status == NodeStatus.SUCCESS, ok
        assert ok.output.device_path == "/dev/sdz1", ok.output

        # And when the device is gone, verify reports not-converged.
        system.partitions.clear()
        missing = node.verify(ResolveMode.LIVE)
        assert missing.status == NodeStatus.PENDING_REBOOT, missing


def test_partition_remove_wipes_the_real_device():
    """remove derives the real device — no literal TODO in the unit."""
    system = FakeSystem()
    system.partitions["disk1-part1"] = "/dev/sdz1"
    with fake_ops(system):
        node = _with_own_output(
            Partition(number=1, size_mb=100), "part",
            PartitionResult(number=1, size_mb=100, stable_id="disk1-part1",
                            device_path="/dev/sdz1"))
        r = node.remove(ResolveMode.LIVE)
    assert r.status == NodeStatus.PENDING_REBOOT, r
    exec_start = r.systemd_units[0].exec_start
    assert exec_start == "wipefs -a /dev/sdz1", exec_start
    assert "TODO" not in exec_start


def test_partition_remove_noop_when_already_gone():
    system = FakeSystem()  # partition not present
    with fake_ops(system):
        node = _with_own_output(
            Partition(number=1, size_mb=100), "part",
            PartitionResult(number=1, size_mb=100, stable_id="disk1-part1"))
        r = node.remove(ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert r.systemd_units == [], r.systemd_units


# --- LIVE format ---------------------------------------------------------

def test_format_live_makes_filesystem_and_captures_uuid():
    system = FakeSystem()
    with fake_ops(system):
        r = Format(filesystem="ext4").resolve(
            PartitionResult(number=1, size_mb=100, stable_id="disk1-part1",
                            device_path="/dev/sdz1"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert r.output.uuid == "uuid-ext4", r.output
    assert ("mkfs", "/dev/sdz1", "ext4") in system.log, system.log


def test_format_live_is_idempotent():
    system = FakeSystem()
    system.fs["/dev/sdz1"] = "ext4"
    system.uuids["/dev/sdz1"] = "existing-uuid"
    with fake_ops(system):
        r = Format(filesystem="ext4").resolve(
            PartitionResult(number=1, size_mb=100, device_path="/dev/sdz1"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert r.output.uuid == "existing-uuid", r.output
    assert system.log == [], system.log  # no mkfs


def test_format_live_refuses_to_clobber_a_different_filesystem():
    system = FakeSystem()
    system.fs["/dev/sdz1"] = "xfs"
    with fake_ops(system):
        r = Format(filesystem="ext4").resolve(
            PartitionResult(number=1, size_mb=100, device_path="/dev/sdz1"),
            ResolveMode.LIVE)
    assert r.status == NodeStatus.ERROR, r
    assert "refusing" in (r.error or ""), r.error
    assert system.log == [], system.log  # nothing destroyed


# --- LIVE mount ----------------------------------------------------------

def test_mount_live_mounts_and_writes_fstab():
    system = FakeSystem()
    with fake_ops(system):
        r = Mount(mountpoint="/mnt/data").resolve(
            FormatResult(device_path="/dev/sdz1", filesystem="ext4",
                         uuid="UU"), ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert "/mnt/data" in system.mounts
    assert system.fstab == [("UU", "/mnt/data", "ext4", "defaults")], system.fstab


def test_mount_live_idempotent_still_ensures_fstab():
    system = FakeSystem()
    system.mounts.add("/mnt/data")
    with fake_ops(system):
        r = Mount(mountpoint="/mnt/data").resolve(
            FormatResult(device_path="/dev/sdz1", filesystem="ext4",
                         uuid="UU"), ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert ("mount", "/dev/sdz1", "/mnt/data") not in system.log, system.log
    assert system.fstab == [("UU", "/mnt/data", "ext4", "defaults")], system.fstab


def test_mount_remove_umounts_and_clears_fstab():
    system = FakeSystem()
    system.mounts.add("/mnt/data")
    system.fstab.append(("UU", "/mnt/data", "ext4", "defaults"))
    with fake_ops(system):
        r = Mount(mountpoint="/mnt/data").remove(ResolveMode.LIVE)
    assert r.status == NodeStatus.SUCCESS, r
    assert "/mnt/data" not in system.mounts
    assert system.fstab == [], system.fstab


# --- re-entry plumbing ---------------------------------------------------

def test_agent_bin_is_resolved_not_a_placeholder():
    assert AGENT_BIN, "AGENT_BIN must resolve to a path"
    assert "TODO" not in AGENT_BIN


# --- fstab file management ----------------------------------------------

def test_ensure_fstab_is_idempotent_and_scoped():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fstab")
        with open(path, "w") as f:
            f.write("UUID=root / ext4 defaults 0 1\n")  # not ours; must survive

        ops_mod.ensure_fstab("U1", "/mnt/data", "ext4", "defaults", path=path)
        ops_mod.ensure_fstab("U1", "/mnt/data", "ext4", "defaults", path=path)
        lines = open(path).read().splitlines()

        managed = [ln for ln in lines if ops_mod.FSTAB_MARKER in ln]
        assert len(managed) == 1, lines            # no duplicate
        assert any(ln.startswith("UUID=root") for ln in lines), lines  # preserved

        ops_mod.remove_fstab("/mnt/data", path=path)
        lines = open(path).read().splitlines()
        assert not any(ops_mod.FSTAB_MARKER in ln for ln in lines), lines
        assert any(ln.startswith("UUID=root") for ln in lines), lines


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}\n        {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
