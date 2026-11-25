"""Durable sample library for trajectory scenarios.

All data is synthetic simulation material; nothing here certifies real
aircraft.  ``TrajectoryDataset`` keeps scenarios and issuance receipts in one
JSON file.  Every business operation is a single atomic commit (write temp
file, fsync, rename, fsync directory), so a crash mid-operation exposes either
the state before or the complete state after the operation — never a partial
one.  Single process, single writer.
"""

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from .service import process

_STATE_VERSION = 1


def _durably_replace(src, dst):
    os.replace(src, dst)


class TrajectoryDataset:
    """Reopenable store of trajectory scenarios and their issuance receipts."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._state = self._read_state()

    def _read_state(self):
        if not self._path.exists():
            return {"version": _STATE_VERSION, "scenarios": {}, "receipts": {}}
        with self._path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict) or state.get("version") != _STATE_VERSION \
                or not isinstance(state.get("scenarios"), dict) \
                or not isinstance(state.get("receipts"), dict):
            raise ValueError("unsupported dataset state")
        return state

    def _commit(self, state):
        """Atomically replace the on-disk state, then adopt it in memory.

        If the process dies before the rename, the previous committed state is
        what a reopen sees; if it dies after, the full new state.  A request id
        and its receipt (or a new current revision together with the revocation
        of older receipts) therefore always take effect as a unit.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            _durably_replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._state = state
        dir_fd = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _ensure_open(self):
        if self._closed:
            raise ValueError("dataset is closed")

    @staticmethod
    def _check_id(value, name):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _check_revision(value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("revision must be an integer")

    def put_scenario(self, scenario_id, revision, data):
        """Commit the complete authoritative trajectory version of a scenario.

        Structurally valid but ineligible trajectories still become the current
        revision; structural errors or wrong revisions are rejected and leave
        the previous state untouched.  Returns the trajectory analysis.
        """
        with self._lock:
            self._ensure_open()
            self._check_id(scenario_id, "scenario_id")
            self._check_revision(revision)
            scenarios = self._state["scenarios"]
            current = scenarios.get(scenario_id)
            expected = 1 if current is None else current["revision"] + 1
            if revision != expected:
                raise ValueError(f"revision must be {expected}")
            if not isinstance(data, dict) or "frames" not in data:
                raise ValueError("scenario data must be a trajectory with frames")
            analysis = process(data)  # raises ValueError on structural problems; state untouched
            try:
                json.dumps(data)
            except (TypeError, ValueError) as exc:
                raise ValueError("scenario data must be JSON serializable") from exc
            record = {"revision": revision, "data": copy.deepcopy(data), "analysis": copy.deepcopy(analysis)}
            state = {"version": _STATE_VERSION, "scenarios": dict(scenarios), "receipts": self._state["receipts"]}
            state["scenarios"][scenario_id] = record
            self._commit(state)
            return copy.deepcopy(analysis)

    def issue(self, request_id, scenario_id, revision):
        """Issue a receipt binding ``request_id`` to the current eligible revision.

        Re-issuing with the same id and the same binding returns the original
        receipt unchanged, even if it can no longer be consumed; the same id
        with a different binding is rejected.  Rejected requests do not reserve
        the id.  Receipts never contain the raw loads.
        """
        with self._lock:
            self._ensure_open()
            self._check_id(request_id, "request_id")
            self._check_id(scenario_id, "scenario_id")
            self._check_revision(revision)
            receipts = self._state["receipts"]
            existing = receipts.get(request_id)
            if existing is not None:
                if existing["scenario_id"] == scenario_id and existing["revision"] == revision:
                    return copy.deepcopy(existing)
                raise ValueError("request_id already issued with a different binding")
            scenario = self._state["scenarios"].get(scenario_id)
            if scenario is None:
                raise ValueError("unknown scenario_id")
            if scenario["revision"] != revision:
                raise ValueError("revision is not the current revision")
            if not scenario["analysis"]["eligible"]:
                raise ValueError("current revision is not eligible")
            receipt = {"request_id": request_id, "scenario_id": scenario_id, "revision": revision,
                       "analysis": copy.deepcopy(scenario["analysis"])}
            state = {"version": _STATE_VERSION, "scenarios": self._state["scenarios"],
                     "receipts": dict(receipts)}
            state["receipts"][request_id] = receipt
            self._commit(state)
            return copy.deepcopy(receipt)

    def get_receipt(self, request_id):
        """Return the stored receipt unchanged (audit query; never re-analyses)."""
        with self._lock:
            self._ensure_open()
            self._check_id(request_id, "request_id")
            receipt = self._state["receipts"].get(request_id)
            if receipt is None:
                raise ValueError("unknown request_id")
            return copy.deepcopy(receipt)

    def consume(self, request_id):
        """Hand the sample to the simulated downstream while the receipt is live.

        Succeeds only while the receipt still names the current, eligible
        revision; repeatable for the same live receipt.  Unknown, stale or
        ineligible receipts raise ``ValueError`` and return nothing.
        """
        with self._lock:
            self._ensure_open()
            self._check_id(request_id, "request_id")
            receipt = self._state["receipts"].get(request_id)
            if receipt is None:
                raise ValueError("unknown request_id")
            scenario = self._state["scenarios"].get(receipt["scenario_id"])
            if scenario is None or scenario["revision"] != receipt["revision"]:
                raise ValueError("receipt no longer matches the current revision")
            if not scenario["analysis"]["eligible"]:
                raise ValueError("current revision is not eligible")
            return {"scenario_id": receipt["scenario_id"], "revision": receipt["revision"],
                    "data": copy.deepcopy(scenario["data"]), "analysis": copy.deepcopy(scenario["analysis"])}

    def close(self):
        with self._lock:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
