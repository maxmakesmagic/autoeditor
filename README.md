# autoeditor

This script automatically cuts out portions of dead video from videos. I used it to post-parse OBS recordings of Twitch streams so that I could upload them to YouTube.

## How does it work?

It uses `ffmpeg` to query the input video and generates a complex filter to crossfade between video sections, ensuring that no silence is longer than 3 seconds.

## How do I use it?

Ensure `ffmpeg` is in your path, and then

`python3 stage1_silencecutterfade.py --input <input directory> --output <output directory>`

You can also pass the `--audio-stream-id` parameter if you use multi-track recordings.

## How does it work - more details?

First off, the script uses `ffmpeg` to generate a "silences" file. It gets the output from `ffmpeg -af silencedetect=noise=-30dB:d=# -f null` where # is the duration in seconds, to detect all the places in the video where the audio is quieter than -30db for that amount of time or more. This command generates output like:

```
[silencedetect @ 0000021a5e45c540] silence_start: 11.0451

[silencedetect @ 0000021a5e45c540] silence_end: 16.756 | silence_duration: 5.71088
```

This output is parsed to get a set of silence starts, ends, and durations.

Next, the set of silences is iterated over to work out where to cross-fade. Because ffmpeg doesn't support sensible cross-fading, a "complex filter script" is created to do the crossfading. Example:

```
# Pre-fade
[0:v:0]trim=start=0:end=12.7951,setpts=PTS-STARTPTS[v1];
[0:a:0]atrim=start=0:end=12.7951,asetpts=PTS-STARTPTS[a1];

# 0.5 second crossfade section from pre-cut
[0:v:0]trim=start=12.7951:end=13.2951,setpts=PTS-STARTPTS[v2];
[0:a:0]atrim=start=12.7951:end=13.2951,asetpts=PTS-STARTPTS[a2];

# 0.5 second crossfade section from post-cut
[0:v:0]trim=start=16.006:end=16.506,setpts=PTS-STARTPTS[v3];
[0:a:0]atrim=start=16.006:end=16.506,asetpts=PTS-STARTPTS[a3];

# 0.5 second fade to alpha from pre-cut
[v2]format=pix_fmts=yuva420p,fade=t=out:st=0:d=0.5:alpha=1[z4];
[z4]fifo[y4];

# 0.5 second fade from alpha from post-cut
[v3]format=pix_fmts=yuva420p,fade=t=in:st=0:d=0.5:alpha=1[b4];
[b4]fifo[c4];

# Overlaying video alphas so we get a continuous video 
[y4][c4]overlay[v4];

# Crossfading video audio
[a2][a3]acrossfade=d=0.5[a4];

# The rest of the video
[0:v:0]trim=start=16.506:end=32.016,setpts=PTS-STARTPTS[v5];
[0:a:0]atrim=start=16.506:end=32.016,asetpts=PTS-STARTPTS[a5];

# Concatenating all the video segments together.
v1][a1][v4][a4][v5][a5]concat=n=3:v=1:a=1[v6][a6]
```
ffmpeg crunches away on this and spits out a video at the end of it.


### Does it work properly?

Well, looking over the previous timestamps I think I'm a little out, so TODO? It looks like I'm actually generating silences of 1.75s + 0.5s + 0.25s == 2.5s rather than 3s. Oh well :D

Also if your video is very long or has a lot of silences then ffmpeg will probably run out of memory and crash. 