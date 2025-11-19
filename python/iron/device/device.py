# device.py -*- Python -*-
#
# This file is licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# (c) Copyright 2024 Advanced Micro Devices, Inc.

from abc import abstractmethod
from typing import Generator

from ... import ir  # type: ignore
from ...dialects._aie_enum_gen import WireBundle  # type: ignore
from ...dialects.aie import AIEDevice, tile, TileOp, get_target_model  # type: ignore
from ..resolvable import Resolvable
from .tile import Tile

import re


class Device(Resolvable):
    """
    A base class for representations of a device of a specific type.

    Note: this class is abstract because it does not implement Resolve
    """

    class __DeviceTile(Resolvable):
        """
        Interior class for tiles objects owned by a particular device.
        This is needed to ensure we don't generate more than one MLIR operation corresponding
        to the same logical tile within a device.
        """

        def __init__(self, col: int, row: int) -> None:
            self._col: int = col
            self._row: int = row
            self._op: TileOp | None = None
            super().__init__()

        def resolve(
            self,
            loc: ir.Location | None = None,
            ip: ir.InsertionPoint | None = None,
            allocation_scheme: str | None = None,
        ) -> None:
            if self._op == None:
                self._op = tile(
                    self._col,
                    self._row,
                    loc=loc,
                    ip=ip,
                    allocation_scheme=allocation_scheme,
                )

        @property
        def op(self) -> TileOp:
            if not self._op:
                raise ValueError("Cannot get operation before it is set.")
            return self._op

        @op.setter
        def op(self, op: TileOp):
            if self._op:
                raise ValueError("Cannot set operation more than once.")
            self._op = op

    def __init__(self, device: AIEDevice) -> None:
        """Initialize a representation of a device.

        Args:
            device (AIEDevice): aie device
        """
        self._device = device
        self._tiles: list[list[Device.__DeviceTile]] = []
        self._tm = get_target_model(device)
        for c in range(self._tm.columns()):
            self._tiles.append([])
            for r in range(self._tm.rows()):
                self._tiles[c].append(Device.__DeviceTile(c, r))

    def tile_iterator(self) -> Generator[Tile, None, None]:
        """
        Iterates over the device tiles deterministically
        """
        for c in range(self._tm.columns()):
            for r in range(self._tm.rows()):
                yield self._tiles[c][r]
        return None

    @property
    def rows(self) -> int:
        return self._tm.rows()

    @property
    def cols(self) -> int:
        return self._tm.columns()

    def get_shim_tiles(self) -> list[Tile]:
        """Returns a list of all shim tiles on the device.

        Returns:
            list[Tile]: A list of shim tiles.
        """
        return [
            Tile(t._col, t._row)
            for t in self.tile_iterator()
            if self._tm.is_shim_noc_or_pl_tile(t._col, t._row)
        ]

    def get_mem_tiles(self) -> list[Tile]:
        """Returns a list of all mem tiles on the device.

        Returns:
            list[Tile]: A list of mem tiles.
        """
        return [
            Tile(t._col, t._row)
            for t in self.tile_iterator()
            if self._tm.is_mem_tile(t._col, t._row)
        ]

    def get_compute_tiles(self) -> list[Tile]:
        """Returns a list of all compute tiles on the device.

        Returns:
            list[Tile]: A list of compute tiles.
        """
        return [
            Tile(t._col, t._row)
            for t in self.tile_iterator()
            if self._tm.is_core_tile(t._col, t._row)
        ]

    def get_num_source_switchbox_connections(self, t: Tile) -> int:
        """Returns number of DMA source ports in the switchbox for the given tile on the device.

        Returns:
            int: Number of DMA source ports.
        """
        col = t.col
        row = t.row
        bundle = WireBundle.DMA
        return self._tm.get_num_source_switchbox_connections(col, row, bundle)

    def get_num_dest_switchbox_connections(self, t: Tile) -> int:
        """Returns number of DMA dest ports in the switchbox for the given tile on the device.

        Returns:
            int: Number of DMA dest ports.
        """
        col = t.col
        row = t.row
        bundle = WireBundle.DMA
        return self._tm.get_num_dest_switchbox_connections(col, row, bundle)

    def get_num_source_shim_mux_connections(self, t: Tile) -> int:
        """Returns number of DMA source ports in the shim mux for the given tile on the device.

        Returns:
            int: Number of DMA source ports.
        """
        col = t.col
        row = t.row
        bundle = WireBundle.DMA
        return self._tm.get_num_source_shim_mux_connections(col, row, bundle)

    def get_num_dest_shim_mux_connections(self, t: Tile) -> int:
        """Returns number of DMA dest ports in the shim mux for the given tile on the device.

        Returns:
            int: Number of DMA dest ports.
        """
        col = t.col
        row = t.row
        bundle = WireBundle.DMA
        return self._tm.get_num_dest_shim_mux_connections(col, row, bundle)

    def get_num_connections(self, tile: Tile, output: bool) -> int:
        """Returns number of DMA input or output "channels" available on the tile.
        Returns:
            int: Number of connections (channels) available on the tile
        """
        if tile.row == 0:
            if output:
                return self.get_num_source_shim_mux_connections(tile)
            else:
                return self.get_num_dest_shim_mux_connections(tile)
        if output:
            return self.get_num_source_switchbox_connections(tile)
        else:
            return self.get_num_dest_switchbox_connections(tile)

    def get_hardware_wrap_step_iter_bits(self, t: Tile) -> tuple:
        """Returns hardware wrap, step, and iter bits based on the tile type.

        Returns:
            tuple: Wrap, step, and iter bits.
        """
        wrap_bits = 0
        step_bits = 0
        iter_bits = 6
        if self._tm.is_shim_noc_or_pl_tile(t._col, t._row):
            step_bits = 20  # XAIEMLGBL_NOC_MODULE_DMA_BD0_3_D0_STEPSIZE_WIDTH
            wrap_bits = 10  # XAIEMLGBL_NOC_MODULE_DMA_BD0_3_D0_WRAP_WIDTH
        elif self._tm.is_mem_tile(t._col, t._row):
            step_bits = 17  # XAIEMLGBL_MEM_TILE_MODULE_DMA_BD0_2_D0_STEPSIZE_WIDTH
            wrap_bits = 10  # XAIEMLGBL_MEM_TILE_MODULE_DMA_BD0_2_D0_WRAP_WIDTH
        elif self._tm.is_core_tile(t._col, t._row):
            step_bits = 13  # XAIEMLGBL_MEMORY_MODULE_DMA_BD0_2_D0_STEPSIZE_WIDTH
            wrap_bits = 8  # XAIEMLGBL_MEMORY_MODULE_DMA_BD0_3_D0_WRAP_WIDTH
        else:
            raise ValueError(
                f"Unsupported tile type at ({t._col},{t._row}) Must be ShimNOC, Mem or Core."
            )
        return (wrap_bits, step_bits, iter_bits)

    def get_hardware_size_limit(self, t: Tile, dim: int) -> int | None:
        """Returns hardware size limit for the device in a spcecific dimension.
        Some hardware sizes may not have a limit.

        Returns:
            int: Hardware size limit for the specified dimension
        """
        wrap_bits, step_bits, iter_bits = get_hardware_wrap_step_iter_bits(t)
        if dim < 2:
            return (1 << wrap_bits) - 1
        elif dim == 3:
            return 1 << iter_bits
        return None

    def get_hardware_stride_limit(self, t: Tile, dim: int, hw_size: int) -> int | None:
        """Returns hardware stride limit for the device in a spcecific dimension.
        The hardware size needs to be passed in since the last dimension only has a limit when its size is greater than 1.

        Returns:
            int: Hardware stride limit for the specified dimension
        """
        wrap_bits, step_bits, iter_bits = get_hardware_wrap_step_iter_bits(t)
        if dim < 3:
            return 1 << step_bits
        elif dim == 3 and hw_size > 1:
            return 1 << step_bits
        return None

    def is_legal_mem_affinity(self, src_tile: Tile, dst_tile: Tile) -> bool:
        """Returns whether memory on a destination can be accessed by a source.
        Returns:
            int: Number of connections (channels) available on the tile
        """
        return self._tm.is_legal_mem_affinity(
            src_tile.col, src_tile.row, dst_tile.col, dst_tile.rol
        )

    def resolve_tile(
        self,
        tile: Tile,
        loc: ir.Location | None = None,
        ip: ir.InsertionPoint | None = None,
    ) -> None:
        self._tiles[tile.col][tile.row].resolve(loc, ip, tile.allocation_scheme)
        tile.op = self._tiles[tile.col][tile.row].op


def create_class(class_name, device):

    def _device__init__(self) -> None:
        super(globals()[class_name], self).__init__(device=device)

    def _device_resolve(
        self,
        loc: ir.Location | None = None,
        ip: ir.InsertionPoint | None = None,
    ) -> None:
        return device

    globals()[class_name] = type(
        class_name,
        (Device,),
        {
            "__init__": _device__init__,
            "resolve": _device_resolve,
            "__doc__": f"A representation of a device that resolves to {device}",
        },
    )


for device in AIEDevice:
    class_name = re.sub(r"NPU(\d+)_(\d+)COL", r"NPU\1Col\2", device.name.upper())
    create_class(class_name, device)
