"""Navigation helpers for traversing devices and chains in Live.

This module keeps a lightweight path stack to follow the current selection
from the selected track down through nested racks and chains. It exposes helper
methods for moving between sibling devices, entering and exiting chains, and
feeding selection updates back to a caller-provided feedback function (e.g. a
TouchOSC surface).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple


@dataclass
class NavigationStep:
    """Represents a hop into a rack: the rack device index and chosen chain."""

    device_index: int
    chain_index: int


class DeviceNavigator:
    """Keeps track of nested device/chain navigation for the selected track."""

    def __init__(self, song, feedback_callback: Optional[Callable[[dict], None]] = None):
        self.song = song
        self.feedback_callback = feedback_callback
        self.path: List[NavigationStep] = []
        self._last_track = self._selected_track

    # ---- Selection helpers -------------------------------------------------
    @property
    def _selected_track(self):
        try:
            return self.song.view.selected_track
        except Exception:
            return None

    @property
    def _selected_device(self):
        track = self._selected_track
        if track is None:
            return None
        try:
            return track.view.selected_device
        except Exception:
            return None

    def select_device(self, device) -> None:
        track = self._selected_track
        if track is None or device is None:
            return
        try:
            track.view.selected_device = device
        except Exception:
            return
        self._send_feedback()

    def select_chain(self, chain) -> None:
        track = self._selected_track
        if track is None:
            return
        try:
            track.view.selected_chain = chain
        except Exception:
            return
        self._send_feedback()

    # ---- Internal helpers --------------------------------------------------
    def _devices_for_container(self, container) -> Sequence:
        return list(getattr(container, "devices", []) or [])

    def _chains_for_device(self, device) -> Sequence:
        return list(getattr(device, "chains", []) or [])

    def _ensure_track(self) -> None:
        track = self._selected_track
        if track is None:
            self.path = []
            self._last_track = None
            return
        if track is not self._last_track:
            self.reset_to_track_root()
            self._last_track = track

    def _validate_path(self) -> Tuple[Sequence, Optional[object]]:
        """Confirm the stored path is still valid. Resets on invalidation."""

        self._ensure_track()
        track = self._selected_track
        if track is None:
            return [], None

        container = track
        chain = None
        for step in list(self.path):
            devices = self._devices_for_container(container)
            if step.device_index >= len(devices):
                self.reset_to_track_root()
                return self._devices_for_container(track), None
            device = devices[step.device_index]
            chains = self._chains_for_device(device)
            if step.chain_index >= len(chains):
                self.reset_to_track_root()
                return self._devices_for_container(track), None
            chain = chains[step.chain_index]
            container = chain
        return self._devices_for_container(container), chain

    def _current_devices(self) -> Sequence:
        devices, _ = self._validate_path()
        return devices

    def _current_chain(self):
        _, chain = self._validate_path()
        return chain

    def _device_index(self, devices: Sequence, target) -> Optional[int]:
        for idx, candidate in enumerate(devices):
            if candidate is target:
                return idx
        return None

    def _current_device_index(self) -> Optional[int]:
        devices = self._current_devices()
        selected = self._selected_device
        return self._device_index(devices, selected)

    def _current_rack_chain_selection(self, rack) -> Optional[int]:
        chains = self._chains_for_device(rack)
        track = self._selected_track
        if track is None:
            return None
        selected_chain = getattr(track.view, "selected_chain", None)
        if selected_chain in chains:
            return chains.index(selected_chain)
        return None

    # ---- State description -------------------------------------------------
    def current_device(self):
        self._validate_path()
        return self._selected_device

    def current_chain(self):
        return self._current_chain()

    def describe_state(self) -> dict:
        devices = self._current_devices()
        chain = self._current_chain()
        device = self._selected_device
        device_index = self._device_index(devices, device)
        chain_depth = len(self.path)
        chain_info = None

        if chain is not None and self.path:
            parent_container = self._selected_track if chain_depth == 1 else self._current_chain_from_depth(chain_depth - 1)
            parent_device = None
            if parent_container is not None:
                parent_devices = self._devices_for_container(parent_container)
                if self.path[-1].device_index < len(parent_devices):
                    parent_device = parent_devices[self.path[-1].device_index]
            chains = self._chains_for_device(parent_device) if parent_device is not None else []
            chain_position = chains.index(chain) + 1 if chain in chains else None
            chain_total = len(chains)
            chain_info = {
                "name": getattr(chain, "name", ""),
                "position": chain_position,
                "count": chain_total,
            }

        return {
            "track": getattr(self._selected_track, "name", ""),
            "device": getattr(device, "name", ""),
            "device_index": device_index,
            "device_count": len(devices),
            "chain_depth": chain_depth,
            "chain": chain_info,
            "path": [(step.device_index, step.chain_index) for step in self.path],
        }

    def _current_chain_from_depth(self, depth: int):
        if depth > len(self.path):
            return None

        container = self._selected_track
        if container is None:
            return None
        for step in self.path[:depth]:
            device = self._devices_for_container(container)[step.device_index]
            container = self._chains_for_device(device)[step.chain_index]
        return container

    def _send_feedback(self):
        if self.feedback_callback:
            self.feedback_callback(self.describe_state())

    # ---- Navigation --------------------------------------------------------
    def enter_chain(self, chain_index: int) -> None:
        devices = self._current_devices()
        device_index = self._current_device_index()
        if device_index is None:
            return
        device = devices[device_index]
        chains = self._chains_for_device(device)
        if not chains or chain_index >= len(chains):
            return

        chain = chains[chain_index]
        self.path.append(NavigationStep(device_index=device_index, chain_index=chain_index))
        self.select_chain(chain)
        chain_devices = self._devices_for_container(chain)
        if chain_devices:
            self.select_device(chain_devices[0])
        else:
            self._send_feedback()

    def exit_chain(self) -> None:
        if not self.path:
            return

        popped_step = self.path.pop()
        container_devices = self._current_devices()
        if popped_step.device_index >= len(container_devices):
            self.reset_to_track_root()
            return

        parent_device = container_devices[popped_step.device_index]
        self.select_device(parent_device)

        if self.path:
            parent_chain = self._current_chain_from_depth(len(self.path))
            if parent_chain is not None:
                self.select_chain(parent_chain)
        else:
            track = self._selected_track
            if track is not None:
                try:
                    track.view.selected_chain = None
                except Exception:
                    pass

    def next_device(self) -> None:
        devices = self._current_devices()
        if not devices:
            return
        idx = self._current_device_index()
        if idx is None or idx + 1 >= len(devices):
            return
        self.select_device(devices[idx + 1])

    def previous_device(self) -> None:
        devices = self._current_devices()
        if not devices:
            return
        idx = self._current_device_index()
        if idx is None or idx == 0:
            return
        self.select_device(devices[idx - 1])

    def next_chain(self) -> None:
        device = self._selected_device
        if device is None:
            return
        chains = self._chains_for_device(device)
        if not chains:
            return
        current_index = self._current_rack_chain_selection(device)
        if current_index is None:
            new_index = 0
        elif current_index + 1 < len(chains):
            new_index = current_index + 1
        else:
            new_index = 0
        self.select_chain(chains[new_index])

    def previous_chain(self) -> None:
        device = self._selected_device
        if device is None:
            return
        chains = self._chains_for_device(device)
        if not chains:
            return
        current_index = self._current_rack_chain_selection(device)
        if current_index is None:
            new_index = len(chains) - 1
        elif current_index > 0:
            new_index = current_index - 1
        else:
            new_index = len(chains) - 1
        self.select_chain(chains[new_index])

    # ---- Command helpers ---------------------------------------------------
    def move_left(self) -> None:
        self.previous_device()

    def move_right(self) -> None:
        self.next_device()

    def move_up(self) -> None:
        self.exit_chain()

    def move_down(self) -> None:
        device = self._selected_device
        if device is None:
            return
        chains = self._chains_for_device(device)
        if not chains:
            return
        track = self._selected_track
        selected_chain = getattr(track.view, "selected_chain", None) if track else None
        chain_index = chains.index(selected_chain) if selected_chain in chains else 0
        self.enter_chain(chain_index)

    def reset_to_track_root(self) -> None:
        self.path = []
        track = self._selected_track
        if track is None:
            return
        try:
            track.view.selected_chain = None
        except Exception:
            pass
        devices = self._devices_for_container(track)
        if devices:
            self.select_device(devices[0])
        else:
            self._send_feedback()

    # ---- Path maintenance --------------------------------------------------
    def ensure_selection_valid(self) -> None:
        """Reset navigation when the stored path no longer matches Live state."""

        devices, _ = self._validate_path()
        device = self._selected_device
        if device not in devices:
            self.reset_to_track_root()

    def refresh_feedback(self) -> None:
        self._send_feedback()
