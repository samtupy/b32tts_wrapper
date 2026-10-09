// b32_helper.cpp
// 32-bit out-of-process TTS helper, used by the NVDA addon when running under
// 64-bit NVDA (2026+). Launched as a subprocess with the b32_tts.dll path as
// its sole command-line argument.
//
// === stdin protocol (binary, little-endian) ===
//   SPEAK command  : [uint32 text_len (> 0)] [float32 rate_mult] [text_len bytes, windows-1252]
//   CANCEL command : [uint32 = 0]
//   QUIT command   : [uint32 = 0xFFFFFFFF]
//   EOF on stdin is treated the same as QUIT.
//
// === stdout protocol (binary, little-endian) ===
//   Startup ready    : [uint32 = 0x52545342 ("BSTR")]
//   Audio chunk    : [uint32 chunk_len (> 0)] [chunk_len bytes raw 16-bit mono PCM @ 11025 Hz]
//   End-of-utter.  : [uint32 = 0]
//   One end-of-utterance sentinel is emitted after every SPEAK command
//   (whether it completed normally or was cancelled).

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <atomic>
#include <fcntl.h>
#include <io.h>
#include <math.h>
#include <new>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "b32_wrapper.h"

// Advanced by the stdin thread to abort speech accepted in an older generation.
static std::atomic_uint32_t g_cancel_generation(0);
// Set to true when the helper should exit cleanly.
static std::atomic_bool g_quit(false);

// ---- Pending-command queue (capacity 1) ----------------------------------- //
// The stdin reader thread deposits a single pending speak command here.
// The main thread drains it before starting synthesis.

struct PendingCmd {
    char*    text;
    uint32_t text_len;
    float    rate_mult;
    uint32_t generation;
    bool     valid;
};

static PendingCmd        g_pending = {};
static CRITICAL_SECTION  g_pending_cs;
// Signalled when g_pending.valid becomes true, or g_quit becomes true.
static HANDLE            g_cmd_event;
static const uint32_t     MAX_TEXT_LENGTH = 16 * 1024 * 1024;
static const uint32_t     HELPER_READY = 0x52545342u;

// ---------------------------------------------------------------------------

struct AudioContext { uint32_t generation; };

static bool audio_cb(char* data, long size, void* user)
{
    const AudioContext* context = static_cast<const AudioContext*>(user);
    if (!context || g_quit || g_cancel_generation.load() != context->generation) return false;
    if (!data || size <= 0) return true;

    uint32_t len = (uint32_t)size;
    if (fwrite(&len, sizeof(uint32_t), 1, stdout) != 1) return false;
    if (fwrite(data, 1, (size_t)size, stdout) != (size_t)size) return false;
    fflush(stdout);
    return !g_quit && g_cancel_generation.load() == context->generation;
}

// Reads exactly `n` bytes from `fp` into `buf`.  Returns false on short read.
static bool read_exact(FILE* fp, void* buf, size_t n)
{
    size_t got = 0;
    while (got < n) {
        size_t r = fread((char*)buf + got, 1, n - got, fp);
        if (r == 0) return false;
        got += r;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Stdin reader thread: runs for the lifetime of the process.
// Parses incoming commands and either advances cancellation / quits, or enqueues a
// SPEAK command for the main thread.
// ---------------------------------------------------------------------------
static DWORD WINAPI stdin_reader(LPVOID /*unused*/)
{
    while (true) {
        if (g_quit) return 0;
        uint32_t text_len = 0;
        if (!read_exact(stdin, &text_len, sizeof(uint32_t))) {
            // EOF or read error -> quit
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }

        if (text_len == 0xFFFFFFFFu) {
            g_quit   = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }

        if (text_len == 0) {
            // Cancel: abort whatever synthesis is currently running.
            ++g_cancel_generation;
            continue;
        }

        if (text_len > MAX_TEXT_LENGTH) {
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }

        float rate_mult = 1.0f;
        if (!read_exact(stdin, &rate_mult, sizeof(float))) {
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }

        if (!isfinite(rate_mult) || rate_mult < 0.1f || rate_mult > 10.0f) {
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }

        char* text = new (std::nothrow) char[text_len + 1];
        if (!text) {
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }
        if (!read_exact(stdin, text, text_len)) {
            delete[] text;
            g_quit = true;
            ++g_cancel_generation;
            SetEvent(g_cmd_event);
            return 0;
        }
        text[text_len] = '\0';

        // Replace any unprocessed pending command (NVDA always cancels before
        // issuing a new speak, but guard just in case).
        EnterCriticalSection(&g_pending_cs);
        if (g_pending.valid) {
            delete[] g_pending.text;
        }
        g_pending.text     = text;
        g_pending.text_len = text_len;
        g_pending.rate_mult = rate_mult;
        g_pending.generation = g_cancel_generation.load();
        g_pending.valid    = true;
        LeaveCriticalSection(&g_pending_cs);

        SetEvent(g_cmd_event);
    }
}

// ---------------------------------------------------------------------------

int wmain(int argc, wchar_t** argv)
{
    // Switch stdin/stdout to binary mode to avoid newline translation.
    _setmode(_fileno(stdin),  _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    const wchar_t* dll_path = L"b32_tts.dll";
    if (argc >= 2) dll_path = argv[1];

    bst_state* state = bst_init_w(dll_path);
    if (!state) return 1;

    InitializeCriticalSection(&g_pending_cs);
    g_cmd_event = CreateEvent(NULL, /*manualReset=*/FALSE, /*initial=*/FALSE, NULL);
    if (!g_cmd_event) {
        bst_free(state);
        DeleteCriticalSection(&g_pending_cs);
        return 2;
    }
    HANDLE reader_thread = CreateThread(NULL, 0, stdin_reader, NULL, 0, NULL);
    if (!reader_thread) {
        bst_free(state);
        CloseHandle(g_cmd_event);
        DeleteCriticalSection(&g_pending_cs);
        return 3;
    }

    // Do not let NVDA accept a process that started but failed to initialize
    // the engine or its command reader.
    if (fwrite(&HELPER_READY, sizeof(HELPER_READY), 1, stdout) != 1 || fflush(stdout) != 0) {
        // Returning from the process also terminates the blocked stdin reader;
        // waiting for it here could itself hang when the parent pipe is open.
        return 4;
    }

    while (true) {
        WaitForSingleObject(g_cmd_event, INFINITE);
        if (g_quit) break;

        PendingCmd cmd = {};
        EnterCriticalSection(&g_pending_cs);
        if (g_pending.valid) {
            cmd            = g_pending;
            g_pending.valid = false;
            g_pending.text  = nullptr;
        }
        LeaveCriticalSection(&g_pending_cs);

        if (!cmd.valid) continue;

        if (g_quit) {
            delete[] cmd.text;
            break;
        }
        AudioContext context = {cmd.generation};
        if (g_cancel_generation.load() == cmd.generation) {
            bst_speak_async(state, audio_cb, &context, cmd.text, -1, 0, cmd.rate_mult, 0);
        }
        delete[] cmd.text;

        // Always emit end-of-utterance sentinel, even if cancelled.
        uint32_t zero = 0;
        if (fwrite(&zero, sizeof(uint32_t), 1, stdout) != 1 || fflush(stdout) != 0) {
            g_quit = true;
            break;
        }
    }

    // If stdout failed, the reader may still be blocked on an open stdin pipe.
    // Cancel that synchronous read so process shutdown cannot hang forever.
    ++g_cancel_generation;
    for (int attempt = 0; attempt < 20; ++attempt) {
        if (WaitForSingleObject(reader_thread, 100) == WAIT_OBJECT_0) break;
        CancelSynchronousIo(reader_thread);
        CancelIoEx((HANDLE)_get_osfhandle(_fileno(stdin)), NULL);
    }
    if (WaitForSingleObject(reader_thread, 0) != WAIT_OBJECT_0) {
        TerminateThread(reader_thread, 1);
        WaitForSingleObject(reader_thread, 1000);
    }
    CloseHandle(reader_thread);
    EnterCriticalSection(&g_pending_cs);
    if (g_pending.valid) delete[] g_pending.text;
    g_pending = {};
    LeaveCriticalSection(&g_pending_cs);
    bst_free(state);
    DeleteCriticalSection(&g_pending_cs);
    CloseHandle(g_cmd_event);
    return 0;
}
