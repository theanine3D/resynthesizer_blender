"""
Rasterize mesh UV faces into a numpy pixel mask.

Conventions match Blender's image storage: array row 0 = bottom of the image
(UV v=0), so masks index directly into pixels reshaped as (H, W, C).
"""

import numpy as np


def poly_select_mask(mesh):
    """Bool array over polygons: True = selected."""
    selected = np.empty(len(mesh.polygons), dtype=bool)
    mesh.polygons.foreach_get("select", selected)
    return selected


def uv_islands(mesh, uv_layer):
    """List of UV islands, each an int64 index array of polygon indices.

    Islands group faces sharing UV coordinates (bpy_extras semantics), so
    disconnected geometry mapped onto the same texels counts as one island.
    """
    from bpy_extras import mesh_utils

    # mesh_linked_uv_islands works on the active UV layer
    layer_index = mesh.uv_layers.find(uv_layer.name)
    previous_index = mesh.uv_layers.active_index
    if layer_index != previous_index:
        mesh.uv_layers.active_index = layer_index
    try:
        islands = mesh_utils.mesh_linked_uv_islands(mesh)
    finally:
        if layer_index != previous_index:
            mesh.uv_layers.active_index = previous_index

    return [np.asarray(island, dtype=np.int64) for island in islands]


def island_poly_mask(mesh, uv_layer, seed_mask):
    """Bool array over polygons: True = polygon belongs to a UV island that
    contains at least one seed polygon."""
    out = np.zeros(len(mesh.polygons), dtype=bool)
    for indices in uv_islands(mesh, uv_layer):
        if seed_mask[indices].any():
            out[indices] = True
    return out


def gather_triangles(mesh, uv_layer, poly_mask=None):
    """Return (N,3,2) float32 array of UV triangles from the mesh.

    poly_mask: optional bool array over polygons; only triangles of masked
    polygons are returned. None = all polygons.
    """
    mesh.calc_loop_triangles()

    loop_count = len(mesh.loops)
    uvs = np.empty(loop_count * 2, dtype=np.float32)
    uv_layer.uv.foreach_get("vector", uvs)
    uvs = uvs.reshape(loop_count, 2)

    tri_count = len(mesh.loop_triangles)
    tri_loops = np.empty(tri_count * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_loops = tri_loops.reshape(tri_count, 3)

    if poly_mask is not None:
        tri_polys = np.empty(tri_count, dtype=np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", tri_polys)
        tri_loops = tri_loops[poly_mask[tri_polys]]

    return uvs[tri_loops]  # (N, 3, 2)


def rasterize_triangles(triangles_uv, width, height, mask=None):
    """Rasterize UV-space triangles into a (height, width) bool mask.

    Pixel centers are sampled: pixel (row, col) is inside if its center
    ((col+0.5)/W, (row+0.5)/H) falls within the triangle. Row 0 = v0 (bottom).

    Coordinates wrap with texture-tiling semantics: faces placed outside the
    0..1 tile (or spanning a tile boundary) fold back into the image, matching
    how the texture repeats on the mesh.
    """
    if mask is None:
        mask = np.zeros((height, width), dtype=bool)

    tile = np.array((width, height), dtype=np.float64)
    for tri in triangles_uv:
        pts = tri.astype(np.float64) * tile  # to pixel space
        # Translate by whole tiles so the triangle's min corner lies in tile 0;
        # any remaining overhang past the tile edge is wrapped below.
        pts -= np.floor(pts.min(axis=0) / tile) * tile

        min_x = int(np.floor(pts[:, 0].min() - 0.5))
        max_x = int(np.ceil(pts[:, 0].max() + 0.5))
        min_y = int(np.floor(pts[:, 1].min() - 0.5))
        max_y = int(np.ceil(pts[:, 1].max() + 0.5))
        # One full tile of span already reaches every texel column/row
        max_x = min(max_x, min_x + width)
        max_y = min(max_y, min_y + height)
        if min_x >= max_x or min_y >= max_y:
            continue  # degenerate

        xs = np.arange(min_x, max_x, dtype=np.float64) + 0.5
        ys = np.arange(min_y, max_y, dtype=np.float64) + 0.5
        grid_x, grid_y = np.meshgrid(xs, ys)

        def edge(a, b):
            return (grid_x - a[0]) * (b[1] - a[1]) - (grid_y - a[1]) * (b[0] - a[0])

        e0 = edge(pts[0], pts[1])
        e1 = edge(pts[1], pts[2])
        e2 = edge(pts[2], pts[0])
        # accept either winding
        inside = ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))

        # Fold the (possibly out-of-bounds) bbox back into the image.
        # Spans are capped at one tile above, so folded indices are distinct.
        rows = np.arange(min_y, max_y) % height
        cols = np.arange(min_x, max_x) % width
        mask[np.ix_(rows, cols)] |= inside

    return mask


def dilate(mask, pixels):
    """Binary dilation by `pixels` steps of 8-connected growth (no scipy).

    Wraps at the image edges, consistent with texture tiling.
    """
    out = mask.copy()
    for _ in range(pixels):
        grown = out.copy()
        for shift_y in (-1, 0, 1):
            for shift_x in (-1, 0, 1):
                if shift_y or shift_x:
                    grown |= np.roll(out, (shift_y, shift_x), axis=(0, 1))
        out = grown
    return out


def erode(mask, pixels):
    """Binary erosion by `pixels` steps (wrapping, mirror of dilate)."""
    return ~dilate(~mask, pixels)


def mask_bbox(mask, margin, width, height):
    """Bounding box of true pixels grown by margin, clamped. Returns x0,y0,x1,y1."""
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    y0, y1 = np.argmax(rows), height - np.argmax(rows[::-1])
    x0, x1 = np.argmax(cols), width - np.argmax(cols[::-1])
    return (
        max(int(x0) - margin, 0),
        max(int(y0) - margin, 0),
        min(int(x1) + margin, width),
        min(int(y1) + margin, height),
    )
