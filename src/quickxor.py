"""
QuickXorHash — Microsoft's proprietary hash for OneDrive.

Port of the C# reference:
https://learn.microsoft.com/en-us/onedrive/developer/code-snippets/quickxorhash
"""

from __future__ import annotations

import base64
import struct

MASK_64 = (1 << 64) - 1
WIDTH_IN_BITS = 160
SHIFT = 11


class QuickXorHash:
    __slots__ = ("_data", "_length_so_far", "_shift_so_far")

    def __init__(self) -> None:
        self._data: list[int] = [0, 0, 0]  # 2 x 64-bit + 1 x 32-bit cell = 160 bits
        self._length_so_far: int = 0
        self._shift_so_far: int = 0

    def update(self, data: bytes | bytearray | memoryview) -> None:
        current_shift = self._shift_so_far
        cells = self._data

        for byte in data:
            index = current_shift >> 6  # // 64
            offset = current_shift & 63  # % 64

            # Last cell is only 32 bits wide (160 - 2*64 = 32)
            is_last_cell = index == 2
            cell_bits = 32 if is_last_cell else 64

            if offset <= cell_bits - 8:
                cells[index] = (cells[index] ^ (byte << offset)) & MASK_64
            else:
                cells[index] = (cells[index] ^ (byte << offset)) & MASK_64
                next_index = 0 if is_last_cell else (index + 1)
                cells[next_index] ^= byte >> (cell_bits - offset)

            current_shift = (current_shift + SHIFT) % WIDTH_IN_BITS

        self._shift_so_far = current_shift
        self._length_so_far += len(data)

    def digest(self) -> bytes:
        rgb = bytearray(20)

        # Pack first two full 64-bit cells
        struct.pack_into("<Q", rgb, 0, self._data[0] & MASK_64)
        struct.pack_into("<Q", rgb, 8, self._data[1] & MASK_64)

        # Pack last cell — only 32 bits (160 - 128 = 32)
        struct.pack_into("<I", rgb, 16, self._data[2] & 0xFFFFFFFF)

        # XOR in the file length (8 bytes, little-endian) at bytes 12-19
        length_bytes = struct.pack("<q", self._length_so_far)
        for i, b in enumerate(length_bytes):
            rgb[12 + i] ^= b

        return bytes(rgb)

    def base64_digest(self) -> str:
        return base64.b64encode(self.digest()).decode("ascii")
