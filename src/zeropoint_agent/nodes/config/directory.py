"""DirectoryVar — a Var whose value is a filesystem path the agent owns.

A DirectoryVar is a regular Var[str] in every observable way: it
produces a `VarResult[str](name, value)`, can hold a literal path or
be linked to another Var[str], and downstream consumers can't
tell the difference from a vanilla Var.

What's special is what happens **when its `value` is edited**. The
agent treats `value` as the canonical location of a directory it owns:
on a config change, before the new value is persisted, the directory
at the old path is moved to the new path. So consumers always see a
value that points to a real directory with the live data inside it.

Used for:
  - `modules/<id>/zp_module_dir` — the terraform working dir (state +
    cloned source)
  - `modules/<id>/zp_storage_dir` — the module's isolated data root

Move semantics:
  - Same filesystem  → atomic rename(2).
  - Cross filesystem → rsync to <new>.incoming on the destination FS,
    atomic rename to <new>, best-effort rmtree(<old>). Resumable: a
    stale .incoming from a crashed run is detected and resumed.

All side effects happen in `on_config_changed`, which is called by the
mutation handler before the change is persisted. If the move fails the
hook raises and the change is rejected; the user sees the error and
nothing is persisted.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from zeropoint_agent.inode import NodeResult, NodeStatus, ResolveMode, readonly
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.nodes.config.var import Var, VarResult

logger = logging.getLogger(__name__)


def _same_filesystem(a: Path, b: Path) -> bool:
    """True if both paths live on the same filesystem device.

    Either path may not exist yet; for non-existent paths we walk up to
    the first existing ancestor.
    """
    def device_of(p: Path) -> int:
        cur = p
        while True:
            try:
                return os.stat(cur).st_dev
            except FileNotFoundError:
                if cur.parent == cur:
                    raise
                cur = cur.parent
    return device_of(a) == device_of(b)


def _is_descendant(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or any descendant of `parent`."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, FileNotFoundError):
        return False


def _safe_move(old: Path, new: Path) -> None:
    """Move a directory tree from `old` to `new`, safely.

    On same-filesystem moves this is atomic. On cross-filesystem moves
    rsync copies to `<new>.incoming` first, an atomic rename swaps it
    into place, then the old tree is removed best-effort. The "old gone,
    new not in place" window is reduced to the rename, which is atomic.
    """
    if not old.exists():
        # Nothing on disk to move. The DirectoryVar's value can still be
        # updated; the directory will be created lazily by whichever
        # downstream consumer needs it (terraform, docker bind mount).
        logger.info("DirectoryVar move: %s does not exist; nothing to move", old)
        return

    if new.exists():
        raise ValueError(
            f"destination already exists: {new}; refusing to overwrite")

    if _is_descendant(new, old):
        raise ValueError(
            f"destination {new} is inside source {old}; refusing self-loop")

    new.parent.mkdir(parents=True, exist_ok=True)

    if _same_filesystem(old, new.parent):
        logger.info("DirectoryVar move (atomic rename): %s -> %s", old, new)
        os.rename(old, new)
        return

    # Cross-filesystem: rsync to a sibling temp on the destination FS,
    # then atomic-swap into place. rsync's --partial keeps incomplete
    # files so an interrupted move resumes on retry.
    tmp = Path(f"{new}.incoming")
    if tmp.exists():
        logger.info("DirectoryVar move: resuming previous rsync into %s", tmp)
    else:
        tmp.mkdir(parents=True)

    logger.info("DirectoryVar move (cross-fs rsync): %s -> %s (via %s)",
                old, new, tmp)
    # Trailing slashes matter: rsync 'a/' -> 'b/' copies CONTENTS into b.
    subprocess.run(
        ["rsync", "-a", "--partial", f"{old}/", f"{tmp}/"],
        check=True,
    )
    os.rename(tmp, new)
    # rmtree is best-effort: at this point `new` already holds the live
    # data, so a failure to delete `old` only leaks disk space.
    try:
        shutil.rmtree(old)
    except Exception:
        logger.exception(
            "DirectoryVar move: failed to remove old directory %s "
            "(new location %s is good); leaking disk space", old, new)


@dataclass
class DirectoryVar(Var[str]):
    """A Var whose value is a directory path the agent owns.

    Editing `value` moves the directory before the new value is
    persisted. See module docstring for full move semantics.
    """

    previous_value: Optional[str] = readonly(default=None)

    def on_config_changed(
        self,
        old_config: dict,
        new_config: dict,
        mode: ResolveMode,
    ) -> None:
        # We only react to a change in `value`. `name` doesn't have
        # on-disk consequences.
        old_value = old_config.get("value")
        new_value = new_config.get("value")
        if old_value == new_value:
            return
        del mode
        self.previous_value = str(old_value) if old_value else None

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult[str]]:
        if (
            mode == ResolveMode.LIVE
            and self.previous_value
            and self.value
            and self.previous_value != self.value
            and Path(self.previous_value).exists()
        ):
            return NodeResult()
        return super().verify(mode)

    def resolve_promise(self, input, mode, context):
        if (
            mode != ResolveMode.LIVE
            or not self.previous_value
            or not self.value
            or self.previous_value == self.value
        ):
            return super().resolve_promise(input, mode, context)
        paths = sorted({self.previous_value, self.value})
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.directory.move",
                arguments={
                    "name": self.name,
                    "old": self.previous_value,
                    "new": self.value,
                },
                resources=[f"path:{path}" for path in paths],
                max_attempts=3,
            ),
        )

    def on_result_applied(
        self,
        result: NodeResult[VarResult[str]],
        mode: ResolveMode,
    ) -> bool:
        del mode
        if (
            result.status == NodeStatus.SUCCESS
            and result.output is not None
            and result.output.value == self.value
            and self.previous_value is not None
        ):
            self.previous_value = None
            return True
        return False


def execute_directory_move_job(
    context: JobContext,
    arguments: Dict[str, str],
) -> NodeResult[VarResult[str]]:
    old = Path(str(arguments["old"]))
    new = Path(str(arguments["new"]))
    context.progress(0.1, {"phase": "prepare"})
    context.cancellation.raise_if_requested()
    _safe_move(old, new)
    context.cancellation.raise_if_requested()
    context.progress(1.0, {"phase": "complete"})
    return NodeResult.success(VarResult(
        name=str(arguments["name"]),
        value=str(new),
    ))
