#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detects silences in videos and generates """

from __future__ import (absolute_import, division, print_function, unicode_literals)
import ffmpy
import io
import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)


def silencedetector(input_file):
    """Get a list of areas which need trimming away."""

    silence_video = SilenceDetectedVideo(input_file)
    silences = silence_video.silences()
    log.debug("Silences: %s", silences)

    counters = dict((x, 0) for x in range(30))

    for silence in silences:
        for i in range(30):
            if i <= silence["duration"] < i + 1:
                counters[i] += 1
                break

    for i in range(30):
        log.info("Silences length %d-%d = %d", i, i+1, counters[i])


class SilenceDetectedVideo(object):

    SILENCE_START_RE = re.compile("\[silencedetect @ [0-9a-f]+\] silence_start: ([0-9\.]+)")
    SILENCE_END_RE = re.compile("\[silencedetect @ [0-9a-f]+\] silence_end: ([0-9\.]+) \| silence_duration: ([0-9\.]+)")

    def __init__(self, video_file, silence_duration=3):
        self.video_file = video_file
        self.silence_cache = "{}.sc{}".format(video_file, silence_duration)
        self.silence_duration = silence_duration

    def get_ffmpeg_silence_output(self):
        """Use ffmpeg to get silent spots in video"""

        if os.path.exists(self.silence_cache):
            with io.open(self.silence_cache, mode="rt", encoding="utf-8") as f:
                # Returns string contents from f.
                return f.read()

        # Use ffmpeg to generate the silence data.
        ff = ffmpy.FFmpeg(
            inputs={self.video_file: None},
            outputs={"-": "-af silencedetect=noise=-30dB:d={} -f null".format(self.silence_duration)}
        )

        log.info("FFmpeg command line: %s", ff.cmd)
        (_stdout, stderr_bytes) = ff.run(stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Convert the bytes of stderr to a utf-8 string.
        stderr = stderr_bytes.decode("utf-8")

        # If we got here then ffmpeg finished. Dump the stderr to the cache.
        with io.open(self.silence_cache, mode="wt", encoding="utf-8") as f:
            f.write(stderr)

        # Finally return it to the caller.
        return stderr

    def silences(self):
        """Parse the ffmpeg output for lines of the correct format and write them to file"""
        output = self.get_ffmpeg_silence_output()

        # We expect alternating starts and finishes.
        expecting_start = True
        current_silence = None
        silence_list = []

        for line in output.splitlines():
            if expecting_start:
                m = self.SILENCE_START_RE.match(line)
                if m:
                    current_silence = Silence(float(m.group(1)))
                    expecting_start = False

            else:
                m = self.SILENCE_END_RE.match(line)
                if m:
                    # Update the current silence with the end time and check the duration.
                    current_silence.update_end(float(m.group(1)), float(m.group(2)))
                    silence_list.append(current_silence)
                    current_silence = None
                    expecting_start = True

        return silence_list


class Silence(dict):
    def __init__(self, start):
        super(Silence, self).__init__()
        self["start"] = start

    def update_end(self, end, duration):
        self["end"] = end
        self["duration"] = duration

        # Ensure that the duration is close enough to the claimed duration.
        calc_duration = self["end"] - self["start"]
        duration_error = abs(calc_duration - duration)

        if duration_error > 0.1:
            raise Exception("Duration is off by more than 0.1 seconds: {0} vs {1}".format(calc_duration, duration))

    def keyframe_ts(self, offset):
        # Generate the timestamps for keyframes based on the offset.
        return [str(self["start"] + offset), str(self["end"] - offset)]
