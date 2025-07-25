from sys import getsizeof
from typing import Iterator

import numpy as np
import pyarrow
from ray.data.block import Block
from ray.data.datasource import FileBasedDatasource

FILE_EXTENSIONS = [
    "svs",
    "tif",
    "dcm",
    "ndpi",
    "vms",
    "vmu",
    "scn",
    "mrxs",
    "tiff",
    "svslide",
    "bif",
    "czi",
]


class OpenSlideMetaDatasource(FileBasedDatasource):
    """Datasource for reading OpenSlide metadata.

    This datasource reads metadata from OpenSlide files and returns a block containing
    the metadata for each file. The metadata includes the slide dimensions, tile extent,
    stride, and resolution (microns per pixel) for the specified level or resolution.

    Args:
        paths: Path(s) to the OpenSlide files.
        mpp: Desired resolution in microns per pixel. If provided, `level` must be None.
        level: Desired level of the slide. If provided, `mpp` must be None.
        tile_extent: Size of the tiles to be generated from the slide.
        stride: Stride for tiling the slide.
        **file_based_datasource_kwargs: Additional arguments for the FileBasedDatasource.

    Raises:
        AssertionError: If both `mpp` and `level` are provided or if neither is provided.

    Example:
        >>> from histopath.ray.datasource.openslide_metadatasource import OpenSlideMetaDatasource
        >>> datasource = OpenSlideMetaDatasource(
        ...     paths=["slide1.svs", "slide2.tiff"],
        ...     mpp=0.25,
        ...     tile_extent=(512, 512),
        ...     stride=(256, 256),
        ... )
    """

    def __init__(
        self,
        paths: str | list[str],
        *,
        mpp: float | None = None,
        level: int | None = None,
        tile_extent: int | tuple[int, int],
        stride: int | tuple[int, int],
        **file_based_datasource_kwargs,
    ) -> None:
        super().__init__(
            paths, file_extensions=FILE_EXTENSIONS, **file_based_datasource_kwargs
        )

        assert (mpp is not None) != (level is not None), (
            "Exactly one of 'mpp' or 'level' must be provided, not both or neither."
        )

        self.desired_mpp = mpp
        self.desired_level = level
        self.tile_extent = np.broadcast_to(tile_extent, 2)
        self.stride = np.broadcast_to(stride, 2)

    def _read_stream(self, f: pyarrow.NativeFile, path: str) -> Iterator[Block]:
        from ray.data._internal.delegating_block_builder import DelegatingBlockBuilder

        from histopath.openslide import OpenSlide

        with OpenSlide(path) as slide:
            if self.desired_level is not None:
                level = self.desired_level
            else:
                assert self.desired_mpp is not None
                level = slide.closest_level(self.desired_mpp)
            mpp_x, mpp_y = slide.slide_resolution(level)

            extent = slide.level_dimensions[level]

        builder = DelegatingBlockBuilder()
        item = {
            "path": path,
            "extent_x": extent[0],
            "extent_y": extent[1],
            "tile_extent_x": self.tile_extent[0],
            "tile_extent_y": self.tile_extent[1],
            "stride_x": self.stride[0],
            "stride_y": self.stride[1],
            "mpp_x": mpp_x,
            "mpp_y": mpp_y,
            "level": level,
        }
        builder.add(item)
        yield builder.build()

    def _rows_per_file(self) -> int:  # type: ignore[override]
        return 1

    def estimate_inmemory_data_size(self) -> int | None:
        paths = self._paths()
        if not paths:
            return 0

        # Create a sample item to calculate the base size of a single row.
        sample_item = {
            "path": "",
            "extent_x": 0,
            "extent_y": 0,
            "tile_extent_x": 0,
            "tile_extent_y": 0,
            "stride_x": 0,
            "stride_y": 0,
            "mpp_x": 0.0,
            "mpp_y": 0.0,
            "level": 0,
        }

        # Calculate the size of the dictionary structure, keys, and fixed-size values.
        base_row_size = getsizeof(sample_item)
        for k, v in sample_item.items():
            base_row_size += getsizeof(k)
            base_row_size += getsizeof(v)

        # Calculate the total size of all path strings.
        total_path_size = sum(getsizeof(p) for p in paths)

        # The total estimated size is the base size for each row plus the total size of paths.
        return base_row_size * len(paths) + total_path_size
