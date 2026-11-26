"""Preserve a local video's probe, timed frames, contact sheet and optional audio.

Reads local media only. No uploads, models, account credentials or platform calls.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


def frame_times(duration,interval):
    if not math.isfinite(duration) or not math.isfinite(interval) or duration<=0 or interval<=0:
        raise ValueError('Duration and interval must be positive')
    # shortcut: keep a bounded frame sheet; segment long videos before denser analysis.
    if duration/interval>240:
        raise ValueError('More than 240 scheduled frames; segment the clip or increase the interval')
    times={0.0,max(0.0,round(duration-0.10,3))}
    times.update(float(t) for t in [1,2,3] if t<duration)
    n=0
    while n*interval<duration:
        times.add(round(n*interval,3));n+=1
    return sorted(times)


def prepare(video,output_dir,interval=3):
    import imageio_ffmpeg
    from PIL import Image,ImageDraw,ImageFont,ImageOps
    if not video.is_file():raise FileNotFoundError(video)
    output_dir.mkdir(parents=True,exist_ok=True)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(video)],capture_output=True,text=True,encoding='utf-8',errors='replace')
    (output_dir/'probe.txt').write_text(probe.stderr,encoding='utf-8',newline='\n')
    video_streams=[line for line in probe.stderr.splitlines() if 'Video:' in line and 'attached pic' not in line.lower()]
    if not video_streams:
        raise ValueError('No video track: audio-only downloads are not video evidence')
    match=re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)',probe.stderr)
    if not match:raise ValueError('No valid media duration returned by FFmpeg')
    h,m,s=map(float,match.groups());duration=h*3600+m*60+s
    rows=[]
    for i,t in enumerate(frame_times(duration,interval)):
        file=output_dir/f'frame-{i:03d}-{t:07.3f}s.jpg'
        command=[ffmpeg,'-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=540:-2',str(file)]
        subprocess.run(command,check=True,capture_output=True)
        if not file.is_file() or file.stat().st_size==0:raise ValueError('Missing frame at '+str(t))
        rows.append({'index':i,'time_seconds':t,'local_path':str(file.resolve()),'status':'ok',
                     'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    has_audio='Audio:' in probe.stderr
    audio=output_dir/'audio-16k.wav'
    if has_audio:
        subprocess.run([ffmpeg,'-v','error','-y','-i',str(video),'-vn','-ac','1','-ar','16000',str(audio)],check=True,capture_output=True)
    width=5*256;height=((len(rows)+4)//5)*360
    sheet=Image.new('RGB',(width,height),'#e9edf2');draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
    for i,row in enumerate(rows):
        with Image.open(row['local_path']) as im:thumb=ImageOps.contain(im.convert('RGB'),(246,322))
        x=(i%5)*256+5;y=(i//5)*360+30
        sheet.paste(thumb,(x,y));draw.text((x,y-26),f"{row['time_seconds']:.3f}s",font=font,fill='#182c3b')
    sheet.save(output_dir/'contact-sheet.png')
    result={'source_file':str(video.resolve()),'source_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),
            'prepared_at_utc':datetime.now(timezone.utc).isoformat(),'duration_seconds':duration,
            'frame_interval_seconds':interval,'frames':rows,'has_audio_track':has_audio,
            'audio_file':str(audio.resolve()) if has_audio else None,'audio_transcription_status':'not_transcribed',
            'ffmpeg_executable':ffmpeg,'evidence_limit':'Sparse frames prove sampled moments; no claim of complete motion/audio interpretation'}
    (output_dir/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--interval',type=float,default=3)
    args=parser.parse_args()
    result=prepare(args.video,args.output_dir,args.interval)
    print(json.dumps({'duration_seconds':result['duration_seconds'],'frames':len(result['frames']),
                      'has_audio':result['has_audio_track'],'output':str(args.output_dir)},ensure_ascii=False))


if __name__=='__main__':main()
