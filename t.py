import imgui
import pyglet

# 新しめの pyimgui: create_renderer 推奨（PygletRenderer は deprecated）
try:
    from imgui.integrations.pyglet import create_renderer

    def make_renderer(window):
        return create_renderer(window)  # 固定/プログラマブルを適宜選ぶ

except Exception:
    from imgui.integrations.pyglet import PygletRenderer

    def make_renderer(window):
        return PygletRenderer(window)


def main():
    window = pyglet.window.Window(
        1280, 720, caption="pyimgui + pyglet demo", resizable=True
    )

    imgui.create_context()
    impl = make_renderer(window)

    @window.event
    def on_draw():
        window.clear()

        # 念のため毎フレーム更新（バックエンドが面倒を見てくれる場合もある）
        imgui.get_io().display_size = window.get_size()

        imgui.new_frame()
        imgui.show_demo_window()
        imgui.render()

        impl.render(imgui.get_draw_data())

    try:
        pyglet.app.run()
    finally:
        impl.shutdown()


if __name__ == "__main__":
    main()
