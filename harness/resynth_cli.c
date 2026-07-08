/*
CLI test harness for the standalone Resynthesizer engine (libimagesynth).

Verifies the engine works detached from GIMP, before any Blender integration.

Usage:
  resynth_cli <input.png> <output.png> --mask <mask.png>
  resynth_cli <input.png> <output.png> --rect <x> <y> <w> <h>

The mask (painted white = region to heal) may come from a grayscale PNG,
or a filled rectangle generated on the fly with --rect.
Writes <output>.mask.png alongside the output when --rect is used, for inspection.

Part of the Resynthesizer->Blender port. GPL2+, engine Copyright (C) Lloyd Konneker.
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

#include "imageSynth.h"

static void
progressCallback(int percent, void *context)
{
  (void)context;
  printf("\rSynthesizing: %d%%", percent);
  fflush(stdout);
}

static const char *
errorName(int code)
{
  switch (code)
  {
    case IMAGE_SYNTH_SUCCESS: return "success";
    case IMAGE_SYNTH_ERROR_INVALID_IMAGE_FORMAT: return "invalid image format";
    case IMAGE_SYNTH_ERROR_IMAGE_MASK_MISMATCH: return "image/mask size mismatch";
    case IMAGE_SYNTH_ERROR_PATCH_SIZE_EXCEEDED: return "patch size exceeded";
    case IMAGE_SYNTH_ERROR_MATCH_CONTEXT_TYPE_RANGE: return "matchContextType out of range";
    case IMAGE_SYNTH_ERROR_EMPTY_TARGET: return "empty target (mask selects nothing)";
    case IMAGE_SYNTH_ERROR_EMPTY_CORPUS: return "empty corpus (mask selects everything)";
    default: return "unknown error";
  }
}

int
main(int argc, char **argv)
{
  if (argc < 5)
  {
    fprintf(stderr,
      "Usage:\n"
      "  %s <input.png> <output.png> --mask <mask.png>\n"
      "  %s <input.png> <output.png> --rect <x> <y> <w> <h>\n",
      argv[0], argv[0]);
    return 2;
  }

  const char *inPath = argv[1];
  const char *outPath = argv[2];

  int width, height, channelsInFile;
  // Force RGBA: engine's T_RGBA path, simplest and matches Blender's layout
  unsigned char *pixels = stbi_load(inPath, &width, &height, &channelsInFile, 4);
  if (!pixels)
  {
    fprintf(stderr, "Failed to load image: %s\n", inPath);
    return 1;
  }
  printf("Loaded %s: %dx%d (%d channels in file, forced to RGBA)\n",
    inPath, width, height, channelsInFile);

  unsigned char *maskPixels = NULL;
  int wroteMaskDebug = 0;

  if (strcmp(argv[3], "--mask") == 0 && argc == 5)
  {
    int mw, mh, mc;
    maskPixels = stbi_load(argv[4], &mw, &mh, &mc, 1); // force grayscale
    if (!maskPixels)
    {
      fprintf(stderr, "Failed to load mask: %s\n", argv[4]);
      return 1;
    }
    if (mw != width || mh != height)
    {
      fprintf(stderr, "Mask size %dx%d does not match image %dx%d\n", mw, mh, width, height);
      return 1;
    }
    // Threshold to the engine's binary selection values
    for (int i = 0; i < width * height; i++)
      maskPixels[i] = (maskPixels[i] >= 128) ? 0xFF : 0x00;
  }
  else if (strcmp(argv[3], "--rect") == 0 && argc == 8)
  {
    int rx = atoi(argv[4]), ry = atoi(argv[5]);
    int rw = atoi(argv[6]), rh = atoi(argv[7]);
    maskPixels = calloc((size_t)width * height, 1);
    if (!maskPixels) { fprintf(stderr, "Out of memory\n"); return 1; }
    for (int y = ry; y < ry + rh && y < height; y++)
      for (int x = rx; x < rx + rw && x < width; x++)
        if (x >= 0 && y >= 0)
          maskPixels[y * width + x] = 0xFF;
    printf("Generated rect mask: %d,%d %dx%d\n", rx, ry, rw, rh);
    wroteMaskDebug = 1;
  }
  else
  {
    fprintf(stderr, "Bad arguments. Use --mask <file> or --rect <x> <y> <w> <h>\n");
    return 2;
  }

  ImageBuffer image = { pixels, (unsigned)width, (unsigned)height, (size_t)width * 4 };
  ImageBuffer mask  = { maskPixels, (unsigned)width, (unsigned)height, (size_t)width };

  int cancelFlag = 0;
  clock_t start = clock();

  // NULL parameters => engine defaults (matchContext, patchSize 30, 200 probes, etc.)
  int result = imageSynth(&image, &mask, T_RGBA, NULL,
                          progressCallback, NULL, &cancelFlag);

  double seconds = (double)(clock() - start) / CLOCKS_PER_SEC;
  printf("\nEngine returned: %d (%s) in %.2f s\n", result, errorName(result), seconds);

  if (result != IMAGE_SYNTH_SUCCESS)
    return 1;

  if (!stbi_write_png(outPath, width, height, 4, pixels, width * 4))
  {
    fprintf(stderr, "Failed to write output: %s\n", outPath);
    return 1;
  }
  printf("Wrote %s\n", outPath);

  if (wroteMaskDebug)
  {
    char maskOut[1024];
    snprintf(maskOut, sizeof maskOut, "%s.mask.png", outPath);
    stbi_write_png(maskOut, width, height, 1, maskPixels, width);
    printf("Wrote %s (debug mask)\n", maskOut);
  }

  stbi_image_free(pixels);
  free(maskPixels);
  return 0;
}
