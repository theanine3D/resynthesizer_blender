
/*
Types for GUI progress callbacks.
*/

// PATCHED for Blender port: pthread.h only exists/matters for threaded builds;
// upstream included it unconditionally, breaking strict non-POSIX toolchains.
#if defined(SYNTH_THREADED) && !defined(SYNTH_USE_GLIB_THREADS)
	  #include <pthread.h>
#endif
#include "passes.h"


struct ProgressRecord {
  guint estimatedPixelCountToCompletion;
  guint completedPixelCount;
  guint priorReportedPercentComplete;

  void (*progressCallback)(int, void*);    // callback upstream to caller
  void * context;                          // opaque data params to caller

#ifdef SYNTH_THREADED
  // mutually exclude threads over certain other fields of struct
#ifdef SYNTH_USE_GLIB_THREADS
  GMutex *mutexProgress;
#else
  pthread_mutex_t *mutexProgress;
#endif
#endif
};

typedef struct ProgressRecord ProgressRecordT;

void deepProgressCallback(ProgressRecordT*);

void initializeProgressRecord(
     ProgressRecordT* progressRecord,
     TRepetionParameters repetitionParams,
     void (*progressCallback)(int, void*),
     void * contextInfo);

// PATCHED for Blender port: threaded declarations guarded like their
// definitions in progress.c, and upstream's stray ';' inside the parameter
// list (a syntax error under clang) removed.
#ifdef SYNTH_THREADED
void deepProgressCallbackThreaded(ProgressRecordT*);

void initializeThreadedProgressRecord(
     ProgressRecordT* progressRecord,
     TRepetionParameters repetitionParams,
     void (*progressCallback)(int, void*),
     void * contextInfo,
#ifdef SYNTH_USE_GLIB_THREADS
     GMutex *mutexProgress
#else
     pthread_mutex_t *mutexProgress
#endif
);
#endif
     

