// Generate 250 wav files right next to each other very quickly as an attempt to reproduce the NVDA addon squealing issue. Can we use b32tts_wrapper to write a better addon?

#include "b32_wrapper.h"
#include <stdio.h>
#include <windows.h>

void synth(bst_state* s, int n) {
	long size;
	char fn[64];
	_snprintf(fn, 64, "output\\%d.wav", n);
	char* data = bst_speak(s, &size, "this is a test");
	FILE* f = fopen(fn, "wb");
	fwrite(data, sizeof(char), size, f);
	fclose(f);
	free(data);
}
int main() {
	CreateDirectoryW(L"output", nullptr);
	bst_state* s = bst_init("b32_tts.dll");
	for (int i = 0; i < 250; i++) synth(s, i);
	bst_free(s);
}
