"""
ctypes binding for libresynth (standalone Resynthesizer engine).

Wraps imageSynth()/imageSynth2() from libresynth.dll/.so: heals (resynthesizes)
a masked region of an image from its surroundings, in place.

Designed to be vendored into the Blender add-on unchanged: no dependencies
beyond numpy and the compiled engine library.

Engine: Copyright (C) Lloyd Konneker, GPL2+. Algorithm by Paul Harrison.
"""

import ctypes
import os
import sys

import numpy as np

__all__ = [
    "SynthParameters",
    "CancelToken",
    "ResynthError",
    "load_library",
    "default_parameters",
    "heal",
]

# TImageFormat enum (imageFormat.h)
FORMAT_RGB = 0
FORMAT_RGBA = 1
FORMAT_GRAY = 2
FORMAT_GRAYA = 3

_CHANNELS_TO_FORMAT = {3: FORMAT_RGB, 4: FORMAT_RGBA, 1: FORMAT_GRAY, 2: FORMAT_GRAYA}

# TImageSynthError (engineParams.h)
_ERROR_NAMES = {
    0: "success",
    1: "invalid image format",
    2: "image/mask size mismatch",
    3: "patch size exceeded",
    4: "matchContextType out of range",
    5: "empty target (mask selects nothing)",
    6: "empty corpus (mask selects everything, nothing to sample)",
}


class ResynthError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(
            f"imageSynth failed: {_ERROR_NAMES.get(code, 'unknown error')} (code {code})"
        )


class _ImageBuffer(ctypes.Structure):
    # imageBuffer.h
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("rowBytes", ctypes.c_size_t),
    ]


class SynthParameters(ctypes.Structure):
    # engineParams.h TImageSynthParameters
    _fields_ = [
        ("isMakeSeamlesslyTileableHorizontally", ctypes.c_int),
        ("isMakeSeamlesslyTileableVertically", ctypes.c_int),
        ("matchContextType", ctypes.c_int),
        ("mapWeight", ctypes.c_double),
        ("sensitivityToOutliers", ctypes.c_double),
        ("patchSize", ctypes.c_uint),
        ("maxProbeCount", ctypes.c_uint),
    ]


_PROGRESS_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)

_lib = None


def _library_filename():
    """Engine binary for this OS/architecture."""
    import platform

    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if sys.platform == "win32":
        return "libresynth.dll"
    if sys.platform == "darwin":
        return f"libresynth-macos-{arch}.dylib"
    return f"libresynth-linux-{arch}.so"


def load_library(path=None):
    """Load libresynth. Searches next to this file, then ../build, unless a path is given."""
    global _lib
    if _lib is not None and path is None:
        return _lib

    if path is None:
        libname = _library_filename()
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(here, libname),
            os.path.join(here, "..", "build", libname),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                path = candidate
                break
        else:
            raise FileNotFoundError(f"{libname} not found near {here}")

    lib = ctypes.CDLL(os.path.abspath(path))

    lib.setDefaultParams.argtypes = [ctypes.POINTER(SynthParameters)]
    lib.setDefaultParams.restype = None

    common_args = [
        ctypes.POINTER(_ImageBuffer),   # image (in/out)
        ctypes.POINTER(_ImageBuffer),   # mask: 0xFF = heal
    ]
    tail_args = [
        ctypes.c_int,                   # TImageFormat
        ctypes.POINTER(SynthParameters),  # NULL = defaults
        _PROGRESS_FUNC,                 # progress callback
        ctypes.c_void_p,                # context passed to callback
        ctypes.POINTER(ctypes.c_int),   # cancel flag, polled by engine
    ]
    lib.imageSynth.argtypes = common_args + tail_args
    lib.imageSynth.restype = ctypes.c_int
    lib.imageSynth2.argtypes = common_args + [ctypes.POINTER(_ImageBuffer)] + tail_args
    lib.imageSynth2.restype = ctypes.c_int

    _lib = lib
    return lib


def default_parameters():
    """Engine defaults (matchContext, patchSize 30, 200 probes)."""
    params = SynthParameters()
    load_library().setDefaultParams(ctypes.byref(params))
    return params


class CancelToken:
    """Pass to heal(); set .cancelled = True from another thread to abort."""

    def __init__(self):
        self._flag = ctypes.c_int(0)

    @property
    def cancelled(self):
        return bool(self._flag.value)

    @cancelled.setter
    def cancelled(self, value):
        self._flag.value = 1 if value else 0


def _as_buffer(array):
    buf = _ImageBuffer()
    buf.data = array.ctypes.data_as(ctypes.c_void_p)
    buf.height, buf.width = array.shape[0], array.shape[1]
    channels = array.shape[2] if array.ndim == 3 else 1
    buf.rowBytes = array.shape[1] * channels
    return buf


def _prepare_mask(mask, shape):
    if mask.shape[:2] != shape:
        raise ValueError(f"mask shape {mask.shape[:2]} != image shape {shape}")
    if mask.dtype == np.bool_:
        out = mask.astype(np.uint8) * 0xFF
    else:
        out = np.where(np.ascontiguousarray(mask) >= 128, 0xFF, 0).astype(np.uint8)
    return np.ascontiguousarray(out)


def heal(image, mask, corpus_mask=None, params=None, progress=None, cancel=None):
    """
    Resynthesize: fill the masked region of `image` from its surroundings.

    image:  numpy (H,W,C) uint8, or float32/float64 in 0..1 (Blender-style).
            C in {1,2,3,4}; alpha channel is preserved, not synthesized.
    mask:   (H,W) bool, or uint8 where >=128 means "heal this pixel".
    corpus_mask: optional (H,W) same encoding; restricts where the engine may
            sample from (e.g. same UV island). Default: inverse of `mask`.
    params: SynthParameters, or None for engine defaults.
    progress: callable(percent:int) or None. Values are clamped to 100.
    cancel: CancelToken or None.

    Returns a healed copy in the same dtype/shape as the input.
    """
    lib = load_library()

    image = np.asarray(image)
    if image.ndim == 2:
        image = image[:, :, np.newaxis]
    if image.ndim != 3 or image.shape[2] not in _CHANNELS_TO_FORMAT:
        raise ValueError(f"expected (H,W,C) with C in 1..4, got {image.shape}")

    input_dtype = image.dtype
    if input_dtype == np.uint8:
        work = np.ascontiguousarray(image).copy()
    elif input_dtype in (np.float32, np.float64):
        work = np.ascontiguousarray(
            np.clip(np.rint(image * 255.0), 0, 255).astype(np.uint8)
        )
    else:
        raise ValueError(f"unsupported image dtype {input_dtype}")

    height, width, channels = work.shape
    mask_bytes = _prepare_mask(np.asarray(mask), (height, width))

    image_buf = _as_buffer(work)
    mask_buf = _as_buffer(mask_bytes)

    if progress is not None:
        def _trampoline(percent, _context):
            progress(min(int(percent), 100))
        callback = _PROGRESS_FUNC(_trampoline)
    else:
        # Engine invokes the callback unconditionally: pass a no-op, not NULL
        callback = _PROGRESS_FUNC(lambda p, c: None)

    token = cancel if cancel is not None else CancelToken()

    params_ref = ctypes.byref(params) if params is not None else None

    if corpus_mask is not None:
        corpus_bytes = _prepare_mask(np.asarray(corpus_mask), (height, width))
        corpus_buf = _as_buffer(corpus_bytes)
        code = lib.imageSynth2(
            ctypes.byref(image_buf), ctypes.byref(mask_buf), ctypes.byref(corpus_buf),
            _CHANNELS_TO_FORMAT[channels], params_ref,
            callback, None, ctypes.byref(token._flag),
        )
    else:
        code = lib.imageSynth(
            ctypes.byref(image_buf), ctypes.byref(mask_buf),
            _CHANNELS_TO_FORMAT[channels], params_ref,
            callback, None, ctypes.byref(token._flag),
        )

    if code != 0:
        raise ResynthError(code)

    if input_dtype == np.uint8:
        return work
    return (work.astype(input_dtype) / np.array(255.0, dtype=input_dtype))
