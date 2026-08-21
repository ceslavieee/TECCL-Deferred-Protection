"""Transactional capacity-time accounting for dynamic collective admission.

This module implements Stage A from ``DYNAMIC_ADMISSION_DESIGN.md``.  It
replays exported TE-CCL schedules as fixed templates and therefore validates
ledger accounting only; it does not reroute a new request around existing
commitments.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


Cell = Tuple[int, int, int]
Commodity = Tuple[int, int]

_WORKING_RE = re.compile(
    r"Chunk (?P<chunk>\d+) from (?P<source>\d+) traveled over "
    r"(?P<src>\d+)->(?P<dst>\d+) in epoch (?P<epoch>\d+)"
)
_RESERVATION_RE = re.compile(
    r"Chunk (?P<chunk>\d+) from (?P<source>\d+) reserves "
    r"(?P<src>\d+)->(?P<dst>\d+) in epoch (?P<epoch>\d+)"
)


@dataclass(frozen=True)
class TemplateTransfer:
    kind: str
    source: int
    chunk: int
    src: int
    dst: int
    start_epoch: int
    occupancy_epochs: int

    @property
    def commodity(self) -> Commodity:
        return self.source, self.chunk

    def shifted_cells(self, arrival_epoch: int) -> Tuple[Cell, ...]:
        start = arrival_epoch + self.start_epoch
        return tuple(
            (self.src, self.dst, epoch)
            for epoch in range(start, start + self.occupancy_epochs)
        )


@dataclass(frozen=True)
class ScheduleTemplate:
    name: str
    final_deadline_epoch: int
    working_completion: Mapping[Commodity, int]
    transfers: Tuple[TemplateTransfer, ...]

    @classmethod
    def from_path(cls, path: Path, name: Optional[str] = None) -> "ScheduleTemplate":
        payload = json.loads(path.read_text())
        occupancy = {}
        for row in payload.get("14i-Flow_Occupancy_Epochs_By_Link", []):
            src, dst = (int(value) for value in str(row["link"]).split("->"))
            weight = int(row["occupied_epochs_per_flow"])
            if weight <= 0:
                raise ValueError(f"Non-positive occupancy for {src}->{dst}: {weight}")
            occupancy[(src, dst)] = weight

        completion = {
            (int(row["source"]), int(row["chunk"])): int(
                row["working_completion_epoch"]
            )
            for row in payload.get("14h-Chunk_Working_Completion_Profile", [])
        }
        if not completion:
            raise ValueError(f"Schedule has no atomic completion profile: {path}")

        transfers: List[TemplateTransfer] = []
        for kind, rows, pattern in (
            ("working", payload.get("10-Working_Flows", []), _WORKING_RE),
            (
                "protection",
                payload.get("11b-Reserved_Backup_Slots", []),
                _RESERVATION_RE,
            ),
        ):
            for row in rows:
                match = pattern.fullmatch(str(row))
                if not match:
                    raise ValueError(f"Cannot parse {kind} transfer: {row}")
                src = int(match.group("src"))
                dst = int(match.group("dst"))
                if (src, dst) not in occupancy:
                    raise ValueError(f"Missing occupancy weight for {src}->{dst}")
                transfer = TemplateTransfer(
                    kind=kind,
                    source=int(match.group("source")),
                    chunk=int(match.group("chunk")),
                    src=src,
                    dst=dst,
                    start_epoch=int(match.group("epoch")),
                    occupancy_epochs=occupancy[(src, dst)],
                )
                if transfer.commodity not in completion:
                    raise ValueError(
                        f"Missing completion for commodity {transfer.commodity}"
                    )
                transfers.append(transfer)

        if not transfers or not any(row.kind == "protection" for row in transfers):
            raise ValueError(f"Schedule has no complete protected template: {path}")

        template = cls(
            name=name or path.stem,
            final_deadline_epoch=int(payload["8-Final_Deadline_Epoch"]),
            working_completion=completion,
            transfers=tuple(transfers),
        )
        template.validate_internal_capacity()
        return template

    def validate_internal_capacity(self) -> None:
        occupied: Counter[Cell] = Counter()
        for transfer in self.transfers:
            occupied.update(transfer.shifted_cells(0))
        conflicts = sorted(cell for cell, count in occupied.items() if count > 1)
        if conflicts:
            raise ValueError(
                f"Template {self.name} exceeds unit cell capacity at {conflicts[:5]}"
            )
        latest = max(cell[2] for cell in occupied)
        if latest >= self.final_deadline_epoch:
            raise ValueError(
                f"Template {self.name} occupies epoch {latest} outside deadline "
                f"{self.final_deadline_epoch}"
            )


@dataclass(frozen=True)
class CommittedTransfer:
    request_id: str
    template_name: str
    transfer: TemplateTransfer
    scheduled_start_epoch: int
    working_completion_epoch: int
    cells: Tuple[Cell, ...]

    @property
    def releasable_before_execution(self) -> bool:
        return (
            self.transfer.kind == "protection"
            and self.working_completion_epoch <= self.scheduled_start_epoch
        )


@dataclass(frozen=True)
class AdmissionDecision:
    request_id: str
    arrival_epoch: int
    template_name: str
    accepted: bool
    released_protection_transfers: int
    conflicting_cells: Tuple[Cell, ...]
    committed_transfers: int


class CapacityTimeLedger:
    """A unit-capacity directed-link/epoch ledger with atomic admission."""

    def __init__(self) -> None:
        self._cells: Dict[Cell, CommittedTransfer] = {}
        self._by_request: Dict[str, List[CommittedTransfer]] = defaultdict(list)
        self.decisions: List[AdmissionDecision] = []

    @property
    def occupied_cell_count(self) -> int:
        return len(self._cells)

    def occupied_cells(self) -> Mapping[Cell, CommittedTransfer]:
        return dict(self._cells)

    def release_completed_protection(self, observation_epoch: int) -> int:
        released: List[CommittedTransfer] = []
        for transfers in self._by_request.values():
            for committed in transfers:
                if (
                    committed.releasable_before_execution
                    and committed.working_completion_epoch <= observation_epoch
                    and any(self._cells.get(cell) == committed for cell in committed.cells)
                ):
                    released.append(committed)
        for committed in released:
            for cell in committed.cells:
                if self._cells.get(cell) == committed:
                    del self._cells[cell]
        return len(released)

    def try_admit(
        self,
        request_id: str,
        arrival_epoch: int,
        template: ScheduleTemplate,
    ) -> AdmissionDecision:
        if request_id in self._by_request:
            raise ValueError(f"Duplicate request id: {request_id}")
        if arrival_epoch < 0:
            raise ValueError("Arrival epoch must be non-negative")

        released = self.release_completed_protection(arrival_epoch)
        candidate: List[CommittedTransfer] = []
        candidate_cells: Counter[Cell] = Counter()
        for transfer in template.transfers:
            cells = transfer.shifted_cells(arrival_epoch)
            scheduled_start = arrival_epoch + transfer.start_epoch
            completion = arrival_epoch + template.working_completion[
                transfer.commodity
            ]
            committed = CommittedTransfer(
                request_id=request_id,
                template_name=template.name,
                transfer=transfer,
                scheduled_start_epoch=scheduled_start,
                working_completion_epoch=completion,
                cells=cells,
            )
            candidate.append(committed)
            candidate_cells.update(cells)

        internal_conflicts = {
            cell for cell, count in candidate_cells.items() if count > 1
        }
        ledger_conflicts = set(candidate_cells).intersection(self._cells)
        conflicts = tuple(sorted(internal_conflicts | ledger_conflicts))
        accepted = not conflicts
        if accepted:
            for committed in candidate:
                for cell in committed.cells:
                    self._cells[cell] = committed
            self._by_request[request_id] = candidate

        decision = AdmissionDecision(
            request_id=request_id,
            arrival_epoch=arrival_epoch,
            template_name=template.name,
            accepted=accepted,
            released_protection_transfers=released,
            conflicting_cells=conflicts,
            committed_transfers=len(candidate) if accepted else 0,
        )
        self.decisions.append(decision)
        return decision

    def assert_safe(self) -> None:
        seen: Counter[Cell] = Counter()
        active_transfers = set(self._cells.values())
        for committed in active_transfers:
            for cell in committed.cells:
                if self._cells.get(cell) == committed:
                    seen[cell] += 1
        duplicates = sorted(cell for cell, count in seen.items() if count > 1)
        if duplicates:
            raise AssertionError(f"Capacity exceeded at {duplicates[:5]}")

    def accepted_request_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._by_request))

    def residual_occupancy_payload(
        self,
        arrival_epoch: int,
        horizon_epochs: int,
    ) -> Dict[str, object]:
        """Export active commitments in a new request's local time frame."""

        if arrival_epoch < 0 or horizon_epochs <= 0:
            raise ValueError("Invalid residual-capacity export horizon")
        self.release_completed_protection(arrival_epoch)
        rows = []
        for (src, dst, global_epoch), _ in sorted(self._cells.items()):
            local_epoch = global_epoch - arrival_epoch
            if 0 <= local_epoch < horizon_epochs:
                rows.append(
                    {
                        "src": src,
                        "dst": dst,
                        "epoch": local_epoch,
                        "occupancy": 1,
                    }
                )
        trace = {
            "accounting_unit": (
                "occupied chunk-transfer units per directed link-epoch"
            ),
            "arrival_epoch": arrival_epoch,
            "horizon_epochs": horizon_epochs,
            "epochs": rows,
        }
        canonical = json.dumps(trace, sort_keys=True, separators=(",", ":"))
        trace["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        return trace


def run_trace(
    template: ScheduleTemplate,
    arrival_epochs: Sequence[int],
) -> Tuple[CapacityTimeLedger, List[AdmissionDecision]]:
    ledger = CapacityTimeLedger()
    decisions = [
        ledger.try_admit(f"request-{index}", arrival, template)
        for index, arrival in enumerate(arrival_epochs)
    ]
    ledger.assert_safe()
    return ledger, decisions
