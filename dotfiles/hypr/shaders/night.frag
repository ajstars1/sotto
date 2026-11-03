#version 300 es
// Night light: warmer colours (less blue) for the evening. Control Center > Night light.
precision highp float;

in vec2 v_texcoord;
uniform sampler2D tex;
layout(location = 0) out vec4 fragColor;

void main() {
    vec4 c = texture(tex, v_texcoord);
    fragColor = vec4(c.r, c.g * 0.85, c.b * 0.62, c.a);
}
