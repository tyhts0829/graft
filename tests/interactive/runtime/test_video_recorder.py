from pathlib import Path

from grafix.interactive.runtime.video_recorder import _ffmpeg_command, default_video_output_path


def test_default_video_output_path_uses_data_dir_and_script_stem():
    def draw(t: float) -> None:
        return None

    path = default_video_output_path(draw)
    assert path.parts[0] == "data"
    assert path.parts[1] == "output"
    assert path.parts[2] == "video"
    assert path.name == f"{Path(__file__).stem}.mp4"
    assert path.suffix == ".mp4"


def test_ffmpeg_command_contains_expected_rawvideo_args():
    cmd = _ffmpeg_command(output_path=Path("out.mp4"), size=(320, 240), fps=60.0)

    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd
    assert "rawvideo" in cmd
    assert "-pix_fmt" in cmd
    assert "rgb24" in cmd
    assert "-video_size" in cmd
    assert "320x240" in cmd
    assert "-framerate" in cmd
    assert "60.0" in cmd
    assert "-vf" in cmd
    assert "vflip" in cmd
    assert cmd[-1] == "out.mp4"
