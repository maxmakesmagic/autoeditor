#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generates a file which has removed all the dead sections"""

from __future__ import (absolute_import, division, print_function, unicode_literals)
import argparse
import collections
import ffmpy
import io
import logging
import os
import pprint
import silencedetector
import subprocess
import sys
import aeutils

log = logging.getLogger(__name__)

MAX_SILENCE_LENGTH = 3
START_SILENCE_LENGTH = (MAX_SILENCE_LENGTH * 3) / 4
END_SILENCE_LENGTH = MAX_SILENCE_LENGTH - START_SILENCE_LENGTH
FADE_LENGTH = 0.5


def silencecutterfade(video_file, args):
    """Cut the non-silent parts of a video out"""

    # Get durations for the input video.
    video_duration = aeutils.get_duration(video_file)

    silence_video = silencedetector.SilenceDetectedVideo(video_file, silence_duration=MAX_SILENCE_LENGTH)
    silences = silence_video.silences()

    # Generate a complex filter for parsing the video.
    #
    # Start by assigning the correct stream IDs.
    ss = StartStream()
    combined_stream = InputStream(ss, stream_def="main", audio_stream_id=args.audio_stream_id)

    # Create a list to store all streams.
    streams = []

    # The main video starts at ts 0
    start_ts = 0
    fade_out = None

    for (index, silence) in enumerate(silences):
        end_ts = silence["start"] + START_SILENCE_LENGTH

        # Crossfade from previous fade-out clip, then add the main clip (before the next fade-out)
        fade_out = do_crossfade_main(combined_stream, start_ts, end_ts, fade_out, streams)

        start_ts = silence["end"] - END_SILENCE_LENGTH

    # Get the last section of video and crossfade it in. Set the last fadeout for the main video.
    #
    # Need to calculate the outro duration explicitly.
    end_ts = video_duration

    fade_out = do_crossfade_main(combined_stream, start_ts, end_ts, fade_out, streams, do_fade_out=False)
    assert(fade_out is None)

    log.debug("Streams: %s", pprint.pformat(streams))

    # Concatenate all those videos.
    c = Concat(*streams)

    # Generate the output filter settings for the video.
    output_settings = generate_filter_complex(video_file, c)

    # Add youtube settings.
    output_settings.append("-c:v libx264 -preset superfast -crf 18 -c:a aac -pix_fmt yuv420p")

    # Generate a new edited video.
    basefile = os.path.basename(video_file)
    basefile_noext, _extension = os.path.splitext(basefile)

    output_video = os.path.join(args.output,
                                "{0}_silenced{1}".format(basefile_noext, ".mp4"))

    if os.path.exists(output_video):
        os.unlink(output_video)

    inputs = collections.OrderedDict()
    inputs[video_file] = None

    ff = ffmpy.FFmpeg(
        inputs=inputs,
        outputs={output_video: " ".join(output_settings)}
    )

    log.info("Command (len %d): %s", len(ff.cmd), ff.cmd)
    (stdout, stderr) = ff.run(stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    with io.open("{0}.log".format(output_video), mode="wb") as f:
        f.write(b"Stdout:\n")
        f.write(stdout)
        f.write(b"Stderr:\n")
        f.write(stderr)
    log.info("Output: %s %s", stdout.decode("utf-8"), stderr.decode("utf-8"))

    # Rename the input file.
    os.rename(video_file, "{0}.done".format(video_file))


def do_crossfade_main(stream, start_ts, end_ts, fade_out, streams, do_fade_out=True):
    if fade_out is not None:
        # Work out the duration of the fade out.
        fade_out_duration = fade_out.kwargs["end"] - fade_out.kwargs["start"]
        log.debug("Fade out section duration: %f", fade_out_duration)

        # We always have a clip to fade out of and into
        fade_in = PtsTrim(stream, start=start_ts, end=(start_ts + fade_out_duration))
        log.debug("Fading out: %s; fading in: %s", fade_out, fade_in)

        # Combine the fade in and fade out, and add it to the trims.
        cross_fade = Crossfade(fade_out, fade_in)
        streams.append(cross_fade)
    else:
        # No fade out available.
        fade_out_duration = 0

    # The main clip consists of everything after the fade-in section until the fade-out section starts.
    #
    # Explicitly work out if it's going to fit.
    if start_ts + fade_out_duration >= end_ts - FADE_LENGTH:
        new_fade_out = end_ts - (start_ts + fade_out_duration)
        log.warning("Setting fade out duration to %f", new_fade_out)
    else:
        new_fade_out = FADE_LENGTH

    if not do_fade_out:
        new_fade_out = 0

    main_clip = PtsTrim(stream, start=(start_ts + fade_out_duration), end=(end_ts - new_fade_out))

    if main_clip.duration() > 0:
        log.debug("Main clip: %s", main_clip)
        streams.append(main_clip)

    if do_fade_out:
        # Set the fade out.
        fade_out = PtsTrim(stream, start=(end_ts - new_fade_out), end=end_ts)
        log.debug("Setting fade out: %s", fade_out)
    else:
        fade_out = None
    return fade_out


def generate_filter_complex(video_file, stream):
    complex_filter_script = "{0}.cfs".format(video_file)
    filter_list = []
    stream.filters(filter_list)

    with io.open(complex_filter_script, mode="wt", encoding="utf-8") as f:
        filter = ";".join(filter_list)
        f.write(filter)

    cmd = ["-filter_complex_script \"{script}\"".format(script=complex_filter_script),
           "-map \"{video_id}\"".format(video_id=stream.video_id),
           "-map \"{audio_id}\"".format(audio_id=stream.audio_id)]
    return cmd


class BaseStream(object):
    def __init__(self, stream_id):
        self.stream_id = stream_id
        self.video_id = "[v{0}]".format(self.stream_id)
        self.audio_id = "[a{0}]".format(self.stream_id)

    def next_id(self):
        raise NotImplementedError


class StartStream(BaseStream):
    def __init__(self):
        stream_id = -1
        super(StartStream, self).__init__(stream_id)
        self.next = self.stream_id

    def next_id(self):
        self.next += 1
        return self.next

    def filters(self, filter_list):
        pass

    def __repr__(self):
        return "{self.__class__.__name__}({self.stream_id})".format(self=self)


class InputStream(BaseStream):
    def __init__(self, src, stream_def=None, audio_stream_id=0):
        self.src = src
        self.stream_id = src.next_id()
        self.stream_def = stream_def if stream_def else str(self.stream_id)
        super(InputStream, self).__init__(self.stream_id)
        self.video_id = "[{0}:v:0]".format(self.stream_id)
        self.audio_id = "[{0}:a:{1}]".format(self.stream_id, audio_stream_id)

    def next_id(self):
        return self.src.next_id()

    def filters(self, filter_list):
        self.src.filters(filter_list)

    def __repr__(self):
        return ("<{self.stream_def}>".format(self=self))


class SubStream(BaseStream):
    def __init__(self, src):
        self.src = src
        self.stream_id = src.next_id()
        super(SubStream, self).__init__(self.stream_id)

    def next_id(self):
        return self.src.next_id()

    def filters(self, filter_list):
        self.src.filters(filter_list)


class PtsTrim(SubStream):
    def __init__(self, src, **kwargs):
        super(PtsTrim, self).__init__(src)
        self.kwargs = kwargs

    def filters(self, filter_list):
        # First allow the source to provide any filters.
        self.src.filters(filter_list)

        parms = ":".join("{0}={1}".format(k, v) for k, v in self.kwargs.items())

        video_trim = ("{self.src.video_id}trim={parms},setpts=PTS-STARTPTS{self.video_id}"
                      .format(self=self, parms=parms))
        audio_trim = ("{self.src.audio_id}atrim={parms},asetpts=PTS-STARTPTS{self.audio_id}"
                      .format(self=self, parms=parms))

        filter_list.extend([video_trim, audio_trim])

    def __repr__(self):
        return ("{self.__class__.__name__}({self.src!r}, {self.kwargs!r}): {duration}".format(self=self,
                                                                                              duration=self.duration()))

    def duration(self):
        return self.kwargs["end"] - self.kwargs["start"]


class Concat(BaseStream):
    def __init__(self, *streams, **kwargs):
        self.streams = streams
        self.kwargs = kwargs

        self.stream_id = self.streams[0].next_id()
        super(Concat, self).__init__(self.stream_id)

    def filters(self, filter_list):
        concat_ids = []
        for stream in self.streams:
            stream.filters(filter_list)
            concat_ids.extend([stream.video_id, stream.audio_id])

        concat_filter = ("{ids}concat=n={num_streams}:v=1:a=1{self.video_id}{self.audio_id}"
                        .format(ids="".join(concat_ids), num_streams=len(self.streams), self=self))
        filter_list.append(concat_filter)


class Crossfade(BaseStream):
    def __init__(self, fade_out, fade_in):
        assert(fade_out.duration() == fade_in.duration())

        self.fade_out = fade_out
        self.fade_in = fade_in
        self.stream_id = self.fade_out.next_id()
        super(Crossfade, self).__init__(self.stream_id)

    def filters(self, filter_list):
        self.fade_out.filters(filter_list)
        self.fade_in.filters(filter_list)

        # Now generate the instructions to crossfade these.
        fadeout_alpha = ("{self.fade_out.video_id}format=pix_fmts=yuva420p,fade=t=out:st=0:d={duration}:alpha=1"
                         "[z{self.stream_id}]"
                         .format(self=self, duration=FADE_LENGTH))
        fadeout_fifo = ("[z{self.stream_id}]fifo[y{self.stream_id}]"
                        .format(self=self))
        fadein_alpha = ("{self.fade_in.video_id}format=pix_fmts=yuva420p,fade=t=in:st=0:d={duration}:alpha=1"
                        "[b{self.stream_id}]"
                        .format(self=self, duration=FADE_LENGTH))
        fadein_fifo = ("[b{self.stream_id}]fifo[c{self.stream_id}]"
                       .format(self=self))
        overlay = ("[y{self.stream_id}][c{self.stream_id}]overlay{self.video_id}"
                   .format(self=self))

        audio_fade = ("{self.fade_out.audio_id}{self.fade_in.audio_id}acrossfade=d={duration}{self.audio_id}"
                      .format(self=self, duration=FADE_LENGTH))

        filter_list.extend([
            fadeout_alpha,
            fadeout_fifo,
            fadein_alpha,
            fadein_fifo,
            overlay,
            audio_fade
        ])

    def __repr__(self):
        return ("{self.__class__.__name__}({self.fade_out} => {self.fade_in})".format(self=self))


def main():
    """Main handling function. Wraps silencecutterfade."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input directory containing uncut files")
    parser.add_argument("--output", required=True, help="Output directory containing cut files")
    parser.add_argument("--audio-stream-id", help="The nth ffmpeg audio stream ID - choose a mic track!", default=0)
    args = parser.parse_args()

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5.5s %(message)s"))
    stream_handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler("{0}.log".format(__file__), mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5.5s %(message)s"))
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.DEBUG)

    # Run main script.
    videos = []

    for root, dirs, files in os.walk(args.input):
        for filename in files:
            if filename.endswith(".mp4") or filename.endswith(".mkv"):
                videos.append(os.path.join(root, filename))

    success = 0

    for (index, video) in enumerate(videos):
        try:
            log.info("Processing video %d of %d: %s", index + 1, len(videos), video)
            silencecutterfade(video, args)
            success += 1
        except Exception:
            log.exception("Processing failed!! Continue?")
            # break

    log.info("%d of %d conversions succeeded", success, len(videos))


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except Exception as e:
        log.exception(e)
        sys.exit(1)
