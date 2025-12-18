"""
どこで: `src/grafix/interactive/gl/shader.py`。
何を: 線の太さをジオメトリシェーダで表現する最小頂点/ジオメトリ/フラグメントのセットを提供。
なぜ: 単純なラインを太さ付き四角形に展開し、視認性を高めるため。
"""


class Shader:
    VERTEX_SHADER = """
    #version 410
    uniform mat4 projection;
    in vec3 in_vert;
    void main() {
        gl_Position = projection * vec4(in_vert.xy, 0.0, 1.0);
    }
    """
    GEOMETRY_SHADER = """
        #version 410
        layout(lines) in; // 入力は線
        layout(triangle_strip, max_vertices = 4) out; // 出力は四角形（2つの三角形）
        uniform float line_thickness = 0.0006;
        void main() {
            // 線の太さ
            float thickness = line_thickness; // 太さを調整
            // 線の方向を計算
            vec2 dir = normalize(gl_in[1].gl_Position.xy - gl_in[0].gl_Position.xy);
            // 垂直な方向を計算
            vec2 offset = vec2(-dir.y, dir.x) * thickness / 2.0;
            // 四角形の頂点を出力
            gl_Position = vec4(gl_in[0].gl_Position.xy + offset, 0.0, 1.0);
            EmitVertex();
            gl_Position = vec4(gl_in[0].gl_Position.xy - offset, 0.0, 1.0);
            EmitVertex();
            gl_Position = vec4(gl_in[1].gl_Position.xy + offset, 0.0, 1.0);
            EmitVertex();
            gl_Position = vec4(gl_in[1].gl_Position.xy - offset, 0.0, 1.0);
            EmitVertex();
            EndPrimitive();
        }
    """
    FRAGMENT_SHADER = """
        #version 410
        uniform vec4 color = vec4(0.0, 0.0, 0.0, 1.0);
        out vec4 fragColor;
        void main() {
            fragColor = color;
        }
    """

    @classmethod
    def create_shader(cls, mgl_context):
        line_program = mgl_context.program(
            vertex_shader=Shader.VERTEX_SHADER,
            geometry_shader=Shader.GEOMETRY_SHADER,
            fragment_shader=Shader.FRAGMENT_SHADER,
        )
        return line_program
