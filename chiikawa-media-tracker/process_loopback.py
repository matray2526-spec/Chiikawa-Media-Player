from __future__ import annotations

import ctypes
import threading
import time
from ctypes import POINTER, Structure, Union, byref, c_int, c_ubyte, c_ushort, c_ulong, c_ulonglong, c_void_p, cast, sizeof
from typing import Callable

import numpy as np
from comtypes import COINIT_MULTITHREADED, COMMETHOD, COMObject, CoInitializeEx, CoUninitialize, GUID, HRESULT, IUnknown
from pycaw.api.audioclient import IAudioClient, WAVEFORMATEX


_VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
_AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
_PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x2


class _ProcessLoopbackParams(Structure):
    _fields_ = [("TargetProcessId", c_ulong), ("ProcessLoopbackMode", c_int)]


class _AudioClientActivationParams(Structure):
    _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", _ProcessLoopbackParams)]


class _Blob(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", POINTER(c_ubyte))]


class _PropVariantData(Union):
    _fields_ = [("blob", _Blob), ("pointer", c_void_p)]


class _PropVariant(Structure):
    _anonymous_ = ("data",)
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("data", _PropVariantData),
    ]


class _IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "GetBuffer",
            (['out'], POINTER(POINTER(c_ubyte)), "data"),
            (['out'], POINTER(c_ulong), "frames"),
            (['out'], POINTER(c_ulong), "flags"),
            (['out'], POINTER(c_ulonglong), "device_position"),
            (['out'], POINTER(c_ulonglong), "qpc_position"),
        ),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (['in'], c_ulong, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (['out'], POINTER(c_ulong), "frames")),
    ]


class _IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "GetActivateResult",
            (['out'], POINTER(HRESULT), "activation_result"),
            (['out'], POINTER(POINTER(IUnknown)), "activated_interface"),
        )
    ]


class _IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "ActivateCompleted",
            (['in'], POINTER(_IActivateAudioInterfaceAsyncOperation), "operation"),
        )
    ]


class _IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class _ActivationHandler(COMObject):
    _com_interfaces_ = [_IActivateAudioInterfaceCompletionHandler, _IAgileObject]

    def __init__(self) -> None:
        super().__init__()
        self.completed = threading.Event()
        self.audio_client: IAudioClient | None = None
        self.error: Exception | None = None

    def ActivateCompleted(self, operation: _IActivateAudioInterfaceAsyncOperation) -> None:
        try:
            activation_result, activated_interface = operation.GetActivateResult()
            if activation_result < 0:
                raise OSError(f"Process-loopback activation failed: 0x{activation_result & 0xFFFFFFFF:08X}")
            self.audio_client = activated_interface.QueryInterface(IAudioClient)
        except Exception as error:
            self.error = error
        finally:
            self.completed.set()


def _activate_process_client(process_id: int, stop_event: threading.Event) -> IAudioClient:
    params = _AudioClientActivationParams(
        _AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        _ProcessLoopbackParams(process_id, _PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE),
    )
    activation = _PropVariant()
    activation.vt = 65
    activation.blob.cbSize = sizeof(params)
    activation.blob.pBlobData = cast(byref(params), POINTER(c_ubyte))

    handler = _ActivationHandler()
    operation = POINTER(_IActivateAudioInterfaceAsyncOperation)()
    activate = ctypes.WinDLL("Mmdevapi.dll").ActivateAudioInterfaceAsync
    activate.restype = HRESULT
    activate.argtypes = [
        ctypes.c_wchar_p,
        POINTER(GUID),
        POINTER(_PropVariant),
        POINTER(_IActivateAudioInterfaceCompletionHandler),
        POINTER(POINTER(_IActivateAudioInterfaceAsyncOperation)),
    ]
    interface_id = IAudioClient._iid_
    result = activate(
        _VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        byref(interface_id),
        byref(activation),
        handler,
        byref(operation),
    )
    if result < 0:
        raise OSError(f"Could not request process-loopback capture: 0x{result & 0xFFFFFFFF:08X}")
    if not handler.completed.wait(timeout=10) or stop_event.is_set():
        raise TimeoutError("Timed out while starting app audio capture.")
    if handler.error:
        raise handler.error
    if handler.audio_client is None:
        raise RuntimeError("Windows did not return an audio capture client.")
    return handler.audio_client


def capture_process_audio(
    process_id: int,
    stop_event: threading.Event,
    on_audio: Callable[[np.ndarray], None],
) -> None:
    """Capture 44.1 kHz PCM from a process and all its child processes."""
    CoInitializeEx(COINIT_MULTITHREADED)
    audio_client: IAudioClient | None = None
    try:
        audio_client = _activate_process_client(process_id, stop_event)
        wave_format = WAVEFORMATEX()
        wave_format.wFormatTag = 1
        wave_format.nChannels = 2
        wave_format.nSamplesPerSec = 44100
        wave_format.wBitsPerSample = 16
        wave_format.nBlockAlign = 4
        wave_format.nAvgBytesPerSec = 176400
        wave_format.cbSize = 0
        stream_flags = _AUDCLNT_STREAMFLAGS_LOOPBACK | _AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
        audio_client.Initialize(0, stream_flags, 0, 0, byref(wave_format), None)

        capture_unknown = audio_client.GetService(byref(_IAudioCaptureClient._iid_))
        capture_client = capture_unknown.QueryInterface(_IAudioCaptureClient)
        audio_client.Start()

        while not stop_event.is_set():
            frames = capture_client.GetNextPacketSize()
            if not frames:
                stop_event.wait(0.004)
                continue

            data, frame_count, flags, _device_position, _qpc_position = capture_client.GetBuffer()
            try:
                if flags & _AUDCLNT_BUFFERFLAGS_SILENT:
                    samples = np.zeros((frame_count, 2), dtype=np.float32)
                else:
                    sample_count = frame_count * 2
                    pcm_pointer = cast(data, POINTER(ctypes.c_int16 * sample_count))
                    samples = np.ctypeslib.as_array(pcm_pointer.contents).reshape(-1, 2).astype(np.float32) / 32768
                on_audio(samples)
            finally:
                capture_client.ReleaseBuffer(frame_count)
    finally:
        if audio_client is not None:
            try:
                audio_client.Stop()
            except OSError:
                pass
        CoUninitialize()