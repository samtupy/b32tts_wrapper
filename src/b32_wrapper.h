#ifndef b32w_export
#ifdef __cplusplus
#define b32w_export extern "C" __declspec(dllexport)
#else
#define b32w_export extern __declspec(dllexport)
#endif
#endif

struct bst_state;
typedef bool (*bst_async_callback)(char* data, long size, void* user);

b32w_export const char** bst_voices(int* count = nullptr);
b32w_export bst_state* bst_init(const char* module_path = "b32_tts.dll");
b32w_export bst_state* bst_init_w(const wchar_t* module_path = L"b32_tts.dll");
b32w_export void bst_free(bst_state* s);
b32w_export char* bst_speak(bst_state* s, long* size, const char* text, int voice = 0, int rate = 0, int gain = 0, bool pcm_header = true); // Make sure to free return values with bst_speech_free.
b32w_export void bst_speak_async(bst_state* s, bst_async_callback callback, void* user, const char* text, int voice = 0, int rate = 0, int gain = 0);
b32w_export void bst_speech_free(char* data);
