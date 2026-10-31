# -*- coding: utf-8 -*-
"""换声前检查人声 stem：是否有乐器残留/串音。用法:
python engine/tools/check_vocal.py --vocal X.wav --inst Y.wav
"""
import argparse, sys
sys.path.insert(0, "D:/VoiceData/house/plugins/dsh-cover-factory/engine")
import numpy as np, librosa
from assets import audio, analysis

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--vocal",required=True); ap.add_argument("--inst",required=True)
    ap.add_argument("--ffmpeg",default=r"D:/VoiceData/tools/bin/ffmpeg.exe")
    a=ap.parse_args()
    ff=a.ffmpeg
    v=audio.decode(ff,a.vocal,sr=22050,channels=1)
    i=audio.decode(ff,a.inst,sr=22050,channels=1)
    n=min(len(v),len(i)); v=v[:n]; i=i[:n]
    corr=float(np.corrcoef(v,i)[0,1])
    H,P=librosa.effects.hpss(v)
    perc=float((P*P).sum()/((H*H).sum()+(P*P).sum()+1e-9))
    f0=analysis.f0_track(v,22050); s=analysis.f0_summary(f0)
    voiced=s["voiced_frames"]/max(s["frames"],1)
    print("vocal perc=%.3f corr=%.4f voiced=%.3f" % (perc,corr,voiced))
    bad=[]
    if perc>0.30: bad.append("人声轨打击比例过高（可能混入鼓/伴奏）")
    if corr>0.25: bad.append("人声与伴奏相关过高（串音/分离失败）")
    if voiced<0.12: bad.append("几乎测不到人声（空轨或纯乐器）")
    if bad:
        print("WARN: " + "；".join(bad)); print("建议改用更干净的 stem（如 BS-RoFormer ep317 分离）后再换声")
        sys.exit(1)
    print("OK: 可以用于换声")

if __name__=="__main__": main()
