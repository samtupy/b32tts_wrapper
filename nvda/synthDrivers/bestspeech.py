import os
from synthDriverHandler import SynthDriver, synthIndexReached, synthDoneSpeaking
from speech.commands import IndexCommand, PitchCommand, CharacterModeCommand
import ctypes
from ctypes import c_char_p, c_void_p, c_long, c_wchar_p, byref, POINTER, CFUNCTYPE
import nvwave
import config
import winUser
from speech.commands import IndexCommand
from autoSettingsUtils.driverSetting import DriverSetting, BooleanDriverSetting
from autoSettingsUtils.utils import StringParameterInfo
import re
import time
import queue
import threading
from logHandler import log

minRate = 200
maxRate = -90
minPitch = 43
maxPitch = 230
minInflection = -150
maxInflection = 150
minVolume = -68
maxVolume = 12

GAIN_SETTING = 258

bst_async_callback = CFUNCTYPE(c_long, c_void_p, c_long, c_void_p)

# The BGThread from espeak
class BgThread(threading.Thread):
	def __init__(self):
		super().__init__(name=f"{self.__class__.__module__}.{self.__class__.__qualname__}")
		self.daemon = True

	def run(self):
		global isSpeaking
		while True:
			func, args, kwargs = bgQueue.get()
			if not func:
				break
			try:
				func(*args, **kwargs)
			except:  # noqa: E722
				log.error("Error running function from queue", exc_info=True)
			bgQueue.task_done()


def _execWhenDone(func, *args, mustBeAsync=False, **kwargs):
	global bgQueue
	if mustBeAsync or bgQueue.unfinished_tasks != 0:
		# Either this operation must be asynchronous or There is still an operation in progress.
		# Therefore, run this asynchronously in the background thread.
		bgQueue.put((func, args, kwargs))
	else:
		func(*args, **kwargs)

class SynthDriver(SynthDriver):
	name = 'bestspeech'
	description = 'Bestspeech'
	supportedSettings = (SynthDriver.RateSetting(), SynthDriver.PitchSetting(), SynthDriver.InflectionSetting(), SynthDriver.VolumeSetting(), DriverSetting("headsize", "&Headsize", defaultVal="1", availableInSettingsRing=True), DriverSetting("excitation", "&Excitation", defaultVal="0", availableInSettingsRing=True), BooleanDriverSetting("numberProcessing", "&Number Processing", defaultVal=False), BooleanDriverSetting("abbreviations", "&Abbreviations", defaultVal=True), BooleanDriverSetting("phrasePrediction", "&Phrase Prediction", defaultVal=True))
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}
	supportedCommands = {PitchCommand, CharacterModeCommand, IndexCommand}

	@classmethod
	def check(cls):
		return True

	def __init__(self):
		super().__init__()
		path = os.path.join(os.path.dirname(__file__), 'b32_tts.dll')
		wrapper_path = os.path.join(os.path.dirname(__file__), 'b32_wrapper.dll')
		self.dll = ctypes.cdll[wrapper_path]
		self.player = None
		try:
			currentSoundcardOutput = config.conf['speech']['outputDevice']
		except:
			currentSoundcardOutput = config.conf["audio"]["outputDevice"]
		self.player = nvwave.WavePlayer(1, 11025, 16, outputDevice=currentSoundcardOutput)
		self.dll.bst_init_w.argtypes = (ctypes.c_wchar_p,)
		self.dll.bst_init_w.restype = c_void_p
		self.dll.bst_free.argtypes = (c_void_p,)
		self.dll.bst_speak.argtypes = (c_void_p, POINTER(c_long), c_char_p, c_long, c_long, c_long)
		self.dll.bst_speak.restype = c_void_p
		self.dll.bst_speech_free.argtypes = (c_void_p,)
		self.handle = self.dll.bst_init_w(path)
		global bgQueue
		bgQueue = queue.Queue()
		self.bgThread = BgThread()
		self.bgThread.start()
		self.rate = 90
		self.pitch = self._paramToPercent(80, minPitch, maxPitch)
		self.inflection = self._paramToPercent(0, minInflection, maxInflection)
		self.volume = self._paramToPercent(0, minVolume, maxVolume)
		self.headsize = "1"
		self.excitation = "0"
		self.numberProcessing = False
		self.abbreviations = True
		self._phrasePrediction = True
		self.table = str.maketrans("’", "'")
		self.canceled = False

	def _set_rate(self, vl):
		self._rate = self._percentToParam(vl,minRate,maxRate)

	def _get_rate(self):
		return self._paramToPercent(self._rate, minRate, maxRate)

	def _set_pitch(self, vl):
		self._pitch = self._percentToParam(vl,minPitch,maxPitch)

	def _get_pitch(self):
		return self._paramToPercent(self._pitch, minPitch, maxPitch)

	def _set_volume(self, vl):
		self._volume = self._percentToParam(vl,minVolume,maxVolume)

	def _get_volume(self):
		return self._paramToPercent(self._volume, minVolume, maxVolume)

	def _set_inflection(self, vl):
		self._inflection = self._percentToParam(vl,minInflection,maxInflection)

	def _get_inflection(self):
		return self._paramToPercent(self._inflection, minInflection, maxInflection)

	def _set_headsize(self, vl):
		n = int(vl)
		self._headsize = vl if n > -1 and n < 7 else 1

	def _get_headsize(self):
		return self._headsize

	def _get_availableHeadsizes(self):
		return { str(i): StringParameterInfo(str(i), str(i)) for i in range(1, 7)}

	def _set_excitation(self, vl):
		n = int(vl)
		self._excitation = vl if n > -1 and n < 7 else 1

	def _get_excitation(self):
		return self._excitation

	def _get_availableExcitations(self):
		return { str(i): StringParameterInfo(str(i), str(i)) for i in range(7)}

	def _set_numberProcessing(self, val):
		self._numberProcessing = bool(val)

	def _get_numberProcessing(self):
		return self._numberProcessing

	def _set_abbreviations(self, val):
		self._abbreviations = bool(val)

	def _get_abbreviations(self):
		return self._abbreviations

	def _set_phrasePrediction(self, val):
		self._phrasePrediction = bool(val)

	def _get_phrasePrediction(self):
		return self._phrasePrediction

	def _formatNumbers(self, text):
		def replace_num(m):
			num_str = m.group(0)
			return format(int(num_str), ",")
		return re.sub(r"\b\d{5,}\b", replace_num, text)

	def speak(self, speechSequence):
		lst = ["~n10,0]" if self._abbreviations else "~n10,1]", "~~1,0]" if self._phrasePrediction else "~~1,1]"]
		
		idx = []
		char_mode_on = pitch_modified = False
		for item in speechSequence:
			if isinstance(item, str):
				lst.append(item)
				if char_mode_on:
					lst.append("~n1,0]")
					char_mode_on = False
				if pitch_modified:
					lst.append(f"~f{self._pitch}]")
					pitch_modified = False
			elif isinstance(item, IndexCommand):
				idx.append(item.index)
			elif isinstance(item,CharacterModeCommand):
				char_mode_on = bool(item.state)
				lst.append("~n1,1]" if char_mode_on else "~n1,0]")
			elif isinstance(item,PitchCommand):
				try: multiplier = item.multiplier
				except ZeroDevisionError: multiplier = 1
				f = int(self._pitch * multiplier)
				lst.append(f"~f{f}]")
		text = " ".join(lst)
		if self._numberProcessing: text = self._formatNumbers(text)
		text = f"~r{self._rate}]~e{self._excitation}]~v{self.headsize}]~f{self._pitch}]~g{self._volume}]~h{self._inflection}]{text} ~|"
		_execWhenDone(self._speakBg, text, idx, mustBeAsync=True)

	def _speakBg(self, text, idx):
		@bst_async_callback
		def on_audio(data, size, user):
			if not self.speaking: return False
			self.player.feed(data, size)
			return True
		self.speaking = True
		txt = text.translate(self.table).encode('windows-1252', 'replace')
		self.dll.bst_speak_async(self.handle, on_audio, None, txt, -1, 0, 0)
		if not self.speaking: return
		f = lambda idx=idx: self.done(idx)
		self.player.feed(b"", 0, onDone=f)
		self.player.idle()

	def done(self, idx):
		for i in idx:
			synthIndexReached.notify(synth=self, index=i)
		synthDoneSpeaking.notify(synth=self)

	def terminate(self):
		self.cancel()
		bgQueue.put((None, None, None))
		self.bgThread.join()
		self.dll.bst_free(self.handle)

	def cancel(self):
		self.speaking = False
		while True:
			try:
				item = bgQueue.get_nowait()
			except queue.Empty:
				break
		if self.player:
			self.player.stop()

	def pause(self, switch):
		if self.player: self.player.pause(switch)
