"""
Resynthesizer for Blender — resynthesize (heal) texture regions in Texture Paint mode.

Select faces (edit mode or texture-paint face mask), then Resynthesize: the texels
under the selected faces' UV footprint are resynthesized from the surrounding
texture. Engine: Resynthesizer by Lloyd Konneker / Paul Harrison (GPL2+).
"""

bl_info = {
    "name": "Resynthesizer",
    "author": "Pedro Valencia Oseguera (Blender addon), Lloyd Konneker (engine), Paul Harrison (algorithm)",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Texture Paint > Sidebar > Tool > Resynthesize",
    "description": "Regenerate parts of a texture by sampling surrounding pixels",
    "category": "Paint",
}

import threading

import bpy
import numpy as np

from . import resynth
from . import uv_raster


# ---------------------------------------------------------------- job plumbing

def _find_paint_image(context, obj):
    """The image texture painting would write to: explicit canvas, else the
    active material's active texture paint slot."""
    image_paint = context.scene.tool_settings.image_paint
    if image_paint.mode == 'IMAGE':
        return image_paint.canvas
    material = obj.active_material
    if material and material.texture_paint_images:
        slot = min(material.paint_active_slot, len(material.texture_paint_images) - 1)
        return material.texture_paint_images[slot]
    return None


class FillJob:
    """Data for one fill, prepared on the main thread; heal() runs off-thread."""

    def __init__(self):
        self.image = None
        self.pixels = None        # full (H, W, C) float32, written back on apply
        self.crop = None          # x0, y0, x1, y1
        self.work = None          # cropped image passed to the engine
        self.target_mask = None   # cropped, True = synthesize
        self.corpus_mask = None   # cropped, True = may sample
        self.healed = None
        self.error = None
        self.warning = None
        self.cancel = resynth.CancelToken()
        self.progress = 0


def _resolve_image_and_uv(context, obj, settings):
    """Returns (image, uv_layer, error_message)."""
    mesh = obj.data

    image = settings.image or _find_paint_image(context, obj)
    if image is None:
        return None, None, "No paint image found (choose a texture in the panel, or set a canvas/texture slot)"
    if image.size[0] == 0:
        return None, None, f"Image '{image.name}' has no data"

    if settings.uv_map:
        uv_layer = mesh.uv_layers.get(settings.uv_map)
        if uv_layer is None:
            return None, None, f"UV map '{settings.uv_map}' not found on this mesh"
    else:
        uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None, None, "Mesh has no active UV map"
    return image, uv_layer, None


def _assemble_job(image, target, corpus, sampling_radius, warning=None):
    """Crop to the working window and package everything the engine thread needs."""
    width, height = image.size
    channels = image.channels

    x0, y0, x1, y1 = uv_raster.mask_bbox(target, sampling_radius, width, height)

    job = FillJob()
    job.image = image
    job.warning = warning

    flat = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(flat)
    job.pixels = flat.reshape(height, width, channels)

    job.crop = (x0, y0, x1, y1)
    job.work = np.ascontiguousarray(job.pixels[y0:y1, x0:x1])
    job.target_mask = np.ascontiguousarray(target[y0:y1, x0:x1])

    corpus_crop = corpus[y0:y1, x0:x1]
    if not corpus_crop.any():
        return None, "No source texels within sampling radius — increase the radius"
    job.corpus_mask = np.ascontiguousarray(corpus_crop)
    return job, None


def prepare_job(context, obj, settings):
    """Validate context and build the numpy job. Returns (job, error_message)."""
    mesh = obj.data

    image, uv_layer, error = _resolve_image_and_uv(context, obj, settings)
    if error:
        return None, error

    width, height = image.size

    selected = uv_raster.poly_select_mask(mesh)
    if not selected.any():
        return None, "No faces selected (select faces to define the fill region)"
    if selected.all():
        return None, "All faces are selected — nothing left to sample from"

    selected_tris = uv_raster.gather_triangles(mesh, uv_layer, selected)
    target = uv_raster.rasterize_triangles(selected_tris, width, height)
    if not target.any():
        return None, "Selected faces cover no texels (check UV map)"

    # Tiling/overlap warning: unselected faces whose UV footprint reuses the
    # fill region's texels will change too. Erode the target first so mere
    # shared boundary texels between UV-adjacent faces don't trigger it.
    warning = None
    target_interior = (
        uv_raster.erode(target, 2) if not settings.ignore_overlap_warnings
        else np.zeros_like(target)
    )
    if target_interior.any():
        unselected_tris = uv_raster.gather_triangles(mesh, uv_layer, ~selected)
        unselected_coverage = uv_raster.rasterize_triangles(unselected_tris, width, height)
        overlap_count = int(np.count_nonzero(unselected_coverage & target_interior))
        if overlap_count:
            warning = (
                f"{overlap_count} texels in the fill region are also mapped by "
                f"unselected faces (tiled/overlapping UVs) — those areas changed too"
            )

    target = uv_raster.dilate(target, settings.seam_margin)

    # Corpus: texels under unselected faces — never the void between islands,
    # and (optionally) only faces in the same UV island(s) as the fill region.
    corpus_polys = ~selected
    if settings.same_island_only:
        island_mask = uv_raster.island_poly_mask(mesh, uv_layer, selected)
        corpus_polys &= island_mask
        if not corpus_polys.any():
            return None, ("Selected faces span their entire UV island — nothing in-island "
                          "to sample. Select fewer faces or disable 'Same UV Island Only'")

    corpus_tris = uv_raster.gather_triangles(mesh, uv_layer, corpus_polys)
    coverage = uv_raster.rasterize_triangles(corpus_tris, width, height)
    corpus = coverage & ~target
    if not corpus.any():
        return None, "No source texels to sample from (UV coverage minus fill region is empty)"

    return _assemble_job(image, target, corpus, settings.sampling_radius, warning)


def run_job(job, patch_size, probe_count):
    """Engine call. Pure numpy/ctypes — safe off the main thread."""
    try:
        params = resynth.default_parameters()
        params.patchSize = patch_size
        params.maxProbeCount = probe_count

        def on_progress(percent):
            job.progress = percent

        job.healed = resynth.heal(
            job.work, job.target_mask,
            corpus_mask=job.corpus_mask,
            params=params,
            progress=on_progress,
            cancel=job.cancel,
        )
    except Exception as exc:  # surfaced on the main thread
        job.error = str(exc)


def apply_job(job):
    """Write healed pixels back to the Blender image. Main thread only."""
    x0, y0, x1, y1 = job.crop
    job.pixels[y0:y1, x0:x1] = job.healed
    job.image.pixels.foreach_set(job.pixels.ravel())
    job.image.update()
    # Refresh any editors showing the image
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'IMAGE_EDITOR', 'VIEW_3D'}:
                area.tag_redraw()


# ------------------------------------------------------------------- operators

class _FillRunnerMixin:
    """Shared execution for fill operators: synchronous in background mode,
    modal + worker thread with progress and ESC-cancel when interactive.
    Subclasses build their job and may override _post_apply()."""

    _timer = None
    _thread = None
    _job = None

    def _post_apply(self, context):
        pass

    def _report_success(self, job):
        target_count = int(np.count_nonzero(job.target_mask))
        message = f"Resynthesized {target_count} texels"
        if job.warning:
            self.report({'WARNING'}, f"{message} — {job.warning}")
        else:
            self.report({'INFO'}, message)

    def _run_sync(self, context, settings, job):
        run_job(job, settings.patch_size, settings.probe_count)
        if job.error:
            self.report({'ERROR'}, job.error)
            return {'CANCELLED'}
        apply_job(job)
        self._post_apply(context)
        self._report_success(job)
        return {'FINISHED'}

    def _run_modal(self, context, settings, job):
        self._job = job
        self._thread = threading.Thread(
            target=run_job, args=(job, settings.patch_size, settings.probe_count),
            daemon=True,
        )
        self._thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        wm.progress_begin(0, 100)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._job.cancel.cancelled = True
            self._thread.join()
            self._finish(context)
            self.report({'WARNING'}, "Resynthesize cancelled")
            return {'CANCELLED'}

        if event.type == 'TIMER':
            context.window_manager.progress_update(self._job.progress)
            if not self._thread.is_alive():
                self._finish(context)
                if self._job.error:
                    self.report({'ERROR'}, self._job.error)
                    return {'CANCELLED'}
                apply_job(self._job)
                bpy.ops.ed.undo_push(message="Resynthesize")
                self._post_apply(context)
                self._report_success(self._job)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def _finish(self, context):
        wm = context.window_manager
        wm.progress_end()
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None


class PAINT_OT_resynthesize(_FillRunnerMixin, bpy.types.Operator):
    """Resynthesize the texture under the selected faces from surrounding texels"""

    bl_idname = "paint.resynthesize"
    bl_label = "Resynthesize"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.resynthesizer
        job, error = prepare_job(context, context.active_object, settings)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return self._run_sync(context, settings, job)

    def invoke(self, context, event):
        settings = context.scene.resynthesizer
        job, error = prepare_job(context, context.active_object, settings)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        if bpy.app.background:
            return self._run_sync(context, settings, job)
        return self._run_modal(context, settings, job)


# ------------------------------------------------------------------ settings/UI

class ResynthesizerSettings(bpy.types.PropertyGroup):
    image: bpy.props.PointerProperty(
        name="Texture",
        description="Image to fill. Leave empty to use the current paint target "
                    "(the canvas in Single Image mode, or the material's active texture slot)",
        type=bpy.types.Image,
    )
    uv_map: bpy.props.StringProperty(
        name="UV Map",
        description="UV map that defines where the selected faces land on the texture. "
                    "Leave empty to use the mesh's active UV map",
        default="",
    )
    ignore_overlap_warnings: bpy.props.BoolProperty(
        name="Ignore Overlap Warnings",
        description="Skip warnings when working with overlapping/tiled UVs."
                    "Useful when working with tiling textures "
                    "where the overlap is intentional",
        default=False,
    )
    same_island_only: bpy.props.BoolProperty(
        name="Same UV Island Only",
        description="Only sample texels from the UV island(s) containing the selected faces, "
                    "never from unrelated parts of the texture atlas",
        default=True,
    )
    sampling_radius: bpy.props.IntProperty(
        name="Sampling Radius",
        description="How far around the fill region (in pixels) to sample texture from",
        default=64, min=8, max=2048,
    )
    seam_margin: bpy.props.IntProperty(
        name="Seam Margin",
        description="Extra pixels of fill beyond the faces' UV footprint, covering bake bleed",
        default=2, min=0, max=32,
    )
    patch_size: bpy.props.IntProperty(
        name="Patch Size",
        description="Size of the matched neighborhood; larger preserves structure better but is slower",
        default=30, min=9, max=64,
    )
    probe_count: bpy.props.IntProperty(
        name="Max Probes",
        description="Search effort per pixel per pass; higher can improve quality but is slower",
        default=200, min=50, max=2000,
    )


class VIEW3D_PT_resynthesize(bpy.types.Panel):
    bl_label = "Resynthesize"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_context = ".imagepaint"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.resynthesizer
        obj = context.active_object
        mesh = obj.data if obj and obj.type == 'MESH' else None

        if mesh and not mesh.use_paint_mask:
            layout.label(text="Enable face paint mask to select region", icon='INFO')

        # What gets modified: explicit choice, or show what auto mode resolves to,
        # so multi-texture materials never fill the wrong image silently.
        layout.template_ID(settings, "image")
        if settings.image is None:
            auto_image = _find_paint_image(context, obj) if obj else None
            if auto_image is not None:
                layout.label(text=f"Auto target: {auto_image.name}", icon='INFO')
            else:
                layout.label(text="No paint image found", icon='ERROR')

        if mesh:
            layout.prop_search(settings, "uv_map", mesh, "uv_layers", icon='GROUP_UVS')
            if not settings.uv_map and mesh.uv_layers.active:
                layout.label(text=f"Auto UV map: {mesh.uv_layers.active.name}", icon='INFO')

        layout.prop(settings, "same_island_only")
        layout.prop(settings, "ignore_overlap_warnings")
        column = layout.column(align=True)
        column.prop(settings, "sampling_radius")
        column.prop(settings, "seam_margin")
        column.prop(settings, "patch_size")
        column.prop(settings, "probe_count")

        layout.operator(PAINT_OT_resynthesize.bl_idname, icon='BRUSHES_ALL')


_classes = (
    ResynthesizerSettings,
    PAINT_OT_resynthesize,
    VIEW3D_PT_resynthesize,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.resynthesizer = bpy.props.PointerProperty(type=ResynthesizerSettings)


def unregister():
    del bpy.types.Scene.resynthesizer
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
