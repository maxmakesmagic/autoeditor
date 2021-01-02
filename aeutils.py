#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autoeditor utilities"""

import ffmpy
import json
import logging
import subprocess

log = logging.getLogger(__name__)


def get_duration(video_file: str) -> float:
    # Uses ffprobe to query a file for its duration.

    (stdout, _stderr) = ffmpy.FFprobe(
        inputs={video_file: None},
        global_options=[
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams']
    ).run(stdout=subprocess.PIPE)
    meta = json.loads(stdout.decode('utf-8'))

    duration = float(meta["format"]["duration"])
    log.debug("Duration of %s: %f", video_file, duration)
    return duration
