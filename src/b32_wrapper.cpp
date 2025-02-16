// Wrapper around Bestspeak/keynote gold to support speaking to a memory buffer instead of waveout. Various constants/parameters/etc are taken from @rommix0's bst.h.
// This is released into the public domain.

#include <stdio.h> // _snprintf
#include <windows.h>
#include "b32_wrapper.h"
#include "MinHook.h"
#define riffheader_impl
#include "riffheader.h"

// Settings for BST parameters
#define BST_RATE_SETTING 257
#define BST_GAIN_SETTING  258
#define BST_BIT_DEPTH_SETTING 4097

// Voice data collected from BST.h made by @rommix0.
// This is split into pairs of 3 strings. The voice name, it's prefix text, and it's second prefix text? @rommix0 why are these separated?
const char* bst_voice_data[] = {
	"Fred", "~v0]~e3]~h0]~u0]~f80]", "~r0]",
	"Sara", "~v2]~e3]~h-20]~u0]~f175]", "~r0]",
	"Hary", "~v3]~e3]~h10]~u0]~f65]", "~r5]",
	"Wendy", "~v2]~e1]~h50]~u0]~f150]", "~r-5]",
	"Dexter", "~v6]~e6]~h0]~u-25]~f90]", "~r7]",
	"Alien", "~v4]~e6]~h-50]~u-20]~f115]", "~r-20]",
	"Kit", "~v5]~e3]~h40]~u0]~f230]", "~r-10]",
	"Bruno", "~v3]~e3]~h50]~u0]~f60]", "~r8]",
	"Ghost", "~v3]~e2]~h50]~u0]~f60]", "~r8]",
	"Peeper", "~v2]~e2]~h0]~u5]~f80]", "~r0]",
	"Dracula", "~v3]~e3]~h45]~u-5]~f47]", "~r10]",
	"Granny", "~v4]~e3]~h-60]~u0]~f350]", "~r20]",
	"Martha", "~v6]~e4]~h100]~u-5]~f300]", "~r-10]",
	"Tim", "~v3]~e4]~h-10]~u0]~f60]", "~r-10]",
	nullptr, nullptr, nullptr
};
int bst_voice_count = 0; // Will be initialized upon first call to bst_voices or bst_init.
const char** bst_voices_buf = nullptr; // Contains pointers to voice names (populated in bst_voices call).

// Function typedefs. First starting with the bestspeak definitions collected by Rommix, then moving on to the waveout definitions.
typedef int  (__cdecl *bstCreateFunc)(long*&);
typedef int  (__cdecl *TtsWavFunc)(long*, void*, const char*);
typedef void (__cdecl *bstRelBufFunc)(long*);
typedef void (__cdecl *bstCloseFunc)(long*);
typedef void (__cdecl *bstDestroyFunc)();
typedef void (__cdecl *bstSetParamsFunc)(long*, int, int);
typedef void (__cdecl *bstGetParamsFunc)(long*, int, int*);
typedef MMRESULT  (WINAPI *waveOutOpenFunc)(LPHWAVEOUT, UINT, LPCWAVEFORMATEX, DWORD_PTR, DWORD_PTR, DWORD);
waveOutOpenFunc waveOutOpenProc;
typedef MMRESULT  (WINAPI *waveOutHeaderFunc)(HWAVEOUT, WAVEHDR*, UINT);
waveOutHeaderFunc waveOutPrepareHeaderProc;
waveOutHeaderFunc waveOutWriteProc;
waveOutHeaderFunc waveOutUnprepareHeaderProc;
typedef MMRESULT  (WINAPI *waveOutResetFunc)(HWAVEOUT);
waveOutResetFunc waveOutResetProc;
waveOutResetFunc waveOutCloseProc;

// This structure contains all state information required to use bestspeak, including the dll module handle, required bst function pointers and the bestspeak handle itself.
struct bst_state {
	HMODULE dll;
	long* tts;
	bstCreateFunc bstCreate;
	TtsWavFunc TtsWav;
	bstRelBufFunc bstRelBuf;
	bstCloseFunc bstClose;
	bstDestroyFunc bstDestroy;
	bstSetParamsFunc bstSetParams;
	bstGetParamsFunc bstGetParams;
	bst_async_callback async_callback;
	void* async_callback_user;
	bool async_stop_speaking;
	char* audio;
	long audio_size;
};

bool winmm_hooked = false;
thread_local bst_state* winmm_hooked_state = nullptr; // Insure that we can passthrough any hooked waveout calls that come through on a thread other than the one we are working on while providing global access to the speech state within hooks (SetTimer can't cary state information).

// If bstRelBuf is not called from a windows message loop the same number of times that waveOutWrite is called, TtsWav will never return!
void WINAPI on_rel_buf(HWND hwnd, UINT msg, UINT_PTR timer_id, DWORD curtime) {
	KillTimer(hwnd, timer_id);
	winmm_hooked_state->bstRelBuf(winmm_hooked_state->tts);
}

// Actual waveout hooks, most of these are no-ops/passthroughs accept for open and write. The no-ops must still exist to insure that no erroneous function calls reach the WinMM API.
MMRESULT WINAPI waveOutOpenHook(LPHWAVEOUT outptr, UINT device, LPCWAVEFORMATEX format, DWORD_PTR callback, DWORD_PTR instance, DWORD flags) {
	if (!winmm_hooked_state) return waveOutOpenProc(outptr, device, format, callback, instance, flags);
	*outptr = (HWAVEOUT)callback; // Now all other hooks will receive state information in their first parameter, though we prefer to use winmm_hooked_state. This also makes sure our hook returns what the calling function is expecting.
	if (!winmm_hooked_state->audio && !winmm_hooked_state->async_callback) {
		winmm_hooked_state->audio = (char*)malloc(winmm_hooked_state->audio_size);
		make_wav_header_in_place((wav_header*)winmm_hooked_state->audio, 0, format->nSamplesPerSec, format->wBitsPerSample, format->nChannels, format->wFormatTag);
	}
	return MMSYSERR_NOERROR;
}
MMRESULT WINAPI waveOutPrepareHeaderHook(HWAVEOUT ptr, WAVEHDR* header, UINT size) {
	if (!winmm_hooked_state) return waveOutPrepareHeaderProc(ptr, header, size);
	return MMSYSERR_NOERROR;
}
MMRESULT WINAPI waveOutWriteHook(HWAVEOUT ptr, WAVEHDR* header, UINT size) {
	if (!winmm_hooked_state) return waveOutWriteProc(ptr, header, size);
	SetTimer(nullptr, 0, USER_TIMER_MINIMUM, on_rel_buf);
	if (winmm_hooked_state->async_stop_speaking) return MMSYSERR_NOERROR; // Callback returned false, drop all remaining buffers.
	if (winmm_hooked_state->async_callback) {
		if (!winmm_hooked_state->async_callback(header->lpData, header->dwBufferLength, winmm_hooked_state->async_callback_user)) {
			winmm_hooked_state->async_stop_speaking = true;
			return MMSYSERR_NOERROR;
		}
	} else {
		winmm_hooked_state->audio = (char*)realloc(winmm_hooked_state->audio, winmm_hooked_state->audio_size + header->dwBufferLength);
		memcpy(winmm_hooked_state->audio + winmm_hooked_state->audio_size, header->lpData, header->dwBufferLength);
		winmm_hooked_state->audio_size += header->dwBufferLength;
	}
	return MMSYSERR_NOERROR;
}
MMRESULT WINAPI waveOutUnprepareHeaderHook(HWAVEOUT ptr, WAVEHDR* header, UINT size) {
	if (!winmm_hooked_state) return waveOutUnprepareHeaderProc(ptr, header, size);
	return MMSYSERR_NOERROR;
}
MMRESULT WINAPI waveOutResetHook(HWAVEOUT ptr) {
	if (!winmm_hooked_state) return waveOutResetProc(ptr);
	return MMSYSERR_NOERROR;
}
MMRESULT WINAPI waveOutCloseHook(HWAVEOUT ptr) {
	if (!winmm_hooked_state) return waveOutCloseProc(ptr);
	return MMSYSERR_NOERROR;
}

void winmm_hook() {
	if (winmm_hooked) return;
	MH_Initialize();
	MH_CreateHook(waveOutOpen, waveOutOpenHook, (LPVOID*)&waveOutOpenProc);
	MH_CreateHook(waveOutPrepareHeader, waveOutPrepareHeaderHook, (LPVOID*)&waveOutPrepareHeaderProc);
	MH_CreateHook(waveOutWrite, waveOutWriteHook, (LPVOID*)&waveOutWriteProc);
	MH_CreateHook(waveOutUnprepareHeader, waveOutUnprepareHeaderHook, (LPVOID*)&waveOutUnprepareHeaderProc);
	MH_CreateHook(waveOutReset, waveOutResetHook, (LPVOID*)&waveOutResetProc);
	MH_CreateHook(waveOutClose, waveOutCloseHook, (LPVOID*)&waveOutCloseProc);
	winmm_hooked = TRUE;
}

inline void voices_count() {
	if (!bst_voice_count) {
		for (int i = 0; bst_voice_data[i]; i += 3) bst_voice_count++;
	}
}

// Finally the public library interface itself.
b32w_export const char** bst_voices(int* count) {
	voices_count();
	if (!bst_voices_buf) bst_voices_buf = (const char**) malloc(sizeof(const char*) * (bst_voice_count + 1));
	for (int i = 0; i < bst_voice_count; i++) bst_voices_buf[i] = bst_voice_data[i * 3];
	bst_voices_buf[bst_voice_count] = nullptr;
	if (count) *count = bst_voice_count;
	return bst_voices_buf;
}
inline bst_state* bst_init_from_hmodule(HMODULE hmod) {
	if (!hmod) return nullptr;
	voices_count();
	bst_state* s = (bst_state*)malloc(sizeof(bst_state));
	s->dll = hmod;
	s->bstCreate = (bstCreateFunc)GetProcAddress(s->dll, "bstCreate");
	s->TtsWav = (TtsWavFunc)GetProcAddress(s->dll, "TtsWav");
	s->bstRelBuf = (bstRelBufFunc)GetProcAddress(s->dll, "bstRelBuf");
	s->bstDestroy = (bstDestroyFunc)GetProcAddress(s->dll, "bstDestroy");
	s->bstClose = (bstCloseFunc)GetProcAddress(s->dll, "bstClose");
	s->bstSetParams = (bstSetParamsFunc)GetProcAddress(s->dll, "bstSetParams");
	s->bstGetParams = (bstGetParamsFunc)GetProcAddress(s->dll, "bstGetParams");
	s->audio = nullptr;
	if (!s->bstCreate || !s->TtsWav || s->bstCreate(s->tts)) {
		FreeLibrary(s->dll);
		free(s);
		return nullptr;
	}
	s->bstSetParams(s->tts, BST_BIT_DEPTH_SETTING, 16);
	return s;
}
b32w_export bst_state* bst_init(const char* module_path) {
	return bst_init_from_hmodule(LoadLibraryA(module_path));
}
b32w_export bst_state* bst_init_w(const wchar_t* module_path) {
	return bst_init_from_hmodule(LoadLibraryW(module_path));
}
b32w_export void bst_free(bst_state* s) {
	if (!s) return;
	s->bstClose(s->tts);
	s->bstDestroy();
	FreeLibrary(s->dll);
	free(s);
}
inline void bst_speak_internal(bst_state* s, const char* text, int voice, int rate, int gain) {
	if (voice >= 0 && voice < bst_voice_count) { // prepend voice prefixes
		int text_len = strlen(bst_voice_data[voice * 3 + 1]) + strlen(bst_voice_data[voice * 3 + 2]) + strlen(text) + 1;
		char* actual_text = (char*)malloc(text_len);
		_snprintf(actual_text, text_len, "%s%s%s", bst_voice_data[voice * 3 + 1], bst_voice_data[voice * 3 + 2], text);
		text = actual_text;
	}
	s->bstSetParams(s->tts, BST_RATE_SETTING, rate * -1); // Bestspeech interprets lower numbers as faster rates.
	s->bstSetParams(s->tts, BST_GAIN_SETTING, gain);
	winmm_hook();
	winmm_hooked_state = s;
	MH_EnableHook(MH_ALL_HOOKS);
	s->TtsWav(s->tts, s, text);
	MH_DisableHook(MH_ALL_HOOKS);
	winmm_hooked_state = nullptr;
	if (voice >= 0 && voice < bst_voice_count) free((void*)text); // We've allocated a custom string in this case.
}
b32w_export char* bst_speak(bst_state* s, long* size, const char* text, int voice, int rate, int gain, bool pcm_header) {
	if (!s || !text) return nullptr;
	s->audio_size = sizeof(wav_header);
	s->async_callback = nullptr;
	s->async_stop_speaking = false;
	bst_speak_internal(s, text, voice, rate, gain);
	wav_header* h = (wav_header*)s->audio; // We must correct filesize here.
	h->wav_size = s->audio_size - 8;
	h->data_bytes = s->audio_size - sizeof(wav_header);
	if (!pcm_header) s->audio_size -= sizeof(wav_header);
	if (size) *size = s->audio_size;
	char* data = s->audio;
	s->audio = nullptr; // Will be allocated upon next speech segment.
	return pcm_header? data : data + sizeof(wav_header);
}
b32w_export void bst_speak_async(bst_state* s, bst_async_callback callback, void* user, const char* text, int voice, int rate, int gain) {
	if (!s || !text) return;
	s->audio = nullptr;
	s->async_callback = callback;
	s->async_callback_user = user;
	s->async_stop_speaking = false;
	bst_speak_internal(s, text, voice, rate, gain);
}
b32w_export void bst_speech_free(char* data) {
	free(data);
}
