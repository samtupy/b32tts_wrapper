import os
from synthDriverHandler import SynthDriver, synthIndexReached, synthDoneSpeaking
import ctypes
from ctypes import c_char_p, c_void_p, c_long, c_wchar_p, byref, POINTER, CFUNCTYPE
import nvwave
import config
import winUser
from speech.commands import IndexCommand
import time
import queue
import threading
from logHandler import log

minRate = 200
maxRate = -90

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
	supportedSettings = (SynthDriver.RateSetting(), SynthDriver.PitchSetting(), SynthDriver.InflectionSetting())
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}

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
		self.table = str.maketrans("’", "'")
		self.canceled = False

	def _set_rate(self, vl):
		self.dt_rate = self._percentToParam(vl,minRate,maxRate)

	def _get_rate(self):
		return self._paramToPercent(self.dt_rate, minRate, maxRate)

	def speak(self, speechSequence):
		lst = []
		idx = []
		for item in speechSequence:
			if isinstance(item, str):
				lst.append(item)
			elif isinstance(item, IndexCommand):
				idx.append(item.index)
		text = " ".join(lst)
		text = f"~r{self.dt_rate}]{text} ~|"
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
