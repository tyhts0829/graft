import time

import pytest

from grafix.interactive.runtime.frame_clock import RealTimeClock, RecordingClock


def test_recording_clock_advances_by_fixed_fps():
    clock = RecordingClock(t0=1.0, fps=60.0)
    assert clock.fps == 60.0
    assert clock.frame_index == 0
    assert clock.t() == pytest.approx(1.0)

    clock.tick()
    assert clock.frame_index == 1
    assert clock.t() == pytest.approx(1.0 + 1.0 / 60.0)

    for _ in range(59):
        clock.tick()
    assert clock.frame_index == 60
    assert clock.t() == pytest.approx(2.0)


def test_real_time_clock_returns_elapsed_seconds():
    start_time = time.perf_counter() - 1.0
    clock = RealTimeClock(start_time=start_time)
    assert 0.5 < clock.t() < 1.5

