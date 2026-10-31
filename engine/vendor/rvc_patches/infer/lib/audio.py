import librosa
import numpy as np
import av
from io import BytesIO


def wav2(i, o, format):
    inp = av.open(i)
    if format == "m4a":
        format = "mp4"
    out = av.open(o, "w", format=format)
    if format == "ogg":
        format = "libvorbis"
    if format == "mp4":
        format = "aac"

    ostream = out.add_stream(format)

    for frame in inp.decode(audio=0):
        for p in ostream.encode(frame):
            out.mux(p)

    for p in ostream.encode(None):
        out.mux(p)

    out.close()
    inp.close()


def audio2(i, o, format, sr):
    inp = av.open(i)
    out = av.open(o, "w", format=format)
    if format == "ogg":
        format = "libvorbis"
    if format == "f32le":
        format = "pcm_f32le"

    ostream = out.add_stream(format, channels=1)
    ostream.sample_rate = sr

    for frame in inp.decode(audio=0):
        for p in ostream.encode(frame):
            out.mux(p)

    out.close()
    inp.close()


def load_audio(file, sr):
    """新 PyAV 的 av.open(path,'rb') 已失效：改用 ffmpeg 解码（RVC_FFMPEG 可由引擎注入）。"""
    import os
    import shutil
    import subprocess
    file = (file.strip(' ').strip('"').strip('\n').strip('"').strip(' '))
    ff = os.environ.get('RVC_FFMPEG') or shutil.which('ffmpeg') or shutil.which('ffmpeg.exe') or 'ffmpeg'
    cmd = [ff, '-v', 'quiet', '-i', file, '-f', 'f32le', '-ac', '1', '-ar', str(sr), '-']
    out = subprocess.run(cmd, capture_output=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError('ffmpeg decode failed (' + str(ff) + '): ' + out.stderr.decode('utf-8', 'ignore')[:200])
    return np.frombuffer(out.stdout, np.float32).flatten()
