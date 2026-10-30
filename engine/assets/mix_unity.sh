#!/bin/bash
# 翻唱工厂 · 混音链（移植自 <DATA_DIR>/out/tuning/mix_unity.sh）
#
# 铁律：人声/伴奏都用 stem 原始电平（0dB/0dB），不拉平。
# 用法: mix_unity.sh <vocal.wav> <instrumental.wav> <out.wav> [ir.wav] [target_lufs] [limiter_db]
# 环境变量: FFMPEG 覆盖 ffmpeg 路径
#
# 与 stages/mix.py 的 filter graph 等价；Python 路径是引擎实际走的路，这个脚本留作手工复现/对照。

set -euo pipefail

V="$1"
I="$2"
OUT="$3"
IR="${4:-/d/VoiceData/out/tuning/room_ir.wav}"
TARGET_LUFS="${5:--14}"
LIMITER_DB="${6:--1.5}"
FF="${FFMPEG:-C:/Users/yourname/.dsh/bili-humanism/venv-frames/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe}"

TMPDIR="$(dirname "$OUT")/_tmp"
mkdir -p "$TMPDIR"
TMP="$TMPDIR/premix.wav"

VOCAL_CHAIN="[0:a]aresample=44100,highpass=f=45,equalizer=f=260:width_type=q:width=1.4:g=-1.2,equalizer=f=9000:width_type=q:width=1.0:g=1.6,acompressor=threshold=-18dB:ratio=2:attack=10:release=120"
INST_CHAIN="[1:a]aresample=44100,equalizer=f=2800:width_type=q:width=1.2:g=-1.0,extrastereo=m=1.15"

if [ -f "$IR" ]; then
  FILTER="${VOCAL_CHAIN},asplit=2[v1][vs];[vs][2:a]afir=dry=0:wet=1[w0];[w0]extrastereo=m=1.8,volume=0.30[w];[v1][w]amix=inputs=2:normalize=0[v];${INST_CHAIN}[i];[v][i]amix=inputs=2:duration=longest:normalize=0,acompressor=threshold=-15dB:ratio=1.3:attack=20:release=250,aformat=channel_layouts=stereo[out]"
  "$FF" -y -hide_banner -loglevel error -i "$V" -i "$I" -i "$IR" -filter_complex "$FILTER" -map "[out]" -ac 2 -c:a pcm_f32le "$TMP"
else
  FILTER="${VOCAL_CHAIN},anull[v];${INST_CHAIN}[i];[v][i]amix=inputs=2:duration=longest:normalize=0,acompressor=threshold=-15dB:ratio=1.3:attack=20:release=250,aformat=channel_layouts=stereo[out]"
  "$FF" -y -hide_banner -loglevel error -i "$V" -i "$I" -filter_complex "$FILTER" -map "[out]" -ac 2 -c:a pcm_f32le "$TMP"
fi

L=$("$FF" -hide_banner -nostats -i "$TMP" -af ebur128 -f null - 2>&1 | sed -n '/Summary:/,$p' | grep -m1 "I:" | tr -s " " | sed "s/.*I: //;s/ LUFS//")
G=$(python -c "print(round($TARGET_LUFS - float('$L') - 1.0, 2))")
"$FF" -y -hide_banner -loglevel error -i "$TMP" -af "volume=${G}dB,alimiter=limit=${LIMITER_DB}dB:level=false" -ac 2 -c:a pcm_s16le "$OUT"
