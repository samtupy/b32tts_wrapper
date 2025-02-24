// A command line frontend to bestspeak that allows synthesis to an audio file.

#include <stdio.h>
#include <windows.h>
#include "argparse.h"
#include "b32_wrapper.h"

int main(int argc, const char** argv) {
	const char *voice = nullptr, *text = nullptr, *outpath = nullptr;
	int rate = 0;
	const char* const usages[] = {"bspk [options]", nullptr};
	argparse_option options[] = {
		OPT_HELP(),
		OPT_STRING('f', "filename", &outpath, "output wav filename", nullptr, 0, 0),
		OPT_STRING('v', "voice", &voice, "voice to speak with (? to list)", nullptr, 0, 0),
		OPT_STRING('t', "text", &text, "text to speak", nullptr, 0, 0),
		OPT_INTEGER('r', "rate", &rate, "speech rate", nullptr, 0, 0),
		OPT_END()
	};
	argparse argparse;
	argparse_init(&argparse, options, usages, 0);
	argparse_describe(&argparse, "\nSpeak to a wav file with the bestspeech tts engine.", "");
	argc = argparse_parse(&argparse, argc, argv);
	int voice_count = 0, voice_idx = -1;
	const char** voices = bst_voices(&voice_count);
	if (voice && strcmp(voice, "?") == 0) {
		printf("%d total:\n", voice_count);
		for (int i = 0; voices[i]; i++) {
			printf("%s\n", voices[i]);
		}
		return 0;
	} else if(voice && voice[0]) {
		for (voice_idx = 0; voices[voice_idx] && stricmp(voices[voice_idx], voice) != 0; voice_idx++);
		if (voice_idx >= voice_count) {
			printf("Unable to find voice %s\n", voice);
			return 1;
		}
	}
	if (!text || !text[0]) {
		printf("No text provided, run bspk -h for help\n");
		return 1;
	}
	bst_state* tts = bst_init();
	if (!tts) {
		printf("error initializing text to speech system\n");
		return 1;
	}
	long bufsize;
	char* buf = bst_speak(tts, &bufsize, text, voice_idx, rate);
	bst_free(tts);
	if (!outpath || !outpath[0]) PlaySoundA((LPCSTR)buf, nullptr, SND_MEMORY | SND_SYNC);
	else {
		FILE* f = fopen(outpath, "wb");
		if(!f) {
			printf("error opening output file\n");
			bst_speech_free(buf);
			return 1;
		}
		fwrite(buf, sizeof(char), bufsize, f);
		fclose(f);
	}
	bst_speech_free(buf);
	return 0;
}
